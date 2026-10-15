# The Levenshtein implementation

`src/Levenshtein.py` replaces the `thefuzz` dependency entirely. `thefuzz`,
`RapidFuzz`, `python-Levenshtein` and `Levenshtein` have been removed from
`requirements.txt`; no third-party fuzzy-matching library remains.

## Three approaches

As required, three implementations are provided, and the test suite asserts they
compute the same function:

| Function | Approach | Time | Space |
|----------|----------|------|-------|
| `levenshtein_distance_recursive` | Naive recursion | O(3^n) | O(n) stack |
| `levenshtein_distance_memoized` | Top-down DP (cached recursion) | O(mn) | O(mn) |
| `levenshtein_distance` | **Bottom-up DP, two rows** | O(mn) | O(min(m, n)) |

Measured with `python benchmark_levenshtein.py`, pure-Python backend:

```
Input size            Approach                       ms/call    peak KiB
------------------------------------------------------------------------
Short  (< 10 chars)   Iterative (2-row DP)            0.0113         0.5
                      Memoized (top-down DP)          0.0586       202.8
                      Recursive (naive)               3.3805         0.6
Medium (10-50 chars)  Iterative (2-row DP)            0.7131         1.1
                      Memoized (top-down DP)          1.4317       662.5
                      Recursive (naive)              skipped   (exponential)
Long   (> 50 chars)   Iterative (2-row DP)           15.3028         3.3
                      Memoized (top-down DP)         60.4707      6619.6
                      Recursive (naive)              skipped   (exponential)
```

The benchmark runs the same three approaches through the C backend as well. The
timings there are in [Kernel benchmarks](#kernel-benchmarks); the *memory*
column is not meaningful for C, because `tracemalloc` instruments Python's
allocator and is blind to `malloc` inside the shared library. The C memoised
variant allocates the same O(mn) table its Python counterpart does — the tool
simply cannot see it. Memory figures are comparable within a backend, which is
the comparison the requirement asks for.

**Conclusion.** The naive recursion re-solves identical subproblems and is
already 299x slower than the iterative version at 8 characters; past roughly a
dozen characters it is unusable. Compiling it does not help — the C version is
faster by a constant factor and still exponential, which is why the C backend
skips it at the same input sizes. Memoisation fixes the time complexity but
keeps every solved cell, so at 120 characters it uses about 2,000x the memory of
the iterative version and still recurses. The bottom-up version keeps only the
previous DP row, so it is the one `DrugMatcher` uses -- matching a single OCR
fragment scans thousands of NDC names, where both the constant factor and the
memory footprint matter.

## `fuzz.ratio` was never classic Levenshtein

Worth knowing if you are comparing against the old behaviour. Despite the
library name, `thefuzz.fuzz.ratio` computes the normalised **indel** distance,
where a substitution costs 2 (one deletion plus one insertion) rather than 1:

```
kitten / sitting  ->  classic Levenshtein 57,  thefuzz 62
```

The two metrics are identical when the strings are the same length and differ
only by substitutions. They diverge as soon as characters are inserted or
dropped, where classic Levenshtein is the stricter of the two:

```
ibuprofen / ibuprofn         ->  levenshtein 88.9,  indel 94.1
acetaminophen / acetaminphn  ->  levenshtein 84.6,  indel 91.7
```

`indel_ratio` is retained in the module so this claim stays verifiable in the
tests, but nothing in the pipeline scores with it.

## Why the threshold moved from 85 to 75

Dropped characters are a common OCR failure, so the divergence above is not a
corner case: carrying the old threshold of 85 across to classic Levenshtein
would have silently rejected genuine matches. Measured on simulated OCR noise
over a list of real generic names:

| Configuration | Recall | False positives |
|---|---|---|
| indel @ 85 (old `thefuzz` behaviour) | 80.2% | 0% |
| Levenshtein @ 85 (naive swap) | **72.7%** | 0% |
| **Levenshtein @ 75 (shipped)** | **95.3%** | 0% |

75 also sits on a meaningful boundary. Sibling drug names are the real hazard,
and the closest pairs are genuinely different medicines:

```
ofloxacin  / levofloxacin   75.0    <- excluded, comparison is strictly greater
omeprazole / esomeprazole   83.3
prednisone / prednisolone   83.3
```

Note what the threshold does and does not buy you. It excludes the pair at
exactly 75; the two pairs at 83 are **not** separated by it, and what protects
those is that a correct read scores 100 and wins on argmax. That margin is why
the threshold is not dropped further -- naming the wrong medicine is a worse
failure than naming none.

---

# The compiled kernel

`src/clevenshtein/levenshtein.c` implements the same algorithms as
`src/_levenshtein_python.py` — all three approaches, both distances, and the
`ratio()` scorer with its cutoff logic. It is compiled into a shared library
during setup and bound with `ctypes`.

Both backends stay in the repository permanently. `src/Levenshtein.py` picks one
and is the only module anything else imports:

```
src/Levenshtein.py                 facade, chooses a backend
    ├── src/_levenshtein_native.py  ctypes → liblevenshtein-<platform>.so/.dll
    └── src/_levenshtein_python.py  pure Python, reference and fallback
```

## Build and fallback

The build is deliberately **never fatal**. `src/clevenshtein/build.py` looks for
`cl`, `cc`, `gcc` and `clang` in that order; if none is present it says so and
exits 0, and the project runs on Python. A missing toolchain should cost speed,
not availability — this has to be cloned onto an unknown machine and
demonstrated.

It runs from three places, all through the same idempotent `ensure_built()`:
`setup.bat` / `setup.sh`, application startup in `main.py`, and the first import
of the binding. After the first build each of those is a single `stat` call.

```bash
python -m src.clevenshtein.build           # build if out of date
python -m src.clevenshtein.build --force   # rebuild
python -m src.clevenshtein.build --clean   # remove the artefact
```

The artefact is named `liblevenshtein-<system>-<machine>.<ext>`, so a repository
shared between machines — or a Windows checkout opened under WSL — cannot load a
library built for the wrong target. It also exports an ABI version that the
binding checks on load; a mismatch falls back to Python rather than calling
through a stale signature. A wrong result is a much worse failure than a slow
one.

## Why ctypes and a plain shared library, not a CPython extension

A `Python.h` extension module would be slightly faster per call, but it must be
built against the exact interpreter that imports it, needs `setuptools` and a
wheel, and produces a binary tied to one CPython ABI. A bare `.so`/`.dll` is one
`cc` invocation, works for any interpreter on the platform, and binds with a
module already in the standard library. No new entry in `requirements.txt`.

## Passing strings across the boundary

The C functions take arrays of Unicode **code points**, not bytes, because edit
distance counts characters. Over UTF-8 `café` and `cafe` differ by two bytes but
one character, so a bytes-based kernel would score every accented or non-Latin
name wrong while ASCII kept working — the kind of bug that reaches production.
`str.encode('utf-32-le')` produces that array in one C-level pass.

This layer turned out to matter more than expected. The first version declared
the argument as `POINTER(c_uint32)` and called `ctypes.cast` per call, which is
the obviously-typed way to do it. On drug-name-length strings **C was slower
than Python**:

```
ratio(a, b, cutoff=75), 9-character names
  POINTER(c_uint32) + cast per call :  2.86 us
  c_char_p, bytes passed straight   :  0.89 us   -> 3.2x
```

Both are the same pointer at the ABI level; the difference is entirely ctypes'
per-call conversion. Encoded buffers are also cached with an `lru_cache`, since
one lookup scores a query against thousands of names and converts each of them
repeatedly.

## Kernel benchmarks

`python benchmark_levenshtein.py` measures both backends. On the same machine,
same algorithm:

```
Input size               Python ms        C ms    speed-up
Short  (< 10 chars)         0.0122      0.0049        2.5x
Medium (10-50 chars)        0.7746      0.0098       79.1x
Long   (> 50 chars)        15.1149      0.0239      632.7x

ratio(a, b, score_cutoff=75) on real drug names
  Python   7.78 us/call
  C        1.23 us/call                              6.3x
```

The speed-up grows with length because the ctypes call is a fixed cost while the
DP is quadratic. Drug names sit at the short end, which is exactly why the
marshalling work above was worth doing.

Compiling does not change complexity. The naive recursion is still exponential
in C — it is faster by a constant factor and still unusable past a dozen
characters, which is the point the benchmark makes by running all three
approaches in both languages.

## Moving the whole scan into C

Compiling the distance function alone changed nothing end to end, and the
measurement is worth more than the kernel benchmarks above.

`DrugMatcher.lookup` against a 13,000-name list:

```
DrugMatcher.lookup, 13,003 synthetic names, threshold 75
  python loop + python kernel      25.17 ms/lookup
  python loop + C kernel           28.02 ms/lookup     <- no better
  C loop + C kernel                 0.05 ms/lookup     <- ~500x
```

`python benchmark_levenshtein.py` reports the same thing with the gates broken
out, so the C is not credited with their improvement as well as its own:

```
lookup() over 9,680 names, threshold 75
  Python, exhaustive (no gates)     240.22 ms/lookup
  Python, gated (DrugMatcher path)   29.21 ms/lookup      8x
  C batch scan, 1 thread              0.042 ms/lookup    688x
```

The middle row is the important one. Calling a 6.3x faster scorer from the same
Python loop made no difference, because the three gates reject all but a handful
of candidates:

```
query 'ibuprofn', 13,003 synthetic names, threshold 75
  4,250 candidates examined by the gates
     18 reached the dynamic program
```

(Those counts come from a generated name list of the same size as the real NDC
one, not from the NDC list itself — the gate table in
[How matching is made fast](#how-matching-is-made-fast) has the real figures.
The generated names cluster more tightly by length, so more candidates survive
gate 1 there than they would in production. That makes the measurement
conservative rather than flattering.)

So the scorer ran 18 times per lookup and the gate arithmetic ran 4,250 times —
in Python. Compiling the callee cannot fix a bottleneck living in the caller.
That is Amdahl's law one level below where
[How matching is made fast](#how-matching-is-made-fast) applies it, and it is
why `levenshtein.c` grew a batch matcher: the length band, both bounds and the
DP all moved into C, and the Python side now makes **one** call per lookup
instead of thousands.

`DrugMatcher` builds the index lazily and falls back to the Python loop if the
kernel is missing or the native call raises. Both paths are asserted to return
the same name.

## Threading, and why it is off by default

The scan is embarrassingly parallel — every candidate is independent — and this
is the one place Python genuinely cannot compete. Python threads share the
interpreter lock; `ctypes` releases the GIL for the duration of a foreign call,
so threads spawned inside `mrs_index_best_match` really do run on separate
cores.

The implementation is there, portable across pthreads and the Win32 API, and
correct: results are identical for 1, 2, 4 and 8 threads, verified against an
exhaustive Python scan across both scorers and three thresholds.

**It is nonetheless not worth using at this list size**, which was the
surprising part:

```
~10,000 names, one CPU available
  1 thread      0.042 ms/lookup
  2 threads     0.055 ms
  4 threads     0.106 ms
```

Spawning and joining a thread costs roughly 45 µs. A full 4,250-candidate scan
costs about 50 µs. Dividing 50 µs of work at 45 µs per division loses, and it
loses by more the harder you try.

The reason the scan is that cheap is gate 1: once a good match is found the
score ceiling rejects nearly every remaining candidate in two arithmetic
operations, so most of the band never reaches the histogram compare, let alone
the DP. The optimisation that made threading unnecessary is the same one that
made the compiled kernel unnecessary.

`mrs_index_best_match` therefore auto-tunes: it stays single-threaded below
40,000 candidates and gives each thread at least 20,000, so a large band is
never sliced into pieces too small to pay for themselves. On a normal NDC list
that resolves to one thread. Pass `thread_count` explicitly to override.

> **Measured on one core.** This container has a single CPU, so the numbers
> above show the spawn *cost* with none of the parallel benefit. They establish
> that threading does not pay at 13,000 candidates — the overhead alone exceeds
> the total work — but the crossover point on a multi-core machine will be lower
> than the 40,000 default assumes. If you are tuning this on your own hardware,
> run `python benchmark_levenshtein.py` and set the constants from what you
> measure rather than trusting the ones shipped here.

## What this bought, honestly

A ~500x faster matching stage, from moving the loop rather than from compiling
the kernel. Matching was 15–35% of request latency, so the end-to-end gain is
bounded near 1.2–1.5x — OCR and the network dominate a scan and always did.

The C build is justified by the requirement to implement the algorithms in a
compiled language, by what the kernel benchmark shows about interpreter
overhead, and by two findings that only appeared because it was measured rather
than assumed: the ctypes marshalling that made C *slower* than Python until it
was fixed, and the gates that made both the compiled kernel and the threading
redundant.

---

