"""
Pure-Python reference implementations of the project's edit-distance algorithms.

This module is the **reference**, not the fast path. ``src/Levenshtein.py`` is
the public API; it prefers the compiled kernel in ``src/clevenshtein/`` and
falls back to everything here when no C compiler was available at setup time.

Both backends are kept, permanently, for three reasons:

1. **Correctness.** ``tests/test_levenshtein.py`` runs the C results against
   these on thousands of generated inputs. A reference implementation that can
   disagree with the shipped one is the only way to catch a wrong optimisation,
   and the Python here is deliberately the plainest expression of each
   algorithm.
2. **Portability.** A clone with no toolchain still runs. The project degrades
   in speed, never in behaviour.
3. **Readability.** The algorithms are easier to follow here, and the C file
   was written from this, not the other way round.

Two distances are implemented:

1. ``levenshtein_distance`` -- the classic edit distance the project is built
   on. INSERTION, DELETION and SUBSTITUTION each cost 1::

       d(kitten, sitting) = 3   (k->s, e->i, +g)

2. ``indel_distance`` -- insertions and deletions only, also called the LCS
   distance, where a substitution costs 2::

       d(kitten, sitting) = 5

The second is retained because the code this replaced called
``thefuzz.fuzz.ratio``, which -- despite the library's name -- computes the
normalised indel similarity, not Levenshtein. Keeping it makes that claim
verifiable in the tests and is what justified moving the matching threshold from
85 to 75. Nothing in the pipeline scores with it.

Complexity
----------
Both distances run in O(len(a) * len(b)) time and O(min(len(a), len(b))) space,
using a rolling two-row DP table rather than the full matrix. Several exact
short-circuits (affix trimming, length bounds, cutoffs) are applied first; none
change the result, they only avoid work. ``distance_matrix()`` and
``edit_operations()`` build the full matrix, for explanation and debugging.
"""

from __future__ import annotations

from typing import List, Literal, Optional, Tuple

__all__ = [
    "levenshtein_distance",
    "levenshtein_distance_recursive",
    "levenshtein_distance_memoized",
    "levenshtein_ratio",
    "indel_distance",
    "indel_ratio",
    "lcs_length",
    "ratio",
    "get_scorer",
    "distance_matrix",
    "edit_operations",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _trim_common_affixes(a: str, b: str) -> Tuple[str, str]:
    """
    Strip the shared prefix and suffix of two strings.

    A leading/trailing run of identical characters can never be part of an
    optimal edit script, so removing it leaves both the Levenshtein and the
    indel distance unchanged while shrinking the DP table. This is a pure
    optimisation, not an approximation.
    """
    if not a or not b:
        return a, b

    # Common prefix.
    start = 0
    limit = min(len(a), len(b))
    while start < limit and a[start] == b[start]:
        start += 1

    # Common suffix, never crossing back over the prefix already consumed.
    end = 0
    limit -= start
    while end < limit and a[len(a) - 1 - end] == b[len(b) - 1 - end]:
        end += 1

    return a[start: len(a) - end], b[start: len(b) - end]


def _round_half_up_to_int(value: float) -> int:
    """
    Round to the nearest integer the way ``thefuzz`` does.

    ``thefuzz`` wraps its scorers in ``int(round(x))``, so this deliberately
    uses Python's built-in banker's rounding rather than "half away from zero".
    Matching the tie-breaking exactly keeps scores identical on the rare .5 case.
    """
    return int(round(value))


# ---------------------------------------------------------------------------
# Classic Levenshtein distance
# ---------------------------------------------------------------------------

def levenshtein_distance_recursive(a: str, b: str) -> int:
    """
    Naive recursive Levenshtein distance. Educational only -- do not ship this.

    Follows the mathematical definition directly: compare the last characters,
    then recurse on the three ways of fixing a mismatch.

    Runs in O(3^n) time, because the recursion tree re-solves the same
    subproblems over and over: ``d("ab", "cd")`` is reached by many different
    paths and recomputed each time. Around 12 characters this becomes unusable,
    which is precisely what motivates the memoised and iterative versions.

    Raises:
        RecursionError: On long inputs, via Python's recursion limit.

    Examples:
        >>> levenshtein_distance_recursive("kitten", "sitting")
        3
    """
    if not a:
        return len(b)
    if not b:
        return len(a)

    if a[-1] == b[-1]:
        return levenshtein_distance_recursive(a[:-1], b[:-1])

    return 1 + min(
        levenshtein_distance_recursive(a[:-1], b),      # deletion
        levenshtein_distance_recursive(a, b[:-1]),      # insertion
        levenshtein_distance_recursive(a[:-1], b[:-1]),  # substitution
    )


def levenshtein_distance_memoized(a: str, b: str) -> int:
    """
    Top-down dynamic programming: the recursion above plus a cache.

    Same recurrence, but each ``(i, j)`` subproblem is solved once and stored,
    collapsing the exponential blow-up to O(len(a) * len(b)) time. Memory is
    also O(len(a) * len(b)), since every solved cell is retained -- the price of
    keeping the recursive shape.

    Indices are used instead of string slices so the cache key is cheap and the
    slicing itself does not dominate the runtime.

    Examples:
        >>> levenshtein_distance_memoized("kitten", "sitting")
        3
    """
    cache: dict = {}

    def solve(i: int, j: int) -> int:
        """Distance between the first i characters of a and first j of b."""
        if i == 0:
            return j
        if j == 0:
            return i

        key = (i, j)
        if key in cache:
            return cache[key]

        if a[i - 1] == b[j - 1]:
            result = solve(i - 1, j - 1)
        else:
            result = 1 + min(
                solve(i - 1, j),      # deletion
                solve(i, j - 1),      # insertion
                solve(i - 1, j - 1),  # substitution
            )

        cache[key] = result
        return result

    # Iterative warm-up of the cache along one axis keeps the recursion depth
    # proportional to a single dimension rather than their sum, which avoids
    # hitting Python's recursion limit on realistic drug names.
    for i in range(len(a) + 1):
        solve(i, 0)

    return solve(len(a), len(b))


def levenshtein_distance(a: str, b: str, max_distance: Optional[int] = None) -> int:
    """
    Compute the Levenshtein edit distance between two strings.

    Bottom-up dynamic programming, and the version the pipeline uses. Insertion,
    deletion and substitution each cost 1.

    Of the three implementations here this is the one to ship: it runs in
    O(len(a) * len(b)) time like the memoised version, but in
    O(min(len(a), len(b))) space, because only the previous DP row is needed to
    compute the next one. It also has no recursion limit to trip over. See
    ``benchmark_levenshtein.py`` for the measured comparison.

    Args:
        a: First string.
        b: Second string.
        max_distance: Optional cutoff. If the true distance exceeds this value,
            the function may return early with ``max_distance + 1`` instead of
            the exact distance. Use it when you only care whether two strings
            are "close enough" -- it prunes a lot of work.

    Returns:
        The edit distance, or a value greater than ``max_distance`` if a cutoff
        was supplied and exceeded.

    Examples:
        >>> levenshtein_distance("kitten", "sitting")
        3
        >>> levenshtein_distance("ibuprofen", "ibuprofen")
        0
    """
    if a == b:
        return 0

    a, b = _trim_common_affixes(a, b)

    # Keep the shorter string on the inner axis to minimise memory.
    if len(a) > len(b):
        a, b = b, a

    len_a, len_b = len(a), len(b)

    if len_a == 0:
        # Only insertions remain.
        if max_distance is not None and len_b > max_distance:
            return max_distance + 1
        return len_b

    # The distance is at least the length difference, so bail out cheaply.
    if max_distance is not None and (len_b - len_a) > max_distance:
        return max_distance + 1

    # previous_row[i] = distance between a[:i] and the b-prefix processed so far.
    previous_row: List[int] = list(range(len_a + 1))

    for j in range(1, len_b + 1):
        char_b = b[j - 1]
        current_row = [j] + [0] * len_a

        for i in range(1, len_a + 1):
            substitution_cost = 0 if a[i - 1] == char_b else 1
            current_row[i] = min(
                previous_row[i] + 1,                        # deletion
                current_row[i - 1] + 1,                     # insertion
                previous_row[i - 1] + substitution_cost,    # substitution / match
            )

        # Every remaining distance is >= the row minimum, so once the whole row
        # is past the cutoff the final answer cannot come back under it.
        if max_distance is not None and min(current_row) > max_distance:
            return max_distance + 1

        previous_row = current_row

    return previous_row[len_a]


def levenshtein_ratio(a: str, b: str) -> float:
    """
    Normalise the Levenshtein distance to a 0-100 similarity score.

    Uses ``(1 - distance / max(len(a), len(b))) * 100``, so the score is 100 for
    identical strings and 0 when every character must change.

    Note this is *not* what ``ratio()`` returns; see the module docstring.

    Examples:
        >>> round(levenshtein_ratio("kitten", "sitting"), 2)
        57.14
        >>> levenshtein_ratio("", "")
        100.0
    """
    longest = max(len(a), len(b))
    if longest == 0:
        return 100.0

    return (1.0 - levenshtein_distance(a, b) / longest) * 100.0


# ---------------------------------------------------------------------------
# Indel distance (insertions and deletions only)
# ---------------------------------------------------------------------------

def lcs_length(a: str, b: str) -> int:
    """
    Length of the Longest Common Subsequence of two strings.

    Examples:
        >>> lcs_length("kitten", "sitting")
        4
    """
    if not a or not b:
        return 0

    if len(a) > len(b):
        a, b = b, a

    len_a = len(a)
    previous_row = [0] * (len_a + 1)

    for char_b in b:
        current_row = [0] * (len_a + 1)
        for i in range(1, len_a + 1):
            if a[i - 1] == char_b:
                current_row[i] = previous_row[i - 1] + 1
            else:
                current_row[i] = max(previous_row[i], current_row[i - 1])
        previous_row = current_row

    return previous_row[len_a]


def indel_distance(a: str, b: str) -> int:
    """
    Edit distance allowing only insertions and deletions.

    Every character outside the Longest Common Subsequence must be deleted from
    one string or inserted into the other, giving::

        indel = len(a) + len(b) - 2 * LCS(a, b)

    Examples:
        >>> indel_distance("kitten", "sitting")
        5
    """
    if a == b:
        return 0

    trimmed_a, trimmed_b = _trim_common_affixes(a, b)

    if not trimmed_a:
        return len(trimmed_b)
    if not trimmed_b:
        return len(trimmed_a)

    return len(trimmed_a) + len(trimmed_b) - 2 * lcs_length(trimmed_a, trimmed_b)


def indel_ratio(a: str, b: str) -> float:
    """
    Normalise the indel distance to a 0-100 similarity score.

    Uses ``(1 - distance / (len(a) + len(b))) * 100``. The denominator is the
    sum of the lengths because that is the worst case: delete everything from
    one string, then insert everything from the other.
    """
    total_length = len(a) + len(b)
    if total_length == 0:
        return 100.0

    return (1.0 - indel_distance(a, b) / total_length) * 100.0


# ---------------------------------------------------------------------------
# thefuzz-compatible public scorer
# ---------------------------------------------------------------------------

def ratio(a: str, b: str, score_cutoff: Optional[float] = None) -> int:
    """
    Classic-Levenshtein similarity from 0 to 100. The project's scorer.

    Args:
        a: First string.
        b: Second string.
        score_cutoff: Optional lower bound. If the score provably cannot reach
            this value, 0 is returned without running the full DP. Purely an
            optimisation for scanning large candidate lists.

    Returns:
        An integer score; 100 means identical, 0 means nothing in common.

    Examples:
        >>> ratio("ibuprofen", "ibuprofen")
        100
        >>> ratio("kitten", "sitting")
        57
    """
    if a is None or b is None:
        return 0

    longest = max(len(a), len(b))
    if longest == 0:
        return 100

    if score_cutoff is not None:
        # The distance is at least the length difference, so the best reachable
        # score is min/max. If even that ceiling misses the cutoff, skip the DP.
        best_possible = (min(len(a), len(b)) / longest) * 100.0
        if best_possible < score_cutoff:
            return 0

        # Translate the score cutoff into a distance cutoff, letting the DP
        # abandon a hopeless comparison part-way through its rows. The epsilon
        # absorbs binary-float error: longest * (100 - cutoff) / 100 can land a
        # hair under an exact integer and truncate to one distance too few,
        # which would reject genuine matches.
        max_distance = int(longest * (100.0 - score_cutoff) / 100.0 + 1e-9)
        distance = levenshtein_distance(a, b, max_distance=max_distance)

        # Past the cutoff the DP returns max_distance + 1, a lower bound rather
        # than the true distance, so it must not be turned into a score.
        if distance > max_distance:
            return 0
    else:
        distance = levenshtein_distance(a, b)

    score = (1.0 - distance / longest) * 100.0

    if score_cutoff is not None and score < score_cutoff:
        return 0

    return _round_half_up_to_int(score)


ScorerName = Literal["levenshtein", "indel"]


def get_scorer(name: ScorerName = "levenshtein"):
    """
    Look up a scoring function by name.

    Args:
        name: ``"levenshtein"`` (the default, and what the pipeline uses) or
            ``"indel"``, kept for the threshold-calibration comparison.

    Returns:
        A callable ``(a, b) -> float`` returning a 0-100 score.

    Raises:
        ValueError: If the name is not recognised.
    """
    if name == "levenshtein":
        return levenshtein_ratio
    if name == "indel":
        return indel_ratio
    raise ValueError(f"Unknown scorer: {name!r}. Expected 'levenshtein' or 'indel'.")


# ---------------------------------------------------------------------------
# Explanatory helpers (full matrix + backtrace)
# ---------------------------------------------------------------------------

def distance_matrix(a: str, b: str) -> List[List[int]]:
    """
    Build the complete Levenshtein DP matrix.

    Uses O(len(a) * len(b)) memory, so this is meant for small inputs,
    documentation and debugging rather than the hot matching path.

    Returns:
        A ``(len(a) + 1) x (len(b) + 1)`` grid where ``grid[i][j]`` is the
        distance between ``a[:i]`` and ``b[:j]``. The true distance is the
        bottom-right cell.

    Examples:
        >>> distance_matrix("ab", "ab")[-1][-1]
        0
    """
    len_a, len_b = len(a), len(b)
    grid = [[0] * (len_b + 1) for _ in range(len_a + 1)]

    for i in range(len_a + 1):
        grid[i][0] = i
    for j in range(len_b + 1):
        grid[0][j] = j

    for i in range(1, len_a + 1):
        for j in range(1, len_b + 1):
            substitution_cost = 0 if a[i - 1] == b[j - 1] else 1
            grid[i][j] = min(
                grid[i - 1][j] + 1,
                grid[i][j - 1] + 1,
                grid[i - 1][j - 1] + substitution_cost,
            )

    return grid


def edit_operations(a: str, b: str) -> List[Tuple[str, int, str, str]]:
    """
    Recover one optimal edit script transforming ``a`` into ``b``.

    Walks the DP matrix backwards from the bottom-right corner. Ties are broken
    in a fixed order, so the script is one optimal solution, not the only one.

    Returns:
        A list of ``(operation, index, from_char, to_char)`` tuples in
        left-to-right order, where operation is ``"insert"``, ``"delete"`` or
        ``"substitute"``. Matches are not reported.

    Examples:
        >>> [op for op, _, _, _ in edit_operations("kitten", "sitting")]
        ['substitute', 'substitute', 'insert']
    """
    grid = distance_matrix(a, b)
    operations: List[Tuple[str, int, str, str]] = []

    i, j = len(a), len(b)
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1] and grid[i][j] == grid[i - 1][j - 1]:
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and grid[i][j] == grid[i - 1][j - 1] + 1:
            operations.append(("substitute", i - 1, a[i - 1], b[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and grid[i][j] == grid[i - 1][j] + 1:
            operations.append(("delete", i - 1, a[i - 1], ""))
            i -= 1
        else:
            operations.append(("insert", i, "", b[j - 1]))
            j -= 1

    operations.reverse()
    return operations
