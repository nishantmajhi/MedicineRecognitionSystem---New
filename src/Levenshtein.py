"""
Custom string-similarity algorithms for the Medicine Recognition System.

This module replaces the third-party ``thefuzz`` / ``RapidFuzz`` /
``python-Levenshtein`` dependency. No third-party fuzzy-matching library remains
in ``requirements.txt``.

It is a **facade over two backends**, and the only name the rest of the project
imports:

* ``src/clevenshtein/levenshtein.c`` -- the algorithms in C, compiled to a
  shared library during setup and bound with ``ctypes``. Used when present.
* ``src/_levenshtein_python.py`` -- the same algorithms in pure Python. The
  reference implementation, and the fallback when no compiler was available.

Which one is active is reported by :data:`BACKEND`. Both are always importable,
because the test suite runs them against each other; see
``tests/test_levenshtein.py``.

Why both exist
--------------
The C build is here for speed. Matching one OCR fragment scores it against
~13,000 NDC names, and the inner loop of a DP table -- three integer additions
and a minimum, per cell -- is precisely what an interpreter is worst at. The
Python build is here because a clone with no toolchain still has to run, and
because a reference implementation that can disagree with the shipped one is the
only way to catch a wrong optimisation.

The algorithms
--------------
Three implementations of the same function are provided, as required, and the
tests assert they agree::

    levenshtein_distance_recursive   naive recursion       O(3^n) time
    levenshtein_distance_memoized    top-down DP           O(mn) time, O(mn) space
    levenshtein_distance             bottom-up two-row DP  O(mn) time, O(min(m,n)) space

The third is what ships. See ``benchmark_levenshtein.py`` for the measurements,
which cover both backends.

Two distances are implemented:

1. ``levenshtein_distance`` -- classic edit distance. INSERTION, DELETION and
   SUBSTITUTION each cost 1::

       d(kitten, sitting) = 3   (k->s, e->i, +g)

2. ``indel_distance`` -- insertions and deletions only, so a substitution costs
   2::

       d(kitten, sitting) = 5

``ratio()`` is the scorer the rest of the project calls, and it uses the first.

Why the second one is still here
--------------------------------
The code this replaced called ``thefuzz.fuzz.ratio``. Despite the library's
name, that function is *not* classic Levenshtein -- it is the normalised indel
similarity. ``indel_ratio`` is retained so that claim stays verifiable and so
the threshold change from 85 to 75 can be justified rather than asserted.
Nothing in the pipeline scores with it.

The two metrics agree when the strings are the same length and differ only by
substitutions::

    ibuprofen / ibuprofan   ->  levenshtein 88.9,  indel 88.9

They diverge as soon as characters are inserted or dropped, where Levenshtein is
the stricter of the two::

    ibuprofen / ibuprofn        ->  levenshtein 88.9,  indel 94.1
    acetaminophen / acetaminphn ->  levenshtein 84.6,  indel 91.7

Dropped characters are a common OCR failure, so this is not a corner case. See
``DrugMatcher`` for the recall measurements behind the threshold of 75.
"""

from __future__ import annotations

from typing import Callable, List, Literal, Optional, Tuple

from . import _levenshtein_native as _native
from . import _levenshtein_python as _python

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
    # Backend introspection.
    "BACKEND",
    "BACKEND_DETAIL",
    "using_native",
    "backend_report",
    "python_backend",
    "native_backend",
]


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

#: The two implementations, exposed by name so the tests can compare them
#: directly and the benchmark can time them side by side.
python_backend = _python
native_backend = _native

#: ``"c"`` when the compiled kernel is loaded, ``"python"`` otherwise.
BACKEND: str = "c" if _native.AVAILABLE else "python"

#: Human-readable detail: the library path, or why it could not be used.
BACKEND_DETAIL: str = (
    _native.LIBRARY_PATH if _native.AVAILABLE
    else (_native.UNAVAILABLE_REASON or "unknown")
)

_active = _native if _native.AVAILABLE else _python


def using_native() -> bool:
    """True when the compiled kernel is in use."""
    return BACKEND == "c"


def backend_report() -> str:
    """
    One line describing the active backend, for startup logs and the benchmark.

    Reported rather than raised on purpose: running on the fallback is a
    supported state, and the project should say so plainly rather than fail.
    """
    if using_native():
        return f"Levenshtein backend: C (compiled)  [{BACKEND_DETAIL}]"
    return (
        f"Levenshtein backend: pure Python (fallback)  [{BACKEND_DETAIL}]\n"
        "  Matching will be slower. Build the C kernel with:\n"
        "    python -m src.clevenshtein.build"
    )


# ---------------------------------------------------------------------------
# Public API -- dispatched to the active backend
# ---------------------------------------------------------------------------
#
# These are thin wrappers rather than module-level rebinding
# (``levenshtein_distance = _active.levenshtein_distance``) because the wrappers
# keep signatures, docstrings and ``help()`` output stable regardless of which
# backend loaded, and because a test that swaps ``_active`` then takes effect
# without re-importing every caller.

def levenshtein_distance(a: str, b: str, max_distance: Optional[int] = None) -> int:
    """
    Levenshtein edit distance between two strings. Bottom-up two-row DP.

    This is the version the pipeline uses: O(len(a) * len(b)) time like the
    memoised variant, but O(min(len(a), len(b))) space, since only the previous
    DP row is needed to compute the next. It has no recursion limit to trip over.

    Args:
        a: First string.
        b: Second string.
        max_distance: Optional cutoff. If the true distance exceeds this value
            the function may return ``max_distance + 1`` instead of the exact
            distance -- a lower bound, not an answer, so do not turn it into a
            score. Use it when you only care whether two strings are close
            enough; it prunes a lot of work.

    Examples:
        >>> levenshtein_distance("kitten", "sitting")
        3
        >>> levenshtein_distance("ibuprofen", "ibuprofen")
        0
    """
    return _active.levenshtein_distance(a, b, max_distance)


def levenshtein_distance_recursive(a: str, b: str) -> int:
    """
    Naive recursive Levenshtein distance. Educational only -- do not ship this.

    Follows the mathematical definition directly: compare the last characters,
    then recurse on the three ways of fixing a mismatch. Runs in O(3^n) time
    because the recursion tree re-solves identical subproblems, and becomes
    unusable around a dozen characters. That is what motivates the other two.

    Compiling it does not rescue it -- the C version is faster by a constant
    factor and still exponential, which is the point the benchmark makes.

    Raises:
        RecursionError: On long inputs (Python's recursion limit, or the C
            kernel's equivalent ceiling).

    Examples:
        >>> levenshtein_distance_recursive("kitten", "sitting")
        3
    """
    return _active.levenshtein_distance_recursive(a, b)


def levenshtein_distance_memoized(a: str, b: str) -> int:
    """
    Top-down dynamic programming: the naive recursion above plus a cache.

    Same recurrence, but each ``(i, j)`` subproblem is solved once and stored,
    collapsing the exponential time to O(len(a) * len(b)). Memory is O(mn) too,
    since every solved cell is retained -- the price of keeping the recursive
    shape, and the reason the iterative version is the one that ships.

    Examples:
        >>> levenshtein_distance_memoized("kitten", "sitting")
        3
    """
    return _active.levenshtein_distance_memoized(a, b)


def levenshtein_ratio(a: str, b: str) -> float:
    """
    Normalise the Levenshtein distance to a 0-100 similarity score.

    Uses ``(1 - distance / max(len(a), len(b))) * 100``, so identical strings
    score 100 and strings sharing nothing score 0.

    Examples:
        >>> round(levenshtein_ratio("kitten", "sitting"), 2)
        57.14
        >>> levenshtein_ratio("", "")
        100.0
    """
    return _active.levenshtein_ratio(a, b)


def lcs_length(a: str, b: str) -> int:
    """
    Length of the Longest Common Subsequence of two strings.

    Examples:
        >>> lcs_length("kitten", "sitting")
        4
    """
    return _active.lcs_length(a, b)


def indel_distance(a: str, b: str) -> int:
    """
    Edit distance allowing only insertions and deletions.

    Every character outside the Longest Common Subsequence must be deleted from
    one string or inserted into the other::

        indel = len(a) + len(b) - 2 * LCS(a, b)

    Examples:
        >>> indel_distance("kitten", "sitting")
        5
    """
    return _active.indel_distance(a, b)


def indel_ratio(a: str, b: str) -> float:
    """
    Normalise the indel distance to a 0-100 similarity score.

    Uses ``(1 - distance / (len(a) + len(b))) * 100``. The denominator is the
    sum of the lengths because that is the worst case: delete everything from
    one string, then insert everything from the other.
    """
    return _active.indel_ratio(a, b)


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
    return _active.ratio(a, b, score_cutoff)


ScorerName = Literal["levenshtein", "indel"]


def get_scorer(name: ScorerName = "levenshtein") -> Callable[[str, str], float]:
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
# Explanatory helpers
# ---------------------------------------------------------------------------
#
# These build the full DP matrix and exist for documentation and debugging, not
# the hot path, so they stay in Python where they can be read. There is nothing
# to gain from compiling something that never runs during a request.

def distance_matrix(a: str, b: str) -> List[List[int]]:
    """
    Build the complete Levenshtein DP matrix.

    Uses O(len(a) * len(b)) memory, so this is meant for small inputs,
    documentation and debugging rather than the matching path.

    Returns:
        A ``(len(a) + 1) x (len(b) + 1)`` grid where ``grid[i][j]`` is the
        distance between ``a[:i]`` and ``b[:j]``. The true distance is the
        bottom-right cell.

    Examples:
        >>> distance_matrix("ab", "ab")[-1][-1]
        0
    """
    return _python.distance_matrix(a, b)


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
    return _python.edit_operations(a, b)
