"""
Tests for src.Levenshtein.

The suite is built around properties rather than a handful of golden values,
because an edit distance has strong mathematical guarantees (it is a metric)
that a naive implementation can easily violate. Where an exact number is
asserted it is one that can be verified by hand.
"""

import random
import string

import pytest

from src.Levenshtein import (
    distance_matrix,
    edit_operations,
    get_scorer,
    indel_distance,
    indel_ratio,
    lcs_length,
    levenshtein_distance,
    levenshtein_distance_memoized,
    levenshtein_distance_recursive,
    levenshtein_ratio,
    ratio,
)


def reference_levenshtein(a: str, b: str) -> int:
    """Textbook full-matrix implementation, used as the oracle."""
    grid = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        grid[i][0] = i
    for j in range(len(b) + 1):
        grid[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            grid[i][j] = min(
                grid[i - 1][j] + 1,
                grid[i][j - 1] + 1,
                grid[i - 1][j - 1] + (a[i - 1] != b[j - 1]),
            )
    return grid[-1][-1]


def random_string(rng, alphabet=string.ascii_lowercase[:6], max_length=10) -> str:
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, max_length)))


# ---------------------------------------------------------------------------
# levenshtein_distance -- known values
# ---------------------------------------------------------------------------

class TestLevenshteinKnownValues:
    @pytest.mark.parametrize(
        "a, b, expected",
        [
            ("kitten", "sitting", 3),      # classic textbook example
            ("saturday", "sunday", 3),
            ("flaw", "lawn", 2),
            ("", "", 0),
            ("", "abc", 3),
            ("abc", "", 3),
            ("abc", "abc", 0),
            ("a", "b", 1),
            ("ibuprofen", "ibuprofn", 1),   # single OCR dropout
            ("paracetamol", "paracetamo1", 1),
        ],
    )
    def test_known_distances(self, a, b, expected):
        assert levenshtein_distance(a, b) == expected

    def test_empty_against_nonempty_is_length(self):
        assert levenshtein_distance("", "acetaminophen") == len("acetaminophen")


# ---------------------------------------------------------------------------
# levenshtein_distance -- metric properties
# ---------------------------------------------------------------------------

class TestLevenshteinProperties:
    """A distance function must satisfy the metric axioms."""

    def test_identity_of_indiscernibles(self):
        rng = random.Random(1)
        for _ in range(300):
            s = random_string(rng)
            assert levenshtein_distance(s, s) == 0

    def test_positivity_for_distinct_strings(self):
        rng = random.Random(2)
        for _ in range(300):
            a, b = random_string(rng), random_string(rng)
            if a != b:
                assert levenshtein_distance(a, b) > 0

    def test_symmetry(self):
        rng = random.Random(3)
        for _ in range(500):
            a, b = random_string(rng), random_string(rng)
            assert levenshtein_distance(a, b) == levenshtein_distance(b, a)

    def test_triangle_inequality(self):
        rng = random.Random(4)
        for _ in range(500):
            a, b, c = random_string(rng), random_string(rng), random_string(rng)
            assert levenshtein_distance(a, c) <= (
                levenshtein_distance(a, b) + levenshtein_distance(b, c)
            )

    def test_bounded_by_longer_length(self):
        rng = random.Random(5)
        for _ in range(300):
            a, b = random_string(rng), random_string(rng)
            assert levenshtein_distance(a, b) <= max(len(a), len(b))

    def test_at_least_length_difference(self):
        rng = random.Random(6)
        for _ in range(300):
            a, b = random_string(rng), random_string(rng)
            assert levenshtein_distance(a, b) >= abs(len(a) - len(b))

    def test_matches_reference_implementation(self):
        """The optimised version must agree with the naive matrix everywhere."""
        rng = random.Random(7)
        for _ in range(2000):
            a, b = random_string(rng), random_string(rng)
            assert levenshtein_distance(a, b) == reference_levenshtein(a, b)

    def test_affix_trimming_does_not_change_result(self):
        """Shared prefixes/suffixes are stripped internally; result must hold."""
        rng = random.Random(8)
        for _ in range(300):
            core_a, core_b = random_string(rng), random_string(rng)
            prefix, suffix = random_string(rng), random_string(rng)
            assert levenshtein_distance(
                prefix + core_a + suffix, prefix + core_b + suffix
            ) == reference_levenshtein(core_a, core_b)

    def test_single_edit_costs_one(self):
        """Substitution is a unit cost -- this is what separates it from indel."""
        assert levenshtein_distance("cat", "bat") == 1
        assert levenshtein_distance("cat", "cats") == 1
        assert levenshtein_distance("cats", "cat") == 1


# ---------------------------------------------------------------------------
# max_distance cutoff
# ---------------------------------------------------------------------------

class TestMaxDistanceCutoff:
    def test_exact_when_within_cutoff(self):
        assert levenshtein_distance("kitten", "sitting", max_distance=3) == 3
        assert levenshtein_distance("kitten", "sitting", max_distance=10) == 3

    def test_reports_over_cutoff_when_exceeded(self):
        result = levenshtein_distance("kitten", "sitting", max_distance=2)
        assert result > 2

    def test_cutoff_never_hides_a_close_match(self):
        """Below the cutoff the answer must be exact; above it, clearly above."""
        rng = random.Random(9)
        for _ in range(500):
            a, b = random_string(rng, max_length=8), random_string(rng, max_length=8)
            true_distance = reference_levenshtein(a, b)
            for cutoff in range(0, 6):
                result = levenshtein_distance(a, b, max_distance=cutoff)
                if true_distance <= cutoff:
                    assert result == true_distance
                else:
                    assert result > cutoff

    def test_zero_cutoff_on_equal_strings(self):
        assert levenshtein_distance("aspirin", "aspirin", max_distance=0) == 0

    def test_length_difference_short_circuit(self):
        assert levenshtein_distance("a", "abcdefghij", max_distance=2) > 2


# ---------------------------------------------------------------------------
# LCS and indel distance
# ---------------------------------------------------------------------------

class TestLcsAndIndel:
    @pytest.mark.parametrize(
        "a, b, expected",
        [
            ("kitten", "sitting", 4),   # i, t, t, n
            ("abc", "abc", 3),
            ("abc", "def", 0),
            ("", "abc", 0),
            ("abcdef", "acf", 3),
        ],
    )
    def test_known_lcs(self, a, b, expected):
        assert lcs_length(a, b) == expected

    def test_lcs_symmetry(self):
        rng = random.Random(10)
        for _ in range(300):
            a, b = random_string(rng), random_string(rng)
            assert lcs_length(a, b) == lcs_length(b, a)

    def test_lcs_bounded_by_shorter_string(self):
        rng = random.Random(11)
        for _ in range(300):
            a, b = random_string(rng), random_string(rng)
            assert lcs_length(a, b) <= min(len(a), len(b))

    @pytest.mark.parametrize(
        "a, b, expected",
        [
            ("kitten", "sitting", 5),
            ("abc", "abc", 0),
            ("abc", "", 3),
            ("cat", "bat", 2),   # one substitution == delete + insert
        ],
    )
    def test_known_indel_distances(self, a, b, expected):
        assert indel_distance(a, b) == expected

    def test_indel_identity_from_lcs(self):
        """indel(a, b) == len(a) + len(b) - 2 * LCS(a, b)."""
        rng = random.Random(12)
        for _ in range(500):
            a, b = random_string(rng), random_string(rng)
            assert indel_distance(a, b) == len(a) + len(b) - 2 * lcs_length(a, b)

    def test_indel_is_at_least_levenshtein(self):
        """Forbidding substitution can only make the edit script longer."""
        rng = random.Random(13)
        for _ in range(500):
            a, b = random_string(rng), random_string(rng)
            assert indel_distance(a, b) >= levenshtein_distance(a, b)

    def test_indel_at_most_double_levenshtein(self):
        """Each substitution becomes at most one delete plus one insert."""
        rng = random.Random(14)
        for _ in range(500):
            a, b = random_string(rng), random_string(rng)
            assert indel_distance(a, b) <= 2 * levenshtein_distance(a, b)

    def test_indel_symmetry_and_triangle(self):
        rng = random.Random(15)
        for _ in range(300):
            a, b, c = random_string(rng), random_string(rng), random_string(rng)
            assert indel_distance(a, b) == indel_distance(b, a)
            assert indel_distance(a, c) <= indel_distance(a, b) + indel_distance(b, c)


# ---------------------------------------------------------------------------
# ratio()
# ---------------------------------------------------------------------------

class TestRatio:
    @pytest.mark.parametrize(
        "a, b, expected",
        [
            ("ibuprofen", "ibuprofen", 100),
            ("", "", 100),
            ("abc", "", 0),
            ("", "abc", 0),
            ("kitten", "sitting", 57),
            ("ibuprofen", "ibuprofene", 90),
            ("paracetamol", "acetaminophen", 31),
        ],
    )
    def test_known_scores(self, a, b, expected):
        """Classic Levenshtein similarity, verifiable by hand from the distance."""
        assert ratio(a, b) == expected

    def test_ratio_is_the_levenshtein_metric_not_indel(self):
        """
        The distinguishing case. thefuzz.fuzz.ratio answers 62 here because it
        scores insertions/deletions only; classic Levenshtein answers 57. If
        this ever reads 62 again, a fuzzy library has crept back in.
        """
        assert ratio("kitten", "sitting") == 57
        assert ratio("kitten", "sitting") != 62

    def test_score_is_within_bounds(self):
        rng = random.Random(16)
        for _ in range(500):
            a, b = random_string(rng), random_string(rng)
            assert 0 <= ratio(a, b) <= 100

    def test_symmetry(self):
        rng = random.Random(17)
        for _ in range(500):
            a, b = random_string(rng), random_string(rng)
            assert ratio(a, b) == ratio(b, a)

    def test_identical_strings_always_score_100(self):
        rng = random.Random(18)
        for _ in range(200):
            s = random_string(rng)
            assert ratio(s, s) == 100

    def test_disjoint_alphabets_score_zero(self):
        assert ratio("aaaa", "bbbb") == 0

    def test_none_inputs_are_handled(self):
        assert ratio(None, "aspirin") == 0
        assert ratio("aspirin", None) == 0
        assert ratio(None, None) == 0

    def test_case_sensitivity(self):
        """No case folding happens here; callers lowercase before matching."""
        assert ratio("Ibuprofen", "ibuprofen") < 100

    def test_more_similar_pairs_score_higher(self):
        assert ratio("ibuprofen", "ibuprofen") > ratio("ibuprofen", "ibuprofn")
        assert ratio("ibuprofen", "ibuprofn") > ratio("ibuprofen", "aspirin")


class TestScoreCutoff:
    def test_cutoff_preserves_passing_scores(self):
        rng = random.Random(19)
        for _ in range(500):
            a, b = random_string(rng), random_string(rng)
            full_score = ratio(a, b)
            for cutoff in (25, 50, 85, 95):
                result = ratio(a, b, score_cutoff=cutoff)
                if full_score >= cutoff:
                    assert result == full_score
                else:
                    assert result == 0

    def test_cutoff_rejects_length_mismatch_early(self):
        assert ratio("a", "abcdefghijklmnop", score_cutoff=85) == 0


class TestRatioTracksLevenshtein:
    """``ratio`` must be exactly the rounded ``levenshtein_ratio``."""

    def test_ratio_equals_rounded_levenshtein_ratio(self):
        rng = random.Random(20)
        for _ in range(1000):
            a, b = random_string(rng), random_string(rng)
            assert ratio(a, b) == round(levenshtein_ratio(a, b))


# ---------------------------------------------------------------------------
# Normalised ratios and scorer selection
# ---------------------------------------------------------------------------

class TestRatios:
    def test_levenshtein_ratio_known_values(self):
        assert levenshtein_ratio("abc", "abc") == 100.0
        assert levenshtein_ratio("", "") == 100.0
        assert levenshtein_ratio("aaaa", "bbbb") == 0.0
        assert levenshtein_ratio("kitten", "sitting") == pytest.approx(57.142, abs=1e-3)

    def test_indel_ratio_known_values(self):
        assert indel_ratio("abc", "abc") == 100.0
        assert indel_ratio("", "") == 100.0
        assert indel_ratio("kitten", "sitting") == pytest.approx(61.538, abs=1e-3)

    def test_indel_ratio_never_below_levenshtein_ratio(self):
        """
        Guards the threshold-compatibility claim: swapping to the classic metric
        can only lower scores, so a threshold tuned on indel stays valid or gets
        stricter -- never accidentally looser.
        """
        rng = random.Random(21)
        for _ in range(500):
            a, b = random_string(rng), random_string(rng)
            assert indel_ratio(a, b) >= levenshtein_ratio(a, b) - 1e-9

    def test_ratios_are_bounded(self):
        rng = random.Random(22)
        for _ in range(300):
            a, b = random_string(rng), random_string(rng)
            assert 0.0 <= levenshtein_ratio(a, b) <= 100.0
            assert 0.0 <= indel_ratio(a, b) <= 100.0


class TestGetScorer:
    def test_returns_levenshtein_by_default(self):
        assert get_scorer() is levenshtein_ratio

    def test_selects_named_scorer(self):
        assert get_scorer("indel") is indel_ratio
        assert get_scorer("levenshtein") is levenshtein_ratio

    def test_rejects_unknown_name(self):
        with pytest.raises(ValueError, match="Unknown scorer"):
            get_scorer("jaro-winkler")


# ---------------------------------------------------------------------------
# Explanatory helpers
# ---------------------------------------------------------------------------

class TestThreeImplementationsAgree:
    """
    The project requires three approaches. They are only useful as a comparison
    if they compute the same function, so that is asserted directly rather than
    trusting each in isolation.
    """

    @pytest.mark.parametrize(
        "a, b, expected",
        [
            ("kitten", "sitting", 3),
            ("saturday", "sunday", 3),
            ("", "", 0),
            ("", "abc", 3),
            ("abc", "", 3),
            ("abc", "abc", 0),
            ("flaw", "lawn", 2),
        ],
    )
    def test_all_three_give_known_values(self, a, b, expected):
        assert levenshtein_distance(a, b) == expected
        assert levenshtein_distance_recursive(a, b) == expected
        assert levenshtein_distance_memoized(a, b) == expected

    def test_all_three_agree_on_random_input(self):
        """Kept short: the naive recursion is exponential."""
        rng = random.Random(31)
        for _ in range(400):
            a = random_string(rng, max_length=7)
            b = random_string(rng, max_length=7)
            expected = reference_levenshtein(a, b)
            assert levenshtein_distance(a, b) == expected
            assert levenshtein_distance_recursive(a, b) == expected
            assert levenshtein_distance_memoized(a, b) == expected

    def test_memoized_handles_realistic_drug_names(self):
        """
        The memoised version recurses, so it must not blow the stack on the
        longest names in the NDC list.
        """
        a = "dextromethorphan hydrobromide"
        b = "dextromethorpan hydrobromid"
        assert levenshtein_distance_memoized(a, b) == levenshtein_distance(a, b)

    def test_memoized_matches_iterative_on_longer_strings(self):
        rng = random.Random(32)
        for _ in range(50):
            a = random_string(rng, alphabet=string.ascii_lowercase, max_length=60)
            b = random_string(rng, alphabet=string.ascii_lowercase, max_length=60)
            assert levenshtein_distance_memoized(a, b) == levenshtein_distance(a, b)


class TestDistanceMatrix:
    def test_shape_and_borders(self):
        grid = distance_matrix("abc", "de")
        assert len(grid) == 4 and all(len(row) == 3 for row in grid)
        assert [row[0] for row in grid] == [0, 1, 2, 3]
        assert grid[0] == [0, 1, 2]

    def test_corner_equals_distance(self):
        rng = random.Random(23)
        for _ in range(200):
            a, b = random_string(rng, max_length=7), random_string(rng, max_length=7)
            assert distance_matrix(a, b)[-1][-1] == levenshtein_distance(a, b)


class TestEditOperations:
    def test_no_operations_for_identical_strings(self):
        assert edit_operations("aspirin", "aspirin") == []

    def test_kitten_to_sitting(self):
        operations = [op for op, _, _, _ in edit_operations("kitten", "sitting")]
        assert operations == ["substitute", "substitute", "insert"]

    def test_operation_count_equals_distance(self):
        rng = random.Random(24)
        for _ in range(300):
            a, b = random_string(rng, max_length=7), random_string(rng, max_length=7)
            assert len(edit_operations(a, b)) == levenshtein_distance(a, b)

    def test_applying_operations_transforms_a_into_b(self):
        """The script must be executable, not merely the right length."""
        rng = random.Random(25)
        for _ in range(300):
            a, b = random_string(rng, max_length=7), random_string(rng, max_length=7)
            characters = list(a)
            offset = 0
            for operation, index, _, new_char in edit_operations(a, b):
                if operation == "substitute":
                    characters[index + offset] = new_char
                elif operation == "delete":
                    del characters[index + offset]
                    offset -= 1
                else:
                    characters.insert(index + offset, new_char)
                    offset += 1
            assert "".join(characters) == b


# ---------------------------------------------------------------------------
# Realistic OCR-style inputs
# ---------------------------------------------------------------------------

class TestDrugNameScenarios:
    @pytest.mark.parametrize(
        "ocr_text, drug_name",
        [
            ("ibuprofen", "ibuprofen"),
            ("ibuprofn", "ibuprofen"),        # dropped character
            ("lbuprofen", "ibuprofen"),       # I/l confusion
            ("paracetamo1", "paracetamol"),   # l/1 confusion
            ("acetaminophen", "acetaminophen"),
            ("amoxicilin", "amoxicillin"),    # doubled letter lost
        ],
    )
    def test_ocr_noise_still_clears_threshold(self, ocr_text, drug_name):
        assert ratio(ocr_text, drug_name) > 75

    @pytest.mark.parametrize(
        "ocr_text",
        ["tablets", "400mg", "store in a cool dry place", "exp 2027", "batch no"],
    )
    def test_packaging_text_does_not_match_drugs(self, ocr_text):
        for drug_name in ("ibuprofen", "paracetamol", "amoxicillin"):
            assert ratio(ocr_text, drug_name) <= 75

    def test_different_drugs_stay_distinguishable(self):
        """Confusable real drug names must not collide at the tuned threshold."""
        assert ratio("paracetamol", "acetaminophen") <= 75
        assert ratio("metformin", "metronidazole") <= 75

    @pytest.mark.parametrize(
        "drug_a, drug_b, score",
        [
            ("ofloxacin", "levofloxacin", 75),
            ("omeprazole", "esomeprazole", 83),
            ("prednisone", "prednisolone", 83),
            ("losartan", "valsartan", 67),
            ("glimepiride", "gliclazide", 55),
        ],
    )
    def test_sibling_drug_names_score_as_documented(self, drug_a, drug_b, score):
        """
        Different medicines with near-identical names, and the reason the
        threshold is 75 rather than lower.

        Note what this does and does not establish. The threshold alone only
        separates the pairs scoring at or below it -- ofloxacin/levofloxacin
        lands on exactly 75 and is excluded by the strictly-greater comparison
        in DrugMatcher. The two pairs at 83 are *not* separated by the
        threshold; what protects those is that the correct name scores 100 and
        wins on argmax. That safety margin is why the threshold is not dropped
        to 70, where several more pairs would come into range.
        """
        assert ratio(drug_a, drug_b) == score

    def test_correct_sibling_wins_over_its_near_twin(self):
        """A clean read must resolve to itself, never to the similar sibling."""
        for drug, sibling in [
            ("prednisone", "prednisolone"),
            ("omeprazole", "esomeprazole"),
            ("ofloxacin", "levofloxacin"),
        ]:
            assert ratio(drug, drug) > ratio(drug, sibling)
            assert ratio(sibling, sibling) > ratio(sibling, drug)


# ---------------------------------------------------------------------------
# Unicode and robustness
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_handles_unicode(self):
        assert levenshtein_distance("café", "cafe") == 1
        assert levenshtein_distance("नेपाल", "नेपाल") == 0
        assert ratio("नेपाल", "नेपाल") == 100

    def test_handles_whitespace_and_punctuation(self):
        assert levenshtein_distance("co-codamol", "co codamol") == 1
        assert ratio("vitamin c", "vitamin c") == 100

    def test_handles_long_strings(self):
        a = "a" * 400
        b = "a" * 399 + "b"
        assert levenshtein_distance(a, b) == 1
        assert ratio(a, b) == 100  # rounds up from 99.75

    def test_repeated_characters(self):
        assert levenshtein_distance("aaa", "aa") == 1
        assert indel_distance("aaa", "aa") == 1
