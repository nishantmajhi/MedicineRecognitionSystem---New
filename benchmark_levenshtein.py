"""
Performance comparison of the Levenshtein implementations.

Two comparisons, which answer two different questions:

1. **Algorithm.** Naive recursion vs top-down memoisation vs bottom-up two-row
   DP. This is the project requirement, and the answer is a property of the
   algorithms -- it holds in either language.
2. **Backend.** Pure Python vs the compiled C kernel, running the *same*
   algorithm. This is what the C build bought, and it is reported separately so
   the two effects are not confused with each other.

Run from the project root::

    python benchmark_levenshtein.py
    python benchmark_levenshtein.py --quick     # fewer repeats

A note on the memory column
---------------------------
Peak memory is measured with ``tracemalloc``, which instruments Python's
allocator and is blind to ``malloc`` inside the shared library. The C rows
therefore report near-zero, which is an artefact of the tool, not a result --
the C memoised variant allocates the same O(mn) table its Python counterpart
does. Memory figures are meaningful *within* a backend, comparing approaches to
each other, and that is the comparison the requirement asks for.
"""

from __future__ import annotations

import argparse
import random
import string
import sys
import time
import tracemalloc
from collections import Counter
from typing import Callable, List, Optional, Sequence, Tuple

sys.path.insert(0, ".")

from src.Levenshtein import (  # noqa: E402
    BACKEND,
    backend_report,
    native_backend,
    python_backend,
)

# Beyond this combined length the naive recursion is too slow to measure, and
# the C kernel refuses it outright to protect its own stack.
RECURSIVE_LIMIT = 10

SIZES: List[Tuple[str, int, int]] = [
    ("Short  (< 10 chars)", 8, 200),
    ("Medium (10-50 chars)", 30, 50),
    ("Long   (> 50 chars)", 120, 10),
]


def make_pair(length: int, rng: random.Random) -> Tuple[str, str]:
    """Two strings of the given length differing in roughly a fifth of places."""
    a = "".join(rng.choice(string.ascii_lowercase) for _ in range(length))
    characters = list(a)
    for _ in range(max(1, length // 5)):
        characters[rng.randrange(length)] = rng.choice(string.ascii_lowercase)
    return a, "".join(characters)


def measure(
    function: Callable[[str, str], int],
    pairs: Sequence[Tuple[str, str]],
) -> Tuple[float, float, int]:
    """Return (milliseconds per call, peak KiB, a sample result)."""
    tracemalloc.start()
    start = time.perf_counter()

    result = 0
    for a, b in pairs:
        result = function(a, b)

    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return (elapsed / len(pairs) * 1000, peak / 1024, result)


def approaches_for(backend) -> List[Tuple[str, Callable[[str, str], int]]]:
    """The three implementations exposed by one backend module, in ship order."""
    return [
        ("Iterative (2-row DP)", backend.levenshtein_distance),
        ("Memoized (top-down DP)", backend.levenshtein_distance_memoized),
        ("Recursive (naive)", backend.levenshtein_distance_recursive),
    ]


# ---------------------------------------------------------------------------
# Comparison 1: the three approaches, within one backend
# ---------------------------------------------------------------------------

def compare_approaches(backend, label: str, sizes) -> None:
    print()
    print(f"Approach comparison -- {label} backend")
    print("=" * 78)
    print(f"{'Input size':<22}{'Approach':<26}{'ms/call':>12}{'peak KiB':>12}")
    print("-" * 78)

    for size_label, length, repeats in sizes:
        rng = random.Random(20240101 + length)
        pairs = [make_pair(length, rng) for _ in range(repeats)]

        results = {}
        first = True
        for name, function in approaches_for(backend):
            if name.startswith("Recursive") and length > RECURSIVE_LIMIT:
                print(f"{size_label if first else '':<22}"
                      f"{name:<26}{'skipped':>12}{'(exponential)':>16}")
                first = False
                continue

            milliseconds, peak, value = measure(function, pairs)
            results[name] = value
            print(f"{size_label if first else '':<22}"
                  f"{name:<26}{milliseconds:>12.4f}{peak:>12.1f}")
            first = False

        # A fast wrong answer is worthless, so the run asserts agreement rather
        # than merely reporting times.
        distinct = set(results.values())
        assert len(distinct) == 1, f"implementations disagreed: {results}"
        print("-" * 78)


# ---------------------------------------------------------------------------
# Comparison 2: Python vs C, same algorithm
# ---------------------------------------------------------------------------

def compare_backends(sizes) -> None:
    if not native_backend.AVAILABLE:
        print()
        print("Backend comparison -- SKIPPED")
        print("-" * 78)
        print(f"The C kernel is not loaded: {native_backend.UNAVAILABLE_REASON}")
        print("Build it with:  python -m src.clevenshtein.build")
        return

    print()
    print("Backend comparison -- iterative 2-row DP, identical algorithm")
    print("=" * 78)
    print(f"{'Input size':<22}{'Python ms':>12}{'C ms':>12}{'speed-up':>12}")
    print("-" * 78)

    for size_label, length, repeats in sizes:
        rng = random.Random(20240101 + length)
        pairs = [make_pair(length, rng) for _ in range(max(repeats, 200))]

        python_ms, _, python_value = measure(
            python_backend.levenshtein_distance, pairs)
        native_ms, _, native_value = measure(
            native_backend.levenshtein_distance, pairs)

        assert python_value == native_value, "backends disagreed"

        speedup = python_ms / native_ms if native_ms else float("inf")
        print(f"{size_label:<22}{python_ms:>12.4f}{native_ms:>12.4f}"
              f"{speedup:>11.1f}x")

    print("-" * 78)
    print(
        "The speed-up shrinks on short strings because the fixed cost of a\n"
        "ctypes call and the UTF-32 conversion is amortised over fewer DP\n"
        "cells. Drug names sit at the short end, which is why the code-point\n"
        "cache in src/_levenshtein_native.py matters as much as the C does."
    )


# ---------------------------------------------------------------------------
# Comparison 3: the scorer in anger
# ---------------------------------------------------------------------------

def compare_scorer(repeats: int) -> None:
    """
    ``ratio()`` with a cutoff, on realistic drug-name lengths.

    This is the call the matcher actually makes thousands of times per lookup,
    so it is the number that predicts request latency -- unlike the raw distance
    above, it includes the cutoff short-circuits, which reject most candidates
    before the DP starts.
    """
    if not native_backend.AVAILABLE:
        return

    rng = random.Random(99)
    names = [
        "ibuprofen", "acetaminophen", "paracetamol", "amoxicillin",
        "omeprazole", "esomeprazole", "prednisone", "prednisolone",
        "ofloxacin", "levofloxacin", "metformin", "atorvastatin",
        "azithromycin", "ciprofloxacin", "hydrochlorothiazide",
    ]
    queries = ["ibuprofn", "acetaminphn", "omeprzole", "levofloxcin"]
    pairs = [(rng.choice(queries), rng.choice(names)) for _ in range(repeats)]

    print()
    print("Scorer comparison -- ratio(a, b, score_cutoff=75) on drug names")
    print("=" * 78)

    for label, backend in (("Python", python_backend), ("C", native_backend)):
        start = time.perf_counter()
        for a, b in pairs:
            backend.ratio(a, b, 75)
        elapsed = (time.perf_counter() - start) / len(pairs) * 1e6
        print(f"  {label:<8}{elapsed:>10.2f} us/call")

    print("-" * 78)


# ---------------------------------------------------------------------------
# Comparison 4: the whole scan
# ---------------------------------------------------------------------------

def compare_batch_scan(name_count: int, quick: bool) -> None:
    """
    The measurement that actually matters: a full ``lookup()`` over a large
    candidate list, Python loop versus C batch matcher.

    The three comparisons above all time ONE distance. This times the loop
    around it, which is where a real request spends its time -- and where
    compiling the distance function alone turns out to buy nothing, because the
    gates reject all but a handful of candidates before it is ever called.

    Also reports the thread sweep. On most machines the single-threaded row
    wins at this list size: a full scan costs less than spawning the threads to
    divide it. That is a result, not a bug -- see the README.
    """
    if not native_backend.AVAILABLE:
        return

    rng = random.Random(1234)
    prefixes = ["ibu", "aceta", "para", "amox", "ome", "pred", "oflox", "metfor",
                "atorva", "azithro", "cipro", "hydro", "dextro", "salbu", "lido",
                "adrena", "ceftri", "doxy", "gaba", "losar", "amlo", "simva"]
    middles = ["pro", "mino", "ceta", "i", "pra", "ni", "a", "o", "for", "va",
               "thro", "ro", "chloro", "ta", "te", "ca", "ph", "ax", "cy", "di"]
    suffixes = ["fen", "phen", "mol", "cillin", "zole", "sone", "xacin", "min",
                "statin", "mycin", "phan", "tamol", "terol", "caine", "rine",
                "axone", "cline", "tin", "pine", "prazole", "tidine", "olol"]

    names = set()
    attempts = 0
    while len(names) < name_count and attempts < name_count * 40:
        attempts += 1
        names.add(rng.choice(prefixes) + rng.choice(middles) + rng.choice(suffixes))
    names = sorted(names)

    queries = ["ibuprofn", "acetaminphn", "omeprzole",
               "levofloxcin", "paracetmol", "amoxicilin"]
    repeats = 3 if quick else 20

    print()
    print(f"Full-scan comparison -- lookup() over {len(names):,} names, threshold 75")
    print("=" * 78)

    scorer = python_backend.levenshtein_ratio

    # Row 1: exhaustive Python. No gates at all -- the naive way to write this.
    start = time.perf_counter()
    for query in queries:
        best_name, best_score = "", 0.0
        for name in names:
            score = scorer(query, name)
            if score > 75 and score > best_score:
                best_name, best_score = name, score
    naive_ms = (time.perf_counter() - start) / len(queries) * 1000
    print(f"  {'Python, exhaustive (no gates)':<34}{naive_ms:>10.2f} ms/lookup")

    # Row 2: the gated Python loop -- what DrugMatcher actually runs on the
    # fallback path. This is the honest baseline for the C rows: comparing the
    # batch matcher against the ungated scan above would credit the C with the
    # gates' speed-up as well as its own.
    #
    # The gates are reproduced here rather than imported because importing
    # DrugMatcher pulls in utils/__init__ and therefore easyocr and Torch,
    # which the benchmark should not require.
    length_index = {}
    for name in names:
        length_index.setdefault(len(name), []).append(name)
    counts = {name: Counter(name) for name in names}

    start = time.perf_counter()
    for query in queries:
        best_name, best_score = "", 0.0
        query_counts = Counter(query)
        lowest = int(0.75 * len(query))
        highest = int(len(query) / 0.75) + 1
        for length in range(max(1, lowest), highest + 1):        # gate 1
            for name in length_index.get(length, ()):
                longest = max(len(query), length)
                bar = max(best_score, 75.0)
                if (min(len(query), length) / longest) * 100 <= bar:  # gate 2
                    continue
                difference = sum(
                    (query_counts - counts[name]).values()) + sum(
                    (counts[name] - query_counts).values())
                if (difference + 1) // 2 > longest * (1 - bar / 100) + 1e-9:  # gate 3
                    continue
                score = scorer(query, name)
                if score > 75 and score > best_score:
                    best_name, best_score = name, score
    python_ms = (time.perf_counter() - start) / len(queries) * 1000
    print(f"  {'Python, gated (DrugMatcher path)':<34}{python_ms:>10.2f} ms/lookup"
          f"  {naive_ms / python_ms:>8.0f}x")

    for threads in (1, 2, 4, 0):
        index = native_backend.NameIndex(names, thread_count=threads)
        if not index.available:
            continue
        index.best_match(queries[0], 75)          # warm
        start = time.perf_counter()
        for _ in range(repeats):
            for query in queries:
                index.best_match(query, 75)
        native_ms = (time.perf_counter() - start) / (repeats * len(queries)) * 1000
        label = "auto" if threads == 0 else str(threads)
        speedup = python_ms / native_ms if native_ms else float("inf")
        print(f"  {'C batch scan, ' + label + ' thread(s)':<34}"
              f"{native_ms:>10.3f} ms/lookup  {speedup:>8.0f}x")
        if threads == 1:
            _, _, scored, examined = index.best_match(queries[0], 75)
            gate_line = (f"  gates: {examined:,} candidates examined, "
                         f"{scored} reached the DP")
        index.close()

    print("-" * 78)
    print(gate_line)
    print(
        "  Speed-ups are against the GATED Python row, not the exhaustive one --\n"
        "  crediting the C with the gates' improvement as well as its own would\n"
        "  roughly double every figure above.\n"
        "  The gate ratio is why compiling the distance function alone changes\n"
        "  nothing: it runs a couple of dozen times per lookup while the gates\n"
        "  run thousands of times. Moving the gates is the whole speed-up.\n"
        f"  Cores available: {native_backend.CPU_COUNT}."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Levenshtein benchmarks")
    parser.add_argument("--quick", action="store_true",
                        help="fewer repeats, for a fast sanity check")
    arguments = parser.parse_args()

    sizes = [(label, length, max(2, repeats // 5) if arguments.quick else repeats)
             for label, length, repeats in SIZES]

    print("Levenshtein implementation comparison")
    print("=" * 78)
    print(backend_report())

    compare_approaches(python_backend, "pure Python", sizes)
    if native_backend.AVAILABLE:
        compare_approaches(native_backend, "C (compiled)", sizes)

    compare_backends(sizes)
    compare_scorer(20000 if not arguments.quick else 2000)
    compare_batch_scan(2000 if arguments.quick else 13000, arguments.quick)

    print()
    print("Conclusion")
    print("-" * 78)
    print(
        "ALGORITHM. The naive recursion re-solves identical subproblems, giving\n"
        "O(3^n) time; it is unusable past roughly a dozen characters in either\n"
        "language. Memoisation caches each (i, j) subproblem, cutting this to\n"
        "O(len(a) * len(b)) time, but retains every cell, so memory is also\n"
        "O(len(a) * len(b)) and the recursion depth grows with the input. The\n"
        "iterative version matches that time complexity while keeping only two\n"
        "rows, so memory is O(min(len(a), len(b))) and there is no recursion at\n"
        "all. That is why DrugMatcher uses it.\n\n"
        "BACKEND. Compiling the iterative version changes the constant factor,\n"
        "not the complexity -- the recursive version stays exponential in C.\n\n"
        "SCOPE. The backend table times one distance; the full-scan table times\n"
        "the loop around it. Only the second predicts request latency, and the\n"
        "two disagree sharply: a faster scorer called eighteen times cannot\n"
        "move a lookup whose gates run four thousand times in Python. Compile\n"
        "the loop, not the callee.\n\n"
        "Matching is still only 15-35% of request latency, so Amdahl's law caps\n"
        "the end-to-end effect well below even the full-scan figures."
    )
    print()
    print(f"Active backend for the application: {BACKEND}")


if __name__ == "__main__":
    main()
