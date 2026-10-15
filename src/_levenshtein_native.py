"""
``ctypes`` binding to the compiled kernel in ``src/clevenshtein/``.

Importing this module never fails and never raises. It either binds the shared
library and sets ``AVAILABLE = True``, or it sets ``AVAILABLE = False`` and
records why in ``UNAVAILABLE_REASON``. ``src/Levenshtein.py`` reads those two
names to pick a backend.

Why ctypes and a plain shared library, rather than a CPython extension module
----------------------------------------------------------------------------
A ``Python.h`` extension would be marginally faster per call, but it has to be
built against the exact interpreter that will import it, needs ``setuptools``
and a wheel, and produces a binary tied to one CPython ABI. This project has to
be cloned onto an unknown machine and demonstrated. A bare ``.so``/``.dll`` is
one ``cc`` command, works for any interpreter on the platform, and binds with a
module that is already in the standard library.

The cost is the marshalling below, and it is a real cost -- see
``benchmark_levenshtein.py``, which measures both backends side by side.

Passing strings across the boundary
-----------------------------------
The C side takes arrays of Unicode **code points**, not bytes, because edit
distance counts characters: over UTF-8 "café" and "cafe" differ by two bytes but
one character. ``str.encode('utf-32-le')`` produces exactly that array in a
single C-level pass, which is far cheaper than building a ctypes array element
by element in a Python loop.

The buffer is declared to ctypes as ``c_char_p`` even though the C prototype
says ``const uint32_t *``. Both are just pointers at the ABI level, and this is
the one declaration for which ctypes hands a ``bytes`` object straight to the
callee with no conversion and no ``ctypes.cast``. Declaring the argument as
``POINTER(c_uint32)`` and casting on every call is more obviously typed and
measured **3.2x slower** on drug-name-length inputs -- enough to erase most of
what the C bought, since the strings are short and the marshalling is a fixed
cost per call rather than a per-character one.

Conversions are cached. ``DrugMatcher.lookup`` scores one query against
thousands of candidate names, and does that once per OCR fragment, so the same
name is converted repeatedly within a single request. The cache turns that into
a dictionary hit and is where a good part of the speed-up actually comes from.
"""

from __future__ import annotations

import ctypes
import sys
from functools import lru_cache
from typing import Optional, Tuple

from .clevenshtein.build import LIBRARY, ensure_built

AVAILABLE: bool = False
UNAVAILABLE_REASON: Optional[str] = None
LIBRARY_PATH: Optional[str] = None
RECURSION_LIMIT: int = 0
CPU_COUNT: int = 1

_ABI_VERSION_EXPECTED = 2

# utf-32 without a BOM, in the platform's native order, so the uint32 values the
# C side reads match the ones Python wrote.
_ENCODING = "utf-32-le" if sys.byteorder == "little" else "utf-32-be"

# See the module docstring: the C prototypes take `const uint32_t *`, but this
# is declared to ctypes as `c_char_p` so that a `bytes` buffer is passed through
# untouched. Same pointer at the ABI level, 3.2x less overhead per call.
_BUFFER = ctypes.c_char_p

_lib = None


def _bind() -> Tuple[bool, Optional[str]]:
    """Load the library and declare every signature. Returns (ok, reason)."""
    global _lib, LIBRARY_PATH, RECURSION_LIMIT

    if not LIBRARY.exists():
        # One attempt to build it. If a compiler is present this makes a fresh
        # clone work on first import even if setup was skipped; if not, this is
        # a couple of `which` calls and we fall through.
        ensure_built(quiet=True)

    if not LIBRARY.exists():
        return False, "compiled library not present (no C compiler at setup time?)"

    try:
        library = ctypes.CDLL(str(LIBRARY))
    except OSError as error:
        return False, f"could not load {LIBRARY.name}: {error}"

    try:
        library.mrs_abi_version.argtypes = []
        library.mrs_abi_version.restype = ctypes.c_int32
        version = library.mrs_abi_version()
    except AttributeError as error:
        return False, f"{LIBRARY.name} is missing expected symbols: {error}"

    if version != _ABI_VERSION_EXPECTED:
        # A stale artefact from an older checkout. Degrade to "slower", never to
        # "wrong" -- the whole point of versioning the ABI.
        return False, (
            f"{LIBRARY.name} reports ABI {version}, expected "
            f"{_ABI_VERSION_EXPECTED}; rebuild with "
            f"`python -m src.clevenshtein.build --force`"
        )

    signatures = {
        "mrs_levenshtein":           ([_BUFFER, ctypes.c_int32,
                                       _BUFFER, ctypes.c_int32,
                                       ctypes.c_int32], ctypes.c_int32),
        "mrs_levenshtein_recursive": ([_BUFFER, ctypes.c_int32,
                                       _BUFFER, ctypes.c_int32],
                                      ctypes.c_int32),
        "mrs_levenshtein_memoized":  ([_BUFFER, ctypes.c_int32,
                                       _BUFFER, ctypes.c_int32],
                                      ctypes.c_int32),
        "mrs_lcs_length":            ([_BUFFER, ctypes.c_int32,
                                       _BUFFER, ctypes.c_int32],
                                      ctypes.c_int32),
        "mrs_indel_distance":        ([_BUFFER, ctypes.c_int32,
                                       _BUFFER, ctypes.c_int32],
                                      ctypes.c_int32),
        "mrs_levenshtein_ratio":     ([_BUFFER, ctypes.c_int32,
                                       _BUFFER, ctypes.c_int32],
                                      ctypes.c_double),
        "mrs_indel_ratio":           ([_BUFFER, ctypes.c_int32,
                                       _BUFFER, ctypes.c_int32],
                                      ctypes.c_double),
        "mrs_ratio":                 ([_BUFFER, ctypes.c_int32,
                                       _BUFFER, ctypes.c_int32,
                                       ctypes.c_double], ctypes.c_int32),
        "mrs_recursion_limit":       ([], ctypes.c_int32),
        "mrs_cpu_count":             ([], ctypes.c_int32),
        # Batch matching. The index is an opaque handle; ctypes must be told
        # the return is a pointer, or it truncates it to a 32-bit int on 64-bit
        # builds and the first use segfaults.
        "mrs_index_create":          ([_BUFFER, ctypes.POINTER(ctypes.c_int32),
                                       ctypes.c_int32], ctypes.c_void_p),
        "mrs_index_free":            ([ctypes.c_void_p], None),
        "mrs_index_count":           ([ctypes.c_void_p], ctypes.c_int32),
        "mrs_index_best_match":      ([ctypes.c_void_p, _BUFFER, ctypes.c_int32,
                                       ctypes.c_double, ctypes.c_int32,
                                       ctypes.c_int32,
                                       ctypes.POINTER(ctypes.c_double),
                                       ctypes.POINTER(ctypes.c_int32),
                                       ctypes.POINTER(ctypes.c_int32)],
                                      ctypes.c_int32),
    }

    for symbol, (argtypes, restype) in signatures.items():
        try:
            function = getattr(library, symbol)
        except AttributeError:
            return False, f"{LIBRARY.name} is missing symbol {symbol}"
        function.argtypes = argtypes
        function.restype = restype

    _lib = library
    LIBRARY_PATH = str(LIBRARY)
    RECURSION_LIMIT = library.mrs_recursion_limit()
    global CPU_COUNT
    CPU_COUNT = library.mrs_cpu_count()
    return True, None


AVAILABLE, UNAVAILABLE_REASON = _bind()


# ---------------------------------------------------------------------------
# Marshalling
# ---------------------------------------------------------------------------

# Sized to comfortably hold a full NDC name list (~13,000 entries) plus the OCR
# fragments of a request, so a warm matcher converts nothing twice.
_CACHE_SIZE = 32768


@lru_cache(maxsize=_CACHE_SIZE)
def _encode(text: str) -> Tuple[bytes, int]:
    """
    A string as a native-order UTF-32 code-point buffer, with its length.

    ``bytes`` is used rather than a ctypes array because it is immutable and
    hashable, so it can be cached safely -- a mutable buffer handed out twice
    could be scribbled on by the C side and corrupt the next caller. Nothing in
    ``levenshtein.c`` writes through these pointers; the immutability is
    belt-and-braces.

    The character count is cached alongside it because the caller needs it on
    every call and ``len()`` on the original string would be a second lookup.
    Note it is the length in *characters*, not in bytes -- the buffer is four
    times longer, and passing the byte count would walk the C code off the end
    of the array.
    """
    return text.encode(_ENCODING), len(text)


def _check(value: int) -> int:
    """Translate the C error codes into Python exceptions."""
    if value == -1:
        raise MemoryError("levenshtein.c: allocation failed")
    if value == -2:
        raise RecursionError(
            "levenshtein.c: input exceeds the naive recursion limit of "
            f"{RECURSION_LIMIT} combined characters"
        )
    return value


def clear_cache() -> None:
    """Drop the code-point cache. Used by the tests; harmless otherwise."""
    _encode.cache_clear()


def cache_info():
    """Hits/misses for the code-point cache, for the benchmark's reporting."""
    return _encode.cache_info()


# ---------------------------------------------------------------------------
# Public functions -- signature-compatible with _levenshtein_python
# ---------------------------------------------------------------------------

def levenshtein_distance(a: str, b: str, max_distance: Optional[int] = None) -> int:
    """Bottom-up two-row DP, in C. See the Python reference for the algorithm."""
    (buffer_a, length_a), (buffer_b, length_b) = _encode(a), _encode(b)
    return _check(_lib.mrs_levenshtein(
        buffer_a, length_a,
        buffer_b, length_b,
        -1 if max_distance is None else max_distance,
    ))


def levenshtein_distance_recursive(a: str, b: str) -> int:
    """
    Naive recursion, in C.

    Raises ``RecursionError`` past the C recursion ceiling, mirroring what the
    Python version does when it hits the interpreter's limit. Compiling an
    exponential algorithm moves the wall, it does not remove it.
    """
    (buffer_a, length_a), (buffer_b, length_b) = _encode(a), _encode(b)
    return _check(_lib.mrs_levenshtein_recursive(
        buffer_a, length_a,
        buffer_b, length_b,
    ))


def levenshtein_distance_memoized(a: str, b: str) -> int:
    """Top-down DP with a memo table, in C."""
    (buffer_a, length_a), (buffer_b, length_b) = _encode(a), _encode(b)
    return _check(_lib.mrs_levenshtein_memoized(
        buffer_a, length_a,
        buffer_b, length_b,
    ))


def levenshtein_ratio(a: str, b: str) -> float:
    (buffer_a, length_a), (buffer_b, length_b) = _encode(a), _encode(b)
    value = _lib.mrs_levenshtein_ratio(
        buffer_a, length_a,
        buffer_b, length_b,
    )
    if value < 0:
        _check(int(value))
    return value


def lcs_length(a: str, b: str) -> int:
    (buffer_a, length_a), (buffer_b, length_b) = _encode(a), _encode(b)
    return _check(_lib.mrs_lcs_length(
        buffer_a, length_a,
        buffer_b, length_b,
    ))


def indel_distance(a: str, b: str) -> int:
    (buffer_a, length_a), (buffer_b, length_b) = _encode(a), _encode(b)
    return _check(_lib.mrs_indel_distance(
        buffer_a, length_a,
        buffer_b, length_b,
    ))


def indel_ratio(a: str, b: str) -> float:
    (buffer_a, length_a), (buffer_b, length_b) = _encode(a), _encode(b)
    value = _lib.mrs_indel_ratio(
        buffer_a, length_a,
        buffer_b, length_b,
    )
    if value < 0:
        _check(int(value))
    return value


def ratio(a: str, b: str, score_cutoff: Optional[float] = None) -> int:
    """
    The project's scorer, in C.

    The whole cutoff decision happens on the C side in one call, so scanning a
    candidate list does not pay Python's per-candidate overhead for candidates
    that are rejected by the cheap bounds.
    """
    if a is None or b is None:
        return 0
    (buffer_a, length_a), (buffer_b, length_b) = _encode(a), _encode(b)
    return _check(_lib.mrs_ratio(
        buffer_a, length_a,
        buffer_b, length_b,
        -1.0 if score_cutoff is None else float(score_cutoff),
    ))


# ---------------------------------------------------------------------------
# Batch matching
# ---------------------------------------------------------------------------

SCORER_LEVENSHTEIN = 0
SCORER_INDEL = 1


class NameIndex:
    """
    A compiled index over a candidate name list, searched in C across threads.

    This is the part that Python cannot do for itself. Scoring one query against
    13,000 names is embarrassingly parallel -- every candidate is independent --
    but Python threads share one interpreter lock, so they cannot run the scan
    concurrently. ``ctypes`` releases the GIL for the duration of a foreign
    call, so the threads spawned inside ``mrs_index_best_match`` really do run
    on separate cores while the calling Python thread waits.

    Build it once and reuse it. ``DrugMatcher`` does that lazily, alongside the
    length and character-count indexes it builds for the Python path.

    The object owns C memory, so it frees it in ``close()`` and ``__del__``.
    Every method is a no-op after close, rather than a use-after-free.
    """

    __slots__ = ("_handle", "_count", "_names", "_thread_count")

    def __init__(self, names, thread_count: int = 0):
        """
        Args:
            names: The candidate strings, in the order callers will index them.
            thread_count: Threads per lookup. 0 means "match the core count,
                capped at 8". 1 forces the sequential scan, which the tests use
                to prove the result does not depend on the thread count.
        """
        self._handle = None
        self._names = list(names)
        self._count = len(self._names)
        self._thread_count = thread_count

        if not AVAILABLE or self._count == 0:
            return

        # One encode per name, concatenated. Building the flat buffer in Python
        # costs one pass; doing it per lookup instead would cost 13,000 encodes
        # per query and undo the whole point.
        lengths = (ctypes.c_int32 * self._count)()
        chunks = []
        for position, name in enumerate(self._names):
            chunks.append(name.encode(_ENCODING))
            lengths[position] = len(name)
        flat = b"".join(chunks)

        handle = _lib.mrs_index_create(flat, lengths, self._count)
        if handle:
            self._handle = handle

    @property
    def available(self) -> bool:
        """False when the library is missing or the index could not be built."""
        return self._handle is not None

    def best_match(self, query: str, threshold: float,
                   scorer: int = SCORER_LEVENSHTEIN):
        """
        Best-scoring name above ``threshold``, or ``('', 0.0)`` if there is none.

        Returns:
            ``(name, score, scored, examined)`` where ``scored`` is how many
            candidates reached the dynamic program and ``examined`` how many the
            gates looked at. Those two are returned rather than computed
            separately so the pruning claims in the README stay measured.
        """
        if self._handle is None or not query:
            return "", 0.0, 0, 0

        buffer, length = _encode(query)
        score = ctypes.c_double(0.0)
        scored = ctypes.c_int32(0)
        examined = ctypes.c_int32(0)

        position = _lib.mrs_index_best_match(
            self._handle, buffer, length,
            float(threshold), int(scorer), int(self._thread_count),
            ctypes.byref(score), ctypes.byref(scored), ctypes.byref(examined),
        )

        if position < 0:
            return "", 0.0, scored.value, examined.value
        return self._names[position], score.value, scored.value, examined.value

    def close(self) -> None:
        """Release the C index. Safe to call more than once."""
        if self._handle is not None:
            _lib.mrs_index_free(self._handle)
            self._handle = None

    def __del__(self):
        # Guard the attribute: if __init__ raised before setting _handle, the
        # interpreter still calls __del__.
        try:
            self.close()
        except Exception:
            pass

    def __len__(self) -> int:
        return self._count
