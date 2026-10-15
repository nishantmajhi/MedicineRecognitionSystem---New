import logging
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from src.Levenshtein import get_scorer, native_backend, ratio

logger = logging.getLogger("drug_matcher.py")


class DrugMatcher:
    """
    Match OCR text fragments against the NDC generic-name list.

    Scoring uses the project's own edit-distance implementation in
    ``src.Levenshtein`` -- no third-party fuzzy-matching library involved.
    """

    def __init__(
        self,
        ndc_database_path: str = "MRS/database/ndc_generic-names.txt",
        similarity_threshold: int = 75,
        scorer: str = "levenshtein",
        use_native_index: bool = True,
        thread_count: int = 0,
    ):
        """
        Args:
            ndc_database_path: Path to the newline-delimited generic-name list.
            similarity_threshold: Minimum score (0-100) for a candidate to be
                accepted. A candidate must score strictly *above* this value.
                Defaults to 75; see the note below before changing it.
            scorer: ``"levenshtein"`` (default) is the classic edit distance.
                ``"indel"`` is retained only to reproduce the scores the old
                ``thefuzz`` implementation produced, for comparison.
            use_native_index: When True (default) and the C kernel is loaded,
                the whole candidate scan -- gates and all -- runs in C across
                threads. Set False to force the Python loop below, which the
                tests use to prove the two produce identical results.
            thread_count: Threads per lookup for the native path. 0 means match
                the core count, capped at 8. Ignored on the Python path, where
                the GIL makes threading pointless.

        Note on the threshold:
            The previous implementation paired a threshold of 85 with indel
            scoring. Classic Levenshtein charges a substitution 1 instead of 2,
            which makes its normalised scores *lower* wherever characters are
            inserted or dropped, so carrying 85 across would have quietly made
            matching stricter. Measured on simulated OCR noise over a list of
            real generic names, recall would have fallen from 80% to 73%.

            75 was chosen by measurement, not taste: it recovers 95% recall at
            no measured cost in false positives. It also sits on a meaningful
            boundary. Sibling drug names are the real hazard here -- the pairs
            that score closest are all genuinely different medicines::

                omeprazole  / esomeprazole   83.3
                prednisone  / prednisolone   83.3
                ofloxacin   / levofloxacin   75.0

            Because the comparison below is strictly greater-than, a threshold
            of exactly 75 excludes that last pair. Lowering it further starts
            admitting sibling collisions, and naming the wrong medicine is a
            worse failure than naming none.
        """
        self.ndc_path = Path(ndc_database_path)
        self.similarity_threshold = similarity_threshold
        self.scorer_name = scorer
        self._scorer = get_scorer(scorer)
        self.use_native_index = use_native_index
        self.thread_count = thread_count
        self._drug_names_cache: Optional[List[str]] = None
        self._length_index: Optional[Dict[int, List[str]]] = None
        self._character_counts: Optional[Dict[str, Counter]] = None
        self._native_index = None
        self._native_index_built = False

    @property
    def drug_names(self) -> List[str]:
        if self._drug_names_cache is None:
            self._drug_names_cache = self._load_drug_names()
        return self._drug_names_cache

    @property
    def length_index(self) -> Dict[int, List[str]]:
        """Candidate names bucketed by length, built once and reused."""
        if self._length_index is None:
            index: Dict[int, List[str]] = {}
            for name in self.drug_names:
                index.setdefault(len(name), []).append(name)
            self._length_index = index
        return self._length_index

    @property
    def character_counts(self) -> Dict[str, Counter]:
        """
        Letter frequency of every candidate name, built once and reused.

        Feeds the character-count bound in :meth:`lookup`. Built lazily, since a
        matcher that is never queried should not pay for it.
        """
        if self._character_counts is None:
            self._character_counts = {
                name: Counter(name) for name in self.drug_names
            }
        return self._character_counts

    def _distance_lower_bound(self, query_counts: Counter, drug_name: str) -> int:
        """
        A cheap lower bound on the edit distance, from letter frequencies alone.

        Let ``D`` be the total absolute difference between the two letter
        counts, e.g. for ``ibuprofn`` and ``ibuprofen`` only ``e`` differs, so
        ``D = 1``.

        * **Levenshtein**: one edit changes at most two letter counts (a
          substitution removes one letter and adds another), so at least
          ``ceil(D / 2)`` edits are needed.
        * **Indel**: substitution is not available, so each unit of difference
          needs its own insertion or deletion, giving the stronger bound ``D``.

        This is a true lower bound, never an estimate, so rejecting on it can
        only ever discard candidates that could not have matched.

        Comparing two counters costs about 26 operations regardless of length,
        while the dynamic program it avoids costs ``len(query) * len(name)``.
        """
        difference = query_counts.copy()
        difference.subtract(self.character_counts[drug_name])
        total = sum(abs(count) for count in difference.values())

        if self.scorer_name == "levenshtein":
            return (total + 1) // 2  # ceil(total / 2)
        return total

    def _candidates(self, query_length: int) -> List[str]:
        """
        Names whose length permits clearing the threshold.

        The edit distance is at least the length difference, so the best score
        strings of length ``p`` and ``q`` can reach is ``min / max * 100`` under
        Levenshtein, and ``(1 - |p - q| / (p + q)) * 100`` under indel.
        Requiring that to exceed threshold ``T`` bounds the usable lengths to a
        band around ``p`` -- roughly 0.75p to 1.33p at T=75 -- which discards
        most of a full NDC list before any DP runs. Names outside the band
        cannot possibly match, so this is exact, not a heuristic.
        """
        if query_length == 0:
            return []

        index = self.length_index
        if self.similarity_threshold <= 0:
            return self.drug_names

        if self.scorer_name == "levenshtein":
            # min / max > T/100
            ratio_bound = self.similarity_threshold / 100.0
            lowest = int(ratio_bound * query_length)
            highest = int(query_length / ratio_bound) + 1
        else:
            fraction = (100.0 - self.similarity_threshold) / 100.0
            if fraction >= 1.0:
                return self.drug_names
            lowest = int((1.0 - fraction) / (1.0 + fraction) * query_length)
            highest = int((1.0 + fraction) / (1.0 - fraction) * query_length) + 1

        candidates: List[str] = []
        for length in range(max(1, lowest), highest + 1):
            names = index.get(length)
            if names:
                candidates.extend(names)
        return candidates

    def _load_drug_names(self) -> List[str]:
        if not self.ndc_path.exists():
            logger.warning(f"NDC database not found at: {self.ndc_path}")
            return []
        try:
            with open(self.ndc_path, "r", encoding="utf-8", errors="replace") as f:
                return [line.strip() for line in f if line.strip()]
        except Exception as e:
            logger.exception(f"Error loading drug names: {e}")
            return []

    def score(self, query_string: str, drug_name: str) -> float:
        """Similarity between a query and one candidate, from 0 to 100."""
        return self._scorer(query_string, drug_name)

    @property
    def native_index(self):
        """
        The compiled candidate index, or None when the C kernel is unavailable.

        Built lazily and once. A matcher that is never queried should not pay
        for it, and building it eagerly in ``__init__`` would make constructing
        a matcher against a 13,000-name list cost ~10 ms for nothing.
        """
        if not self._native_index_built:
            self._native_index_built = True
            if self.use_native_index and native_backend.AVAILABLE and self.drug_names:
                index = native_backend.NameIndex(
                    self.drug_names, thread_count=self.thread_count)
                self._native_index = index if index.available else None
        return self._native_index

    def lookup(self, query_string: str) -> str:
        """
        Find the closest generic name for a single OCR fragment.

        Two paths produce the same answer. When the C kernel is loaded the
        entire scan -- the length band, both bounds and the dynamic program --
        runs in ``levenshtein.c``, split across threads. Otherwise the Python
        loop below does the same work sequentially.

        The native path is not merely the Python loop with a faster scorer. The
        gates themselves move into C, which is the point: with a 13,000-name
        list the gates run thousands of times per lookup while the scorer runs
        about eighteen, so leaving the gates in Python caps the gain at
        approximately nothing. See the README.

        Returns:
            The best-matching drug name, or ``''`` if nothing clears the
            similarity threshold.
        """
        if not self.drug_names or not query_string:
            return ''

        index = self.native_index
        if index is not None:
            try:
                scorer_id = (native_backend.SCORER_LEVENSHTEIN
                             if self.scorer_name == "levenshtein"
                             else native_backend.SCORER_INDEL)
                best_match, match_ratio, _, _ = index.best_match(
                    query_string, self.similarity_threshold, scorer_id)
                if best_match:
                    logger.info(
                        f"'{query_string}' matched to '{best_match}' "
                        f"({match_ratio:.0f}%)")
                else:
                    logger.info(f"'{query_string}' => Not a drug")
                return best_match
            except Exception as e:
                # Fall through to the Python loop rather than failing the
                # request. A bug in the fast path should cost latency, not a
                # scan result.
                logger.exception(f"Native lookup failed, using Python path: {e}")

        best_match, match_ratio = '', 0.0
        try:
            query_length = len(query_string)
            query_counts = Counter(query_string)

            for drug_name in self._candidates(query_length):
                name_length = len(drug_name)

                # Gate 1: the edit distance is at least the length difference,
                # which caps the achievable score. Anything that cannot beat the
                # current best is skipped before running the DP. The bar rises
                # as match_ratio improves, so later candidates are cheaper.
                if self.scorer_name == "levenshtein":
                    longest = max(query_length, name_length)
                    if not longest:
                        continue
                    ceiling = (min(query_length, name_length) / longest) * 100.0
                    span = longest
                else:
                    total = query_length + name_length
                    if not total:
                        continue
                    ceiling = (1.0 - abs(query_length - name_length) / total) * 100.0
                    span = total

                bar = max(match_ratio, self.similarity_threshold)
                if ceiling <= bar:
                    continue

                # Gate 2: the character-count bound. Far sharper than the length
                # bound and still far cheaper than the DP, so it removes almost
                # everything the first gate let through.
                #
                # The epsilon matters. The bound is exact integer arithmetic
                # while the score is binary floating point, and a candidate
                # sitting exactly on the threshold can score 30.000000000000004
                # rather than 30 -- accepted by the strictly-greater comparison
                # below, but rejected by an exact gate. Nudging the limit lets
                # those borderline cases fall through to the real scorer, which
                # then decides. The gate stays conservative: it only ever skips
                # candidates that provably cannot win.
                limit = span * (1.0 - bar / 100.0) + 1e-9
                if self._distance_lower_bound(query_counts, drug_name) > limit:
                    continue

                similarity = self._scorer(query_string, drug_name)
                if similarity > self.similarity_threshold and similarity > match_ratio:
                    match_ratio, best_match = similarity, drug_name

            if best_match:
                logger.info(f"'{query_string}' matched to '{best_match}' ({match_ratio:.0f}%)")
            else:
                logger.info(f"'{query_string}' => Not a drug")
            return best_match
        except Exception as e:
            logger.exception(f"Exception in lookup: {e}")
            return ''

    def identify_drugs(self, text_list: List[str], deduplicate: bool = True) -> List[str]:
        """
        Resolve a list of OCR fragments to drug names.

        Args:
            text_list: Cleaned OCR strings.
            deduplicate: When True (default), a drug matched by more than one
                fragment is reported once. Packaging routinely prints the same
                name in several places, and without this the API response
                repeats it ("Ibuprofen, Ibuprofen").
        """
        try:
            identified: List[str] = []
            for text in text_list:
                drug_name = self.lookup(text)
                if drug_name and not (deduplicate and drug_name in identified):
                    identified.append(drug_name)
            logger.info(f"Identified {len(identified)} drug(s): {identified}")
            return identified
        except Exception as e:
            logger.exception(f"Exception in identify_drugs: {e}")
            return []

    def set_similarity_threshold(self, threshold: int) -> None:
        if 0 <= threshold <= 100:
            self.similarity_threshold = threshold
            logger.info(f"Similarity threshold set to: {threshold}")
        else:
            logger.warning(f"Invalid threshold {threshold}. Must be 0-100")
