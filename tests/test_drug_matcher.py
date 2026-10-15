"""
Tests for utils.helper.drug_matcher.

The matcher applies two exact optimisations before scoring: a length-bucket
index and a per-candidate score ceiling. Both prune work without changing
results, so several tests here compare against an exhaustive scan to prove the
pruning never hides a valid match.
"""

import random
import string

import pathlib

import pytest

from src.Levenshtein import levenshtein_ratio
from utils.helper.drug_matcher import DrugMatcher

from .conftest import SAMPLE_DRUG_NAMES


def exhaustive_lookup(query, names, threshold=75):
    """Unoptimised reference: score every name, keep the best above threshold."""
    best_name, best_score = "", 0.0
    for name in names:
        score = levenshtein_ratio(query, name)
        if score > threshold and score > best_score:
            best_score, best_name = score, name
    return best_name


class TestLoading:
    def test_loads_names_from_file(self, drug_matcher):
        assert len(drug_matcher.drug_names) == len(SAMPLE_DRUG_NAMES)
        assert "ibuprofen" in drug_matcher.drug_names

    def test_missing_file_yields_empty_list(self, tmp_path):
        matcher = DrugMatcher(str(tmp_path / "does-not-exist.txt"))
        assert matcher.drug_names == []

    def test_missing_file_lookup_returns_empty(self, tmp_path):
        """The real NDC list is a large file that may not be checked out."""
        matcher = DrugMatcher(str(tmp_path / "does-not-exist.txt"))
        assert matcher.lookup("ibuprofen") == ""
        assert matcher.identify_drugs(["ibuprofen"]) == []

    def test_blank_lines_are_skipped(self, tmp_path):
        path = tmp_path / "ndc.txt"
        path.write_text("ibuprofen\n\n   \naspirin\n", encoding="utf-8")
        assert DrugMatcher(str(path)).drug_names == ["ibuprofen", "aspirin"]

    def test_names_are_cached_after_first_read(self, drug_matcher, ndc_file):
        _ = drug_matcher.drug_names
        ndc_file.write_text("something-else\n", encoding="utf-8")
        assert "ibuprofen" in drug_matcher.drug_names


class TestLookup:
    def test_exact_match(self, drug_matcher):
        assert drug_matcher.lookup("ibuprofen") == "ibuprofen"

    @pytest.mark.parametrize(
        "ocr_text, expected",
        [
            ("ibuprofn", "ibuprofen"),
            ("lbuprofen", "ibuprofen"),
            ("paracetamo1", "paracetamol"),
            ("amoxicilin", "amoxicillin"),
            ("cetirizin", "cetirizine"),
        ],
    )
    def test_matches_through_ocr_noise(self, drug_matcher, ocr_text, expected):
        assert drug_matcher.lookup(ocr_text) == expected

    @pytest.mark.parametrize(
        "ocr_text",
        ["tablets", "400mg", "exp 2027", "store below 25c", "batch no"],
    )
    def test_rejects_packaging_text(self, drug_matcher, ocr_text):
        assert drug_matcher.lookup(ocr_text) == ""

    def test_empty_query_returns_empty(self, drug_matcher):
        assert drug_matcher.lookup("") == ""

    def test_returns_best_of_several_candidates(self, drug_matcher):
        """When more than one name clears the bar, the highest score wins."""
        assert drug_matcher.lookup("aspirin") == "aspirin"

    def test_pruning_agrees_with_exhaustive_scan(self, drug_matcher):
        """The length index and score ceiling must not change any outcome."""
        rng = random.Random(42)
        queries = list(SAMPLE_DRUG_NAMES)
        for name in SAMPLE_DRUG_NAMES:
            for _ in range(6):
                characters = list(name)
                index = rng.randrange(len(characters))
                choice = rng.random()
                if choice < 0.4:
                    characters[index] = rng.choice(string.ascii_lowercase)
                elif choice < 0.7:
                    characters.pop(index)
                else:
                    characters.insert(index, rng.choice(string.ascii_lowercase))
                queries.append("".join(characters))
        queries += ["".join(rng.choice(string.ascii_lowercase)
                            for _ in range(rng.randint(3, 18))) for _ in range(60)]

        for query in queries:
            assert drug_matcher.lookup(query) == exhaustive_lookup(
                query, SAMPLE_DRUG_NAMES
            ), f"pruning changed the result for {query!r}"


class TestThreshold:
    def test_threshold_is_exclusive(self, drug_matcher):
        """A candidate must score strictly above the threshold, not equal it."""
        drug_matcher.set_similarity_threshold(100)
        assert drug_matcher.lookup("ibuprofen") == ""

    def test_lower_threshold_accepts_looser_matches(self, drug_matcher):
        assert drug_matcher.lookup("ibup") == ""
        drug_matcher.set_similarity_threshold(40)
        assert drug_matcher.lookup("ibup") == "ibuprofen"

    def test_valid_threshold_is_applied(self, drug_matcher):
        drug_matcher.set_similarity_threshold(70)
        assert drug_matcher.similarity_threshold == 70

    @pytest.mark.parametrize("bad_value", [-1, 101, 500])
    def test_invalid_threshold_is_ignored(self, drug_matcher, bad_value):
        original = drug_matcher.similarity_threshold
        drug_matcher.set_similarity_threshold(bad_value)
        assert drug_matcher.similarity_threshold == original

    @pytest.mark.parametrize("boundary", [0, 100])
    def test_boundary_thresholds_accepted(self, drug_matcher, boundary):
        drug_matcher.set_similarity_threshold(boundary)
        assert drug_matcher.similarity_threshold == boundary


class TestLengthIndex:
    def test_index_groups_names_by_length(self, drug_matcher):
        index = drug_matcher.length_index
        assert "aspirin" in index[len("aspirin")]
        assert sum(len(v) for v in index.values()) == len(SAMPLE_DRUG_NAMES)

    def test_candidates_include_the_true_match(self, drug_matcher):
        candidates = drug_matcher._candidates(len("ibuprofn"))
        assert "ibuprofen" in candidates

    def test_candidates_exclude_hopeless_lengths(self, drug_matcher):
        """A 4-char query cannot reach 75 against a 29-char name."""
        candidates = drug_matcher._candidates(4)
        assert "dextromethorphan hydrobromide" not in candidates

    def test_zero_length_query_has_no_candidates(self, drug_matcher):
        assert drug_matcher._candidates(0) == []


class TestIdentifyDrugs:
    def test_identifies_multiple_drugs(self, drug_matcher):
        found = drug_matcher.identify_drugs(["ibuprofen", "aspirin", "tablets"])
        assert found == ["ibuprofen", "aspirin"]

    def test_deduplicates_by_default(self, drug_matcher):
        """Packaging repeats the drug name; the response should not."""
        found = drug_matcher.identify_drugs(["ibuprofen", "ibuprofen", "ibuprofn"])
        assert found == ["ibuprofen"]

    def test_deduplication_can_be_disabled(self, drug_matcher):
        found = drug_matcher.identify_drugs(
            ["ibuprofen", "ibuprofen"], deduplicate=False
        )
        assert found == ["ibuprofen", "ibuprofen"]

    def test_preserves_order_of_first_appearance(self, drug_matcher):
        found = drug_matcher.identify_drugs(["aspirin", "ibuprofen", "aspirin"])
        assert found == ["aspirin", "ibuprofen"]

    def test_empty_input(self, drug_matcher):
        assert drug_matcher.identify_drugs([]) == []

    def test_no_matches_returns_empty(self, drug_matcher):
        assert drug_matcher.identify_drugs(["tablets", "500mg", "exp"]) == []


class TestScorerSelection:
    def test_default_scorer_is_classic_levenshtein(self, drug_matcher):
        assert drug_matcher.scorer_name == "levenshtein"
        assert drug_matcher.score("kitten", "sitting") == pytest.approx(57.142, abs=1e-3)

    def test_default_threshold_is_the_calibrated_75(self, ndc_file):
        assert DrugMatcher(str(ndc_file)).similarity_threshold == 75

    def test_indel_scorer_can_still_be_selected_for_comparison(self, ndc_file):
        matcher = DrugMatcher(str(ndc_file), scorer="indel")
        assert matcher.score("kitten", "sitting") == pytest.approx(61.538, abs=1e-3)

    def test_indel_scorer_still_matches_exact_names(self, ndc_file):
        matcher = DrugMatcher(str(ndc_file), scorer="indel")
        assert matcher.lookup("ibuprofen") == "ibuprofen"

    def test_unknown_scorer_rejected(self, ndc_file):
        with pytest.raises(ValueError):
            DrugMatcher(str(ndc_file), scorer="soundex")


class TestThresholdRecalibration:
    """
    The scorer swap and the threshold change go together. These pin down the
    OCR failures that motivated moving from 85 to 75.
    """

    @pytest.mark.parametrize(
        "ocr_text, expected",
        [
            ("acetaminphn", "acetaminophen"),   # two characters dropped
            ("amoxicilin", "amoxicillin"),      # doubled letter lost
            ("paracetmol", "paracetamol"),
        ],
    )
    def test_dropped_characters_still_match(self, drug_matcher, ocr_text, expected):
        """These are exactly the reads classic Levenshtein at 85 would lose."""
        assert drug_matcher.lookup(ocr_text) == expected

    def test_old_threshold_would_have_lost_them(self, drug_matcher):
        """Demonstrates the regression that the recalibration avoids."""
        drug_matcher.set_similarity_threshold(85)
        assert drug_matcher.lookup("acetaminphn") == ""

    def test_packaging_text_still_rejected_at_the_lower_threshold(self, drug_matcher):
        for text in ["tablets", "400mg", "store below 25c", "batch no", "exp 2027"]:
            assert drug_matcher.lookup(text) == ""


class TestCharacterCountPrefilter:
    """
    The prefilter rejects candidates using a lower bound on the edit distance
    derived from letter frequencies alone. It must never change a result -- only
    avoid work -- so most of these tests compare against an unfiltered scan.
    """

    def test_counts_are_built_for_every_name(self, drug_matcher):
        counts = drug_matcher.character_counts
        assert set(counts) == set(SAMPLE_DRUG_NAMES)
        assert counts["ibuprofen"]["i"] == 1
        assert counts["ibuprofen"]["u"] == 1

    def test_bound_is_zero_for_identical_strings(self, drug_matcher):
        from collections import Counter

        assert drug_matcher._distance_lower_bound(Counter("ibuprofen"), "ibuprofen") == 0

    def test_bound_for_one_dropped_character(self, drug_matcher):
        """ibuprofn vs ibuprofen differ by one 'e', so at least one edit."""
        from collections import Counter

        assert drug_matcher._distance_lower_bound(Counter("ibuprofn"), "ibuprofen") == 1

    def test_bound_halves_for_levenshtein(self, ndc_file):
        """
        One substitution changes two letter counts, so the Levenshtein bound is
        ceil(D / 2). Indel cannot substitute, so its bound is the full D.
        """
        from collections import Counter

        from utils.helper.drug_matcher import DrugMatcher

        levenshtein = DrugMatcher(str(ndc_file), scorer="levenshtein")
        indel = DrugMatcher(str(ndc_file), scorer="indel")

        # "aspirin" vs "aspirin" with two letters swapped for two others.
        query = Counter("asqirin")  # p -> q, one substitution
        assert levenshtein._distance_lower_bound(query, "aspirin") == 1
        assert indel._distance_lower_bound(query, "aspirin") == 2

    def test_bound_never_exceeds_the_true_distance(self, drug_matcher):
        """
        The whole method rests on this: a bound above the true distance would
        reject a candidate that should have matched.
        """
        from collections import Counter

        from src.Levenshtein import indel_distance, levenshtein_distance

        rng = random.Random(77)
        for _ in range(400):
            query = "".join(rng.choice("abcdefg") for _ in range(rng.randint(0, 12)))
            name = rng.choice(SAMPLE_DRUG_NAMES)
            bound = drug_matcher._distance_lower_bound(Counter(query), name)
            assert bound <= levenshtein_distance(query, name)

    def test_indel_bound_never_exceeds_indel_distance(self, ndc_file):
        from collections import Counter

        from src.Levenshtein import indel_distance
        from utils.helper.drug_matcher import DrugMatcher

        matcher = DrugMatcher(str(ndc_file), scorer="indel")
        rng = random.Random(78)
        for _ in range(400):
            query = "".join(rng.choice("abcdefg") for _ in range(rng.randint(0, 12)))
            name = rng.choice(SAMPLE_DRUG_NAMES)
            bound = matcher._distance_lower_bound(Counter(query), name)
            assert bound <= indel_distance(query, name)

    def test_prefilter_does_not_change_any_result(self, ndc_file):
        """
        Disables gate 2 only, leaving candidate order untouched, and checks the
        answers are identical. This isolates the prefilter from the length
        index, which can pick a different name among equal-scoring ties.
        """
        from utils.helper.drug_matcher import DrugMatcher

        rng = random.Random(79)
        queries = list(SAMPLE_DRUG_NAMES)
        for name in SAMPLE_DRUG_NAMES:
            for _ in range(4):
                characters = list(name)
                index = rng.randrange(len(characters))
                choice = rng.random()
                if choice < 0.4:
                    characters[index] = rng.choice(string.ascii_lowercase)
                elif choice < 0.7:
                    characters.pop(index)
                else:
                    characters.insert(index, rng.choice(string.ascii_lowercase))
                queries.append("".join(characters))
        queries += [
            "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(2, 18)))
            for _ in range(60)
        ]

        for scorer in ("levenshtein", "indel"):
            for threshold in (0, 30, 50, 75, 85, 95):
                enabled = DrugMatcher(str(ndc_file), threshold, scorer=scorer)
                disabled = DrugMatcher(str(ndc_file), threshold, scorer=scorer)
                disabled._distance_lower_bound = lambda counts, name: 0

                for query in queries:
                    assert enabled.lookup(query) == disabled.lookup(query), (
                        f"prefilter changed the result for {query!r} "
                        f"({scorer} @ {threshold})"
                    )

    def test_ocr_style_matches_survive_the_prefilter(self, drug_matcher):
        """The prefilter must not reject the very matches the system exists for."""
        for ocr_text, expected in [
            ("ibuprofn", "ibuprofen"),
            ("acetaminphn", "acetaminophen"),
            ("amoxicilin", "amoxicillin"),
            ("paracetmol", "paracetamol"),
            ("lbuprofen", "ibuprofen"),
        ]:
            assert drug_matcher.lookup(ocr_text) == expected

    def test_counts_are_cached(self, drug_matcher):
        assert drug_matcher.character_counts is drug_matcher.character_counts

    def test_counts_are_not_built_until_needed(self, drug_matcher):
        assert drug_matcher._character_counts is None


class TestNoThirdPartyFuzzyLibrary:
    """The whole point of the exercise: no fuzzy-matching dependency remains."""

    def test_module_does_not_import_a_fuzzy_library(self):
        """
        Inspects real import statements rather than raw text, so the library
        names can still be discussed in comments and docstrings.
        """
        import ast

        import utils.helper.drug_matcher as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        assert "thefuzz" not in imported
        assert "rapidfuzz" not in imported
        assert "Levenshtein" not in imported  # the C-extension package
        assert "src" in imported               # our own implementation

    def test_scoring_comes_from_our_module(self, drug_matcher, monkeypatch):
        """
        If our implementation is bypassed, this test should notice.

        Scored on the Python path specifically. ``lookup()`` now has two: when
        the C kernel is loaded the whole scan, scorer included, runs inside
        ``levenshtein.c`` and never touches ``self._scorer``, so injecting a
        counting scorer there would prove nothing. That path is our code too --
        it is covered by ``test_native_scan_is_also_ours`` below and by the
        differential tests in ``tests/test_levenshtein_native.py``.
        """
        import src.Levenshtein as our_module

        calls = []
        original = our_module.indel_ratio

        def counting_scorer(a, b):
            calls.append((a, b))
            return original(a, b)

        monkeypatch.setattr(drug_matcher, "use_native_index", False)
        monkeypatch.setattr(drug_matcher, "_native_index", None)
        monkeypatch.setattr(drug_matcher, "_native_index_built", True)
        monkeypatch.setattr(drug_matcher, "_scorer", counting_scorer)
        drug_matcher.lookup("ibuprofen")
        assert calls, "DrugMatcher did not use the injected scorer"

    def test_native_scan_is_also_ours(self, drug_matcher):
        """
        The fast path must come from this project as well.

        A third-party accelerator would be an imported module; ours is a shared
        library this repository compiles from a .c file it owns. Asserting the
        artefact sits under src/clevenshtein/ is what distinguishes the two.
        """
        from src.Levenshtein import native_backend

        if not native_backend.AVAILABLE:
            pytest.skip("C kernel not built")

        library = pathlib.Path(native_backend.LIBRARY_PATH)
        assert library.parent.name == "clevenshtein"
        assert (library.parent / "levenshtein.c").exists()

    def test_both_paths_return_the_same_name(self, drug_matcher):
        """
        The reason two paths are tolerable at all: they are interchangeable.

        Asserted here as well as in the native test file, because this is the
        object the application actually calls.
        """
        from src.Levenshtein import native_backend

        if not native_backend.AVAILABLE:
            pytest.skip("C kernel not built")

        native_matcher = DrugMatcher(
            str(drug_matcher.ndc_path),
            drug_matcher.similarity_threshold,
            drug_matcher.scorer_name,
            use_native_index=True,
        )
        python_matcher = DrugMatcher(
            str(drug_matcher.ndc_path),
            drug_matcher.similarity_threshold,
            drug_matcher.scorer_name,
            use_native_index=False,
        )
        for query in ("ibuprofen", "ibuprofn", "acetaminophen",
                      "acetaminphn", "zzzzzz", "a"):
            assert native_matcher.lookup(query) == python_matcher.lookup(query), \
                query
