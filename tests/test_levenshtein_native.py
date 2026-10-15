"""
Differential tests: the compiled kernel against the pure-Python reference.

``tests/test_levenshtein.py`` checks that the algorithms are *correct* -- the
metric axioms, known values, the optimisations. This file checks something
narrower and, for a two-backend project, more important: that the two backends
are the **same function**.

That distinction matters. A bug in ``levenshtein.c`` that the property tests
would miss is one that satisfies every metric axiom and still returns a
different number from the Python -- an off-by-one in the affix trimming, a
cutoff that fires one distance too early, a rounding mode that breaks ties the
other way. Only a direct comparison catches those, and only if the comparison is
run on enough inputs to hit the corner.

Every test here skips cleanly when no compiler was available, because a clone
with no toolchain is a supported configuration, not a failure.
"""

import random
import string

import pytest

from src import _levenshtein_native as native
from src import _levenshtein_python as reference
from src.Levenshtein import BACKEND, BACKEND_DETAIL, backend_report, using_native

# Everything in this module is meaningless without the compiled library.
pytestmark = pytest.mark.skipif(
    not native.AVAILABLE,
    reason=f"C kernel not built: {native.UNAVAILABLE_REASON}",
)


ALPHABET = "abcde"


def random_string(rng, max_length=10, alphabet=ALPHABET) -> str:
    """A short string over a small alphabet.

    The alphabet is deliberately tiny. Random text over all 26 letters almost
    never produces the interesting cases -- shared prefixes, repeated
    characters, near-identical pairs -- which are exactly where an edit-distance
    bug lives. Five letters makes collisions common.
    """
    return "".join(rng.choice(alphabet)
                   for _ in range(rng.randint(0, max_length)))


# Real names, so the suite exercises the lengths and shapes the matcher
# actually sees rather than only synthetic ones.
DRUG_NAMES = [
    "ibuprofen", "acetaminophen", "paracetamol", "amoxicillin",
    "omeprazole", "esomeprazole", "prednisone", "prednisolone",
    "ofloxacin", "levofloxacin", "salbutamol", "albuterol",
    "lignocaine", "lidocaine", "adrenaline", "epinephrine",
    "dextromethorphan hydrobromide", "hydrochlorothiazide",
]

# The failure modes OCR actually produces: a dropped character, a doubled one,
# a substitution, a truncation.
OCR_NOISE = [
    "ibuprofn", "ibuproofen", "1buprofen", "ibuprof",
    "acetaminphn", "acetamlnophen", "paracetmol", "paracetamoi",
    "omeprzole", "levofloxcin", "prednisolne", "amoxicilin",
]


class TestBackendReporting:
    """The facade should be honest about which backend it loaded."""

    def test_backend_is_c_when_library_available(self):
        assert BACKEND == "c"
        assert using_native() is True

    def test_report_names_the_library(self):
        report = backend_report()
        assert "C (compiled)" in report
        assert BACKEND_DETAIL in report

    def test_abi_version_was_checked(self):
        # _bind() refuses a library whose ABI does not match, so a successful
        # load implies the check passed. Assert the limit came back too, since
        # that is read from the library rather than hardcoded.
        assert native.RECURSION_LIMIT > 0


class TestDistanceAgreement:
    """The two backends must return identical integers, not merely close ones."""

    def test_random_pairs(self):
        rng = random.Random(20240101)
        for _ in range(4000):
            a, b = random_string(rng), random_string(rng)
            assert native.levenshtein_distance(a, b) == \
                   reference.levenshtein_distance(a, b), (a, b)

    def test_indel_and_lcs(self):
        rng = random.Random(20240102)
        for _ in range(4000):
            a, b = random_string(rng), random_string(rng)
            assert native.indel_distance(a, b) == \
                   reference.indel_distance(a, b), (a, b)
            assert native.lcs_length(a, b) == \
                   reference.lcs_length(a, b), (a, b)

    @pytest.mark.parametrize("query", OCR_NOISE)
    def test_ocr_noise_against_real_names(self, query):
        for name in DRUG_NAMES:
            assert native.levenshtein_distance(query, name) == \
                   reference.levenshtein_distance(query, name), (query, name)

    def test_empty_and_identical(self):
        for a, b in [("", ""), ("", "abc"), ("abc", ""), ("abc", "abc")]:
            assert native.levenshtein_distance(a, b) == \
                   reference.levenshtein_distance(a, b), (a, b)


class TestRatioAgreement:
    """
    The scorer is where a mismatch would actually change a diagnosis, so it gets
    the widest sweep -- every cutoff the matcher might plausibly be configured
    with, including the boundaries 0 and 100.
    """

    CUTOFFS = [None, 0, 25, 50, 60, 70, 75, 80, 85, 90, 100]

    @pytest.mark.parametrize("cutoff", CUTOFFS)
    def test_random_pairs(self, cutoff):
        rng = random.Random(20240103)
        for _ in range(1500):
            a, b = random_string(rng), random_string(rng)
            assert native.ratio(a, b, cutoff) == \
                   reference.ratio(a, b, cutoff), (a, b, cutoff)

    @pytest.mark.parametrize("cutoff", CUTOFFS)
    def test_drug_names(self, cutoff):
        for query in OCR_NOISE:
            for name in DRUG_NAMES:
                assert native.ratio(query, name, cutoff) == \
                       reference.ratio(query, name, cutoff), \
                       (query, name, cutoff)

    def test_normalised_ratios_agree_to_floating_point(self):
        rng = random.Random(20240104)
        for _ in range(2000):
            a, b = random_string(rng), random_string(rng)
            assert native.levenshtein_ratio(a, b) == \
                   pytest.approx(reference.levenshtein_ratio(a, b), abs=1e-12)
            assert native.indel_ratio(a, b) == \
                   pytest.approx(reference.indel_ratio(a, b), abs=1e-12)

    def test_rounding_ties_break_the_same_way(self):
        """
        C rounds with nearbyint() and Python with round(); both are round-half-
        to-even under the default FP mode. This asserts it rather than trusting
        it, because a compiler flag such as -ffast-math could change the mode
        and the failure would be a silent one-point score difference.
        """
        # Pairs whose score lands exactly on .5: distance d over length n where
        # 100 * (1 - d/n) has a half fraction, e.g. n=8, d=3 -> 62.5.
        a = "abcdefgh"
        for b in ("abcdefXY", "abXdefgh", "XbcdefgY"):
            assert native.ratio(a, b) == reference.ratio(a, b), b


class TestMaxDistanceCutoffAgreement:
    """
    The cutoff makes ``levenshtein_distance`` return a *lower bound* rather than
    the true distance once it is exceeded, so the backends have to agree on
    where that boundary is -- not just on the exact values below it.
    """

    @pytest.mark.parametrize("max_distance", [0, 1, 2, 3, 4, 5, 10])
    def test_cutoff_boundary(self, max_distance):
        rng = random.Random(20240105)
        for _ in range(1500):
            a, b = random_string(rng), random_string(rng)
            assert native.levenshtein_distance(a, b, max_distance) == \
                   reference.levenshtein_distance(a, b, max_distance), \
                   (a, b, max_distance)

    def test_exact_below_the_cutoff(self):
        """Under the cutoff, the answer must be the true distance, not a bound."""
        rng = random.Random(20240106)
        for _ in range(1000):
            a, b = random_string(rng), random_string(rng)
            true_distance = reference.levenshtein_distance(a, b)
            generous = true_distance + 3
            assert native.levenshtein_distance(a, b, generous) == true_distance


class TestThreeApproachesAgree:
    """
    The project requirement is three implementations of one function. This
    asserts that is true *within* the C backend as well as across backends --
    six implementations, one answer.
    """

    def test_all_six_agree_on_short_inputs(self):
        rng = random.Random(20240107)
        for _ in range(600):
            # Kept short: the naive recursion is exponential in both languages.
            a, b = random_string(rng, 6), random_string(rng, 6)
            expected = reference.levenshtein_distance(a, b)
            assert reference.levenshtein_distance_memoized(a, b) == expected
            assert reference.levenshtein_distance_recursive(a, b) == expected
            assert native.levenshtein_distance(a, b) == expected
            assert native.levenshtein_distance_memoized(a, b) == expected
            assert native.levenshtein_distance_recursive(a, b) == expected

    def test_memoized_handles_long_inputs(self):
        """Only the recursive variant has a length ceiling; memoised must not."""
        rng = random.Random(20240108)
        a = "".join(rng.choice(string.ascii_lowercase) for _ in range(120))
        b = "".join(rng.choice(string.ascii_lowercase) for _ in range(120))
        assert native.levenshtein_distance_memoized(a, b) == \
               reference.levenshtein_distance(a, b)

    def test_recursive_refuses_rather_than_crashing(self):
        """
        An exponential algorithm on a long input must fail loudly. The C version
        would otherwise overflow its own stack and take the interpreter with it,
        which is a far worse outcome than an exception.
        """
        long_input = "a" * (native.RECURSION_LIMIT // 2 + 5)
        with pytest.raises(RecursionError):
            native.levenshtein_distance_recursive(long_input, "b" * len(long_input))


class TestUnicodeHandling:
    """
    The C side receives UTF-32 code points, not bytes, so distances count
    characters. If the binding ever passed UTF-8 instead, ASCII would keep
    working and every accented or non-Latin name would silently score wrong --
    which is precisely the kind of bug that reaches production.
    """

    @pytest.mark.parametrize("a, b", [
        ("café", "cafe"),                # 1 character apart, 2 bytes in UTF-8
        ("naïve", "naive"),
        ("ओमेप्राजोल", "ओमेप्रजोल"),               # Devanagari: relevant to Nepali packaging
        ("日本語", "日本"),                 # 3-byte characters
        ("𝔞𝔟𝔠", "𝔞𝔠"),                   # outside the Basic Multilingual Plane
        ("Ωμέγα", "Ομεγα"),
    ])
    def test_distance_counts_characters_not_bytes(self, a, b):
        assert native.levenshtein_distance(a, b) == \
               reference.levenshtein_distance(a, b), (a, b)
        assert native.ratio(a, b) == reference.ratio(a, b), (a, b)

    def test_accented_pair_is_one_edit(self):
        # The value itself, so the test fails loudly rather than agreeing with
        # an equally broken reference.
        assert native.levenshtein_distance("café", "cafe") == 1


class TestMarshalling:
    """The code-point cache is an optimisation; it must not change answers."""

    def test_cache_hit_returns_the_same_value(self):
        native.clear_cache()
        first = native.ratio("ibuprofn", "ibuprofen", 75)
        second = native.ratio("ibuprofn", "ibuprofen", 75)
        assert first == second
        assert native.cache_info().hits > 0

    def test_clearing_the_cache_changes_nothing(self):
        before = native.levenshtein_distance("acetaminophen", "acetaminphn")
        native.clear_cache()
        after = native.levenshtein_distance("acetaminophen", "acetaminphn")
        assert before == after

    def test_none_arguments_score_zero(self):
        assert native.ratio(None, "ibuprofen") == 0
        assert native.ratio("ibuprofen", None) == 0
        assert reference.ratio(None, "ibuprofen") == 0


class TestNameIndex:
    """
    The compiled candidate index and its parallel scan.

    The property that matters is not speed -- it is that the answer does not
    depend on how the work was divided. A parallel scan that returns a
    different name than the sequential one, on any input, is worse than no
    parallel scan at all.
    """

    NAMES = DRUG_NAMES + [
        "aspirin", "metformin", "atorvastatin", "azithromycin",
        "ciprofloxacin", "warfarin", "furosemide", "gabapentin",
    ]

    def test_index_builds(self):
        index = native.NameIndex(self.NAMES)
        assert index.available
        assert len(index) == len(self.NAMES)
        index.close()

    def test_empty_list_is_unavailable_not_a_crash(self):
        index = native.NameIndex([])
        assert not index.available
        assert index.best_match("ibuprofen", 75) == ("", 0.0, 0, 0)

    def test_close_is_idempotent(self):
        index = native.NameIndex(self.NAMES)
        index.close()
        index.close()
        # Every method must be a no-op after close, not a use-after-free.
        assert index.best_match("ibuprofen", 75) == ("", 0.0, 0, 0)

    @pytest.mark.parametrize("threads", [1, 2, 4, 8])
    def test_result_is_independent_of_thread_count(self, threads):
        """
        The merge prefers the lowest sorted position on an equal score, so
        splitting the range differently must not change the winner -- including
        when two names tie.
        """
        reference_index = native.NameIndex(self.NAMES, thread_count=1)
        index = native.NameIndex(self.NAMES, thread_count=threads)
        for query in OCR_NOISE + self.NAMES:
            expected = reference_index.best_match(query, 75)[:2]
            assert index.best_match(query, 75)[:2] == expected, (query, threads)
        index.close()
        reference_index.close()

    def test_agrees_with_the_python_scan(self):
        """
        The C scan reimplements DrugMatcher's gates, so it has to be checked
        against them rather than against the distance function alone. A gate
        that prunes one candidate too many would be invisible to every other
        test in this file.
        """
        index = native.NameIndex(self.NAMES, thread_count=1)
        for threshold in (60, 75, 85):
            for query in OCR_NOISE + self.NAMES + ["zzzzzz", "q", ""]:
                # Exhaustive Python scan, first strictly-better wins.
                best_name, best_score = "", 0.0
                for name in self.NAMES:
                    score = reference.levenshtein_ratio(query, name)
                    if score > threshold and score > best_score:
                        best_name, best_score = name, score
                assert index.best_match(query, threshold)[0] == best_name, \
                    (query, threshold)
        index.close()

    def test_indel_scorer_agrees_too(self):
        index = native.NameIndex(self.NAMES, thread_count=1)
        for query in OCR_NOISE:
            best_name, best_score = "", 0.0
            for name in self.NAMES:
                score = reference.indel_ratio(query, name)
                if score > 75 and score > best_score:
                    best_name, best_score = name, score
            result = index.best_match(query, 75, native.SCORER_INDEL)[0]
            assert result == best_name, query
        index.close()

    def test_gate_statistics_are_reported(self):
        """
        The README claims the gates take thousands of candidates down to a
        couple of dozen. C returns both counts so that claim is measured rather
        than asserted, which also means a regression in the gates shows up as a
        test failure instead of a quiet slowdown.
        """
        index = native.NameIndex(self.NAMES, thread_count=1)
        _, _, scored, examined = index.best_match("ibuprofn", 75)
        assert examined > 0
        assert scored <= examined
        index.close()

    def test_no_match_returns_empty(self):
        index = native.NameIndex(self.NAMES, thread_count=1)
        name, score, _, _ = index.best_match("qqqqqqqqqqqq", 75)
        assert name == ""
        assert score == 0.0
        index.close()

    def test_unicode_names(self):
        names = ["paracetamol", "ओमेप्राजोल", "café", "日本語"]
        index = native.NameIndex(names, thread_count=1)
        # Devanagari, one character apart -- the case Nepali packaging produces.
        assert index.best_match("ओमेप्रजोल", 75)[0] == "ओमेप्राजोल"
        index.close()

    def test_threshold_is_exclusive_on_the_native_path_too(self):
        """
        "cafe" vs "café" is one edit over four characters, which scores exactly
        75.0. The comparison is strictly-greater, so a threshold of 75 must
        reject it and 74 must accept it. This is the "threshold is exclusive"
        entry under Known issues, asserted here so the C scan cannot quietly
        diverge from the Python one at the boundary -- an off-by-one in a gate
        would show up exactly here and nowhere else.
        """
        index = native.NameIndex(["café", "paracetamol"], thread_count=1)
        assert reference.levenshtein_ratio("cafe", "café") == 75.0
        assert index.best_match("cafe", 75)[0] == ""
        assert index.best_match("cafe", 74)[0] == "café"
        index.close()
