/*
 * levenshtein.c -- edit-distance kernels for the Medicine Recognition System.
 *
 * This is the compiled half of `src/Levenshtein.py`. It implements exactly the
 * same three approaches as the Python module -- naive recursion, top-down
 * memoisation, and bottom-up two-row dynamic programming -- plus the indel
 * (LCS) distance and the `ratio()` scorer with its cutoff logic.
 *
 * WHY C
 * -----
 * The Python versions stay in the repository and remain the reference: the test
 * suite runs both and asserts they agree on every input. The C build exists
 * because matching one OCR fragment scores it against thousands of NDC names,
 * and the inner loop of a DP table is exactly the kind of work an interpreter
 * is worst at -- every cell costs a bytecode dispatch, three integer boxes and
 * a `min()` call.
 *
 * STRINGS
 * -------
 * Every entry point takes `const uint32_t *` -- an array of Unicode code
 * points -- and not `char *`. Two reasons:
 *
 *   1. Edit distance must count characters, not bytes. Over UTF-8, "café" and
 *      "cafe" would differ by 2 bytes but only 1 character, and any non-ASCII
 *      drug name would silently score wrong.
 *   2. Python can hand us a code-point buffer for free: `str.encode('utf-32-le')`
 *      is a single C-level memcpy, whereas building a char array element by
 *      element in Python would cost more than the distance computation saves.
 *
 * The caller owns the buffers; nothing here retains a pointer past return.
 *
 * ERRORS
 * ------
 * Negative return values are errors, never distances:
 *
 *   MRS_ERR_ALLOC     (-1)  malloc failed
 *   MRS_ERR_TOO_DEEP  (-2)  recursive variant refused an input that would
 *                           overflow the C stack (mirrors Python's
 *                           RecursionError, which the binding re-raises)
 *
 * The API is deliberately flat -- plain integers and pointers, no structs and
 * no callbacks -- so it can be bound with `ctypes` from the standard library
 * and needs no build step at Python's end.
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

/*
 * Threads. The batch matcher at the bottom of this file scans a candidate list
 * in parallel; everything above it is single-threaded and reentrant.
 *
 * C11 <threads.h> would be the portable choice on paper, but MSVC still does
 * not ship it, so this uses the two native APIs directly. Both are wrapped
 * behind mrs_thread_* so the matcher itself contains no #ifdef.
 */
#if defined(_WIN32) || defined(_WIN64)
#  include <windows.h>
   typedef HANDLE mrs_thread;
#else
#  include <pthread.h>
#  include <unistd.h>
   typedef pthread_t mrs_thread;
#endif

/* ------------------------------------------------------------------------ */
/* Export macro                                                              */
/* ------------------------------------------------------------------------ */

#if defined(_WIN32) || defined(_WIN64)
#  define MRS_API __declspec(dllexport)
#else
#  define MRS_API __attribute__((visibility("default")))
#endif

#define MRS_ERR_ALLOC     (-1)
#define MRS_ERR_TOO_DEEP  (-2)

/*
 * Ceiling on the naive recursion. The recursion tree is O(3^n) and its depth is
 * la + lb, so anything past a couple of dozen characters is both unusable and a
 * stack hazard. Refusing loudly beats crashing the interpreter.
 */
#define MRS_RECURSION_LIMIT 46

#define MRS_ABI_VERSION 2

static int32_t mrs_min3(int32_t a, int32_t b, int32_t c)
{
    int32_t m = a < b ? a : b;
    return m < c ? m : c;
}

/* ------------------------------------------------------------------------ */
/* Shared prefix/suffix trimming                                             */
/* ------------------------------------------------------------------------ */

/*
 * Advance both pointers past the common prefix and shorten both lengths past
 * the common suffix.
 *
 * A leading or trailing run of identical characters can never appear in an
 * optimal edit script, so dropping it leaves the distance unchanged while
 * shrinking the DP table. This is an exact optimisation, not an approximation
 * -- the same one `_trim_common_affixes` performs in the Python module.
 */
static void mrs_trim_affixes(const uint32_t **a, int32_t *la,
                             const uint32_t **b, int32_t *lb)
{
    int32_t start = 0;
    int32_t end = 0;
    int32_t limit;

    if (*la == 0 || *lb == 0) {
        return;
    }

    limit = (*la < *lb) ? *la : *lb;

    while (start < limit && (*a)[start] == (*b)[start]) {
        start++;
    }

    /* The suffix scan must never cross back over the prefix already consumed. */
    limit -= start;
    while (end < limit &&
           (*a)[*la - 1 - end] == (*b)[*lb - 1 - end]) {
        end++;
    }

    *a += start;
    *b += start;
    *la -= (start + end);
    *lb -= (start + end);
}

/* ------------------------------------------------------------------------ */
/* Approach 1: naive recursion -- O(3^n) time                                */
/* ------------------------------------------------------------------------ */

static int32_t mrs_recursive(const uint32_t *a, int32_t la,
                             const uint32_t *b, int32_t lb)
{
    int32_t deletion, insertion, substitution;

    if (la == 0) return lb;
    if (lb == 0) return la;

    if (a[la - 1] == b[lb - 1]) {
        return mrs_recursive(a, la - 1, b, lb - 1);
    }

    deletion     = mrs_recursive(a, la - 1, b, lb);
    insertion    = mrs_recursive(a, la, b, lb - 1);
    substitution = mrs_recursive(a, la - 1, b, lb - 1);

    return 1 + mrs_min3(deletion, insertion, substitution);
}

/*
 * Educational only. Kept so the benchmark can show the cost of re-solving
 * identical subproblems in a language where function calls are cheap -- the
 * exponential blow-up is a property of the algorithm, and compiling it does not
 * rescue it.
 */
MRS_API int32_t mrs_levenshtein_recursive(const uint32_t *a, int32_t la,
                                          const uint32_t *b, int32_t lb)
{
    if (la + lb > MRS_RECURSION_LIMIT) {
        return MRS_ERR_TOO_DEEP;
    }
    return mrs_recursive(a, la, b, lb);
}

/* ------------------------------------------------------------------------ */
/* Approach 2: top-down DP -- the same recursion plus a memo table            */
/* ------------------------------------------------------------------------ */

/*
 * `memo` is a (la + 1) x (lb + 1) grid of int32, -1 meaning "not yet solved".
 * Solving each (i, j) once collapses the exponential time to O(la * lb), but
 * every solved cell is retained, so memory is O(la * lb) too -- the price of
 * keeping the recursive shape. That trade-off is the whole point of having this
 * variant next to the iterative one.
 */
static int32_t mrs_memo_solve(const uint32_t *a, const uint32_t *b,
                              int32_t *memo, int32_t stride,
                              int32_t i, int32_t j)
{
    int32_t *cell;
    int32_t result;

    if (i == 0) return j;
    if (j == 0) return i;

    cell = &memo[(size_t)i * stride + j];
    if (*cell >= 0) {
        return *cell;
    }

    if (a[i - 1] == b[j - 1]) {
        result = mrs_memo_solve(a, b, memo, stride, i - 1, j - 1);
    } else {
        result = 1 + mrs_min3(
            mrs_memo_solve(a, b, memo, stride, i - 1, j),      /* deletion */
            mrs_memo_solve(a, b, memo, stride, i, j - 1),      /* insertion */
            mrs_memo_solve(a, b, memo, stride, i - 1, j - 1)); /* substitution */
    }

    *cell = result;
    return result;
}

MRS_API int32_t mrs_levenshtein_memoized(const uint32_t *a, int32_t la,
                                         const uint32_t *b, int32_t lb)
{
    int32_t *memo;
    int32_t stride = lb + 1;
    int32_t result;
    int32_t i;
    size_t cells;

    if (la == 0) return lb;
    if (lb == 0) return la;

    cells = (size_t)(la + 1) * (size_t)stride;
    memo = (int32_t *)malloc(cells * sizeof(int32_t));
    if (memo == NULL) {
        return MRS_ERR_ALLOC;
    }
    memset(memo, 0xFF, cells * sizeof(int32_t));  /* every cell = -1 */

    /*
     * Warm the cache down one axis first. Without this the recursion depth is
     * la + lb; with it, the deepest chain is bounded by a single dimension,
     * which is what keeps long inputs off the C stack. The Python version does
     * the same thing for the same reason.
     */
    for (i = 0; i <= la; i++) {
        (void)mrs_memo_solve(a, b, memo, stride, i, 0);
    }

    result = mrs_memo_solve(a, b, memo, stride, la, lb);
    free(memo);
    return result;
}

/* ------------------------------------------------------------------------ */
/* Approach 3: bottom-up two-row DP -- the one the pipeline uses              */
/* ------------------------------------------------------------------------ */

/*
 * Same O(la * lb) time as the memoised version, but O(min(la, lb)) space and no
 * recursion at all: computing row j only ever needs row j - 1.
 *
 * `max_distance` is an optional cutoff. Pass a negative value for none. When
 * supplied, the function may return `max_distance + 1` instead of the exact
 * distance -- a lower bound, not an answer, so callers must not turn it into a
 * score. `ratio()` checks for exactly this.
 */
MRS_API int32_t mrs_levenshtein(const uint32_t *a, int32_t la,
                                const uint32_t *b, int32_t lb,
                                int32_t max_distance)
{
    int32_t *base;      /* the allocation itself -- the only pointer we free */
    int32_t *previous;
    int32_t *current;
    int32_t *swap_ptr;
    const uint32_t *tmp;
    int32_t i, j, swap, result, row_min;
    int has_cutoff = (max_distance >= 0);

    if (la == lb && (la == 0 || memcmp(a, b, (size_t)la * sizeof(uint32_t)) == 0)) {
        return 0;
    }

    mrs_trim_affixes(&a, &la, &b, &lb);

    /* Keep the shorter string on the inner axis, so the rows stay small. */
    if (la > lb) {
        tmp = a; a = b; b = tmp;
        swap = la; la = lb; lb = swap;
    }

    if (la == 0) {
        /* Nothing left to align: only insertions remain. */
        if (has_cutoff && lb > max_distance) return max_distance + 1;
        return lb;
    }

    /* The distance is at least the length difference, so bail out cheaply. */
    if (has_cutoff && (lb - la) > max_distance) {
        return max_distance + 1;
    }

    base = (int32_t *)malloc((size_t)(la + 1) * 2 * sizeof(int32_t));
    if (base == NULL) {
        return MRS_ERR_ALLOC;
    }
    previous = base;
    current  = base + (la + 1);

    /* previous[i] = distance between a[:i] and the b-prefix seen so far. */
    for (i = 0; i <= la; i++) {
        previous[i] = i;
    }

    for (j = 1; j <= lb; j++) {
        uint32_t char_b = b[j - 1];
        current[0] = j;
        row_min = j;

        for (i = 1; i <= la; i++) {
            int32_t substitution_cost = (a[i - 1] == char_b) ? 0 : 1;
            int32_t value = mrs_min3(
                previous[i] + 1,                       /* deletion */
                current[i - 1] + 1,                    /* insertion */
                previous[i - 1] + substitution_cost);  /* substitution / match */
            current[i] = value;
            if (value < row_min) row_min = value;
        }

        /*
         * Every distance still reachable is at least the row minimum, so once
         * the whole row is past the cutoff the answer cannot come back under it.
         */
        if (has_cutoff && row_min > max_distance) {
            free(base);
            return max_distance + 1;
        }

        swap_ptr = previous; previous = current; current = swap_ptr;
    }

    result = previous[la];
    free(base);
    return result;
}

/* ------------------------------------------------------------------------ */
/* Indel distance (insertions and deletions only)                            */
/* ------------------------------------------------------------------------ */

MRS_API int32_t mrs_lcs_length(const uint32_t *a, int32_t la,
                               const uint32_t *b, int32_t lb)
{
    int32_t *scratch, *previous, *current, *swap_ptr;
    const uint32_t *tmp;
    int32_t i, j, swap, result;

    if (la == 0 || lb == 0) return 0;

    if (la > lb) {
        tmp = a; a = b; b = tmp;
        swap = la; la = lb; lb = swap;
    }

    scratch = (int32_t *)calloc((size_t)(la + 1) * 2, sizeof(int32_t));
    if (scratch == NULL) return MRS_ERR_ALLOC;
    previous = scratch;
    current  = scratch + (la + 1);

    for (j = 1; j <= lb; j++) {
        uint32_t char_b = b[j - 1];
        current[0] = 0;
        for (i = 1; i <= la; i++) {
            if (a[i - 1] == char_b) {
                current[i] = previous[i - 1] + 1;
            } else {
                current[i] = previous[i] > current[i - 1]
                           ? previous[i] : current[i - 1];
            }
        }
        swap_ptr = previous; previous = current; current = swap_ptr;
    }

    result = previous[la];
    free(scratch);
    return result;
}

/*
 * Every character outside the Longest Common Subsequence has to be deleted from
 * one string or inserted into the other:
 *
 *     indel = la + lb - 2 * LCS(a, b)
 *
 * This is the metric `thefuzz.fuzz.ratio` actually computed, kept so the
 * project's claim about the old library stays verifiable in the tests. Nothing
 * in the pipeline scores with it.
 */
MRS_API int32_t mrs_indel_distance(const uint32_t *a, int32_t la,
                                   const uint32_t *b, int32_t lb)
{
    int32_t lcs;

    if (la == lb && (la == 0 || memcmp(a, b, (size_t)la * sizeof(uint32_t)) == 0)) {
        return 0;
    }

    mrs_trim_affixes(&a, &la, &b, &lb);

    if (la == 0) return lb;
    if (lb == 0) return la;

    lcs = mrs_lcs_length(a, la, b, lb);
    if (lcs < 0) return lcs;

    return la + lb - 2 * lcs;
}

/* ------------------------------------------------------------------------ */
/* Normalised scorers                                                        */
/* ------------------------------------------------------------------------ */

MRS_API double mrs_levenshtein_ratio(const uint32_t *a, int32_t la,
                                     const uint32_t *b, int32_t lb)
{
    int32_t longest = (la > lb) ? la : lb;
    int32_t distance;

    if (longest == 0) return 100.0;

    distance = mrs_levenshtein(a, la, b, lb, -1);
    if (distance < 0) return (double)distance;

    return (1.0 - (double)distance / (double)longest) * 100.0;
}

MRS_API double mrs_indel_ratio(const uint32_t *a, int32_t la,
                               const uint32_t *b, int32_t lb)
{
    int32_t total = la + lb;
    int32_t distance;

    if (total == 0) return 100.0;

    distance = mrs_indel_distance(a, la, b, lb);
    if (distance < 0) return (double)distance;

    return (1.0 - (double)distance / (double)total) * 100.0;
}

/*
 * The project's scorer: classic Levenshtein similarity from 0 to 100.
 *
 * `score_cutoff` is optional -- pass a negative value for none. When supplied,
 * two exact short-circuits fire before the DP runs, and both only ever discard
 * candidates that provably could not have cleared the cutoff:
 *
 *   1. The distance is at least the length difference, so the best score two
 *      strings can reach is min/max * 100. If that ceiling misses the cutoff,
 *      return 0 without touching the DP.
 *   2. Otherwise translate the score cutoff into a distance cutoff and let the
 *      DP abandon the comparison part-way through its rows.
 *
 * The 1e-9 epsilon is not cosmetic. `longest * (100 - cutoff) / 100` can land a
 * hair under an exact integer in binary floating point and truncate to one
 * distance too few, which would reject genuine matches. This mirrors the Python
 * implementation byte for byte, because the two must agree on every input.
 *
 * Rounding uses nearbyint() under the default FE_TONEAREST mode, which is
 * round-half-to-even -- the same tie-breaking as Python's built-in round(), and
 * therefore the same as the behaviour `thefuzz` had.
 */
MRS_API int32_t mrs_ratio(const uint32_t *a, int32_t la,
                          const uint32_t *b, int32_t lb,
                          double score_cutoff)
{
    int32_t longest = (la > lb) ? la : lb;
    int32_t shortest = (la < lb) ? la : lb;
    int32_t distance, max_distance;
    double score, best_possible;
    int has_cutoff = (score_cutoff >= 0.0);

    if (longest == 0) return 100;

    if (has_cutoff) {
        best_possible = ((double)shortest / (double)longest) * 100.0;
        if (best_possible < score_cutoff) {
            return 0;
        }

        max_distance = (int32_t)((double)longest * (100.0 - score_cutoff)
                                 / 100.0 + 1e-9);
        distance = mrs_levenshtein(a, la, b, lb, max_distance);
        if (distance < 0) return distance;

        /* Past the cutoff the DP returns a lower bound, not the true distance,
           so it must not be turned into a score. */
        if (distance > max_distance) {
            return 0;
        }
    } else {
        distance = mrs_levenshtein(a, la, b, lb, -1);
        if (distance < 0) return distance;
    }

    score = (1.0 - (double)distance / (double)longest) * 100.0;

    if (has_cutoff && score < score_cutoff) {
        return 0;
    }

    return (int32_t)nearbyint(score);
}

/* ------------------------------------------------------------------------ */
/* Introspection                                                             */
/* ------------------------------------------------------------------------ */

/*
 * Bumped whenever a signature above changes. The ctypes binding checks it on
 * load and falls back to pure Python on a mismatch, so a stale .so left over
 * from an older checkout degrades into "slower" rather than "wrong".
 */
MRS_API int32_t mrs_abi_version(void)
{
    return MRS_ABI_VERSION;
}

/* Exposed so the binding can report the recursion ceiling rather than hardcode
   a copy of it that could drift out of sync. */
MRS_API int32_t mrs_recursion_limit(void)
{
    return MRS_RECURSION_LIMIT;
}

/* ========================================================================== */
/* Parallel batch matching                                                    */
/* ========================================================================== */
/*
 * Everything above computes ONE distance. This section computes the whole
 * lookup: given a query and a pre-built index of candidate names, find the
 * best-scoring name above a threshold.
 *
 * WHY THIS EXISTS
 * ---------------
 * Calling mrs_ratio() from a Python loop makes the distance fast and leaves the
 * loop slow. Measured on a 13,000-name list, the three pruning gates reject all
 * but ~18 candidates, so the distance function is called 18 times while the
 * gate arithmetic runs 4,250 times -- in Python. Compiling the callee cannot
 * fix a bottleneck that lives in the caller.
 *
 * So the gates move here too, and once the whole scan is in C it can be split
 * across threads. That is the part Python genuinely cannot do: the GIL
 * serialises Python-level threads, whereas ctypes releases the GIL for the
 * duration of a foreign call, so the threads below run truly concurrently.
 *
 * FIDELITY
 * --------
 * This must return exactly what DrugMatcher.lookup() returns, including which
 * name wins a tie. Two details make that work:
 *
 *   - Names are stored sorted by (length, original position), and the scan
 *     walks the same contiguous length band DrugMatcher._candidates() builds,
 *     in the same order.
 *   - Each thread keeps its own best, and the merge prefers the LOWEST sorted
 *     position on an equal score. Sequential "first strictly-better wins" and
 *     parallel "lowest position wins" then agree, which keeps the result
 *     independent of the thread count.
 *
 * tests/test_levenshtein_native.py asserts that against the Python matcher.
 */

#define MRS_HIST      64    /* character-histogram buckets */
#define MRS_STACK_LEN 256   /* DP rows this long live on the stack */
/*
 * Candidates below which the scan stays on the calling thread.
 *
 * Measured, not guessed. Spawning and joining one thread costs roughly 45 us
 * here; a 4,250-candidate band takes about 50 us to scan in full. Threading
 * that band therefore made lookups two to four times SLOWER, because the
 * spawn cost is paid per thread while the work being divided is smaller than
 * one spawn.
 *
 * Gate 1 is what makes the scan so cheap: once a good match is found the score
 * ceiling rejects almost every remaining candidate in two arithmetic
 * operations, so most of the band never reaches the histogram compare, let
 * alone the DP.
 *
 * The number below is that break-even point with an order of magnitude of
 * headroom. Each thread must also get at least MRS_MIN_PER_THREAD candidates,
 * so a merely-large band does not get split into slices too small to pay for
 * themselves. On a typical NDC list this means the scan runs single-threaded,
 * which is the correct answer -- the parallel path is there for candidate lists
 * an order of magnitude larger than the one this project ships with.
 */
#define MRS_MIN_PARALLEL    40000
#define MRS_MIN_PER_THREAD  20000

#define MRS_SCORER_LEVENSHTEIN 0
#define MRS_SCORER_INDEL       1

typedef struct {
    uint32_t *data;      /* every name's code points, concatenated */
    int32_t  *offset;    /* where each name starts in `data` */
    int32_t  *length;    /* each name's length in characters */
    int32_t  *origin;    /* sorted position -> caller's original index */
    uint8_t  *histogram; /* MRS_HIST bytes per name */
    int32_t  *bucket_start;  /* first sorted position with this length */
    int32_t  *bucket_count;  /* how many names have this length */
    int32_t   max_length;
    int32_t   count;
} mrs_index;

/*
 * Bucket a code point into the histogram.
 *
 * Folding the whole of Unicode into 64 buckets makes different characters
 * collide, which can only make the computed difference SMALLER than the true
 * one. The result is therefore a weaker lower bound, never an invalid one --
 * the gate can still only discard candidates that provably could not win.
 * Sixty-four buckets keep the per-candidate compare inside a couple of cache
 * lines, which is what makes it cheaper than the DP it replaces.
 */
static int mrs_bucket(uint32_t code_point)
{
    return (int)(code_point & (MRS_HIST - 1));
}

static void mrs_histogram(const uint32_t *text, int32_t length, uint8_t *out)
{
    int32_t i;
    memset(out, 0, MRS_HIST);
    for (i = 0; i < length; i++) {
        int bucket = mrs_bucket(text[i]);
        if (out[bucket] < 255) out[bucket]++;   /* saturate; still a bound */
    }
}

/* Sort key: length first, then the caller's original order. */
typedef struct { int32_t length; int32_t origin; } mrs_sort_entry;

static int mrs_compare_entries(const void *left, const void *right)
{
    const mrs_sort_entry *a = (const mrs_sort_entry *)left;
    const mrs_sort_entry *b = (const mrs_sort_entry *)right;
    if (a->length != b->length) return (a->length < b->length) ? -1 : 1;
    return (a->origin < b->origin) ? -1 : (a->origin > b->origin);
}

/*
 * Build the index once, reuse it for every lookup.
 *
 * `flat` is every name's code points concatenated, `lengths` their character
 * counts. Python builds both with one encode() per name, which is why the
 * binding can hand 13,000 names over in a single call.
 *
 * Returns NULL on allocation failure; the binding treats that as "fall back".
 */
MRS_API mrs_index *mrs_index_create(const uint32_t *flat,
                                    const int32_t *lengths,
                                    int32_t count)
{
    mrs_index *index;
    mrs_sort_entry *entries = NULL;
    int32_t *source_offset = NULL;
    int32_t i, total = 0, cursor = 0, max_length = 0;

    if (count <= 0) return NULL;

    index = (mrs_index *)calloc(1, sizeof(mrs_index));
    if (index == NULL) return NULL;

    entries = (mrs_sort_entry *)malloc((size_t)count * sizeof(mrs_sort_entry));
    source_offset = (int32_t *)malloc((size_t)count * sizeof(int32_t));
    if (entries == NULL || source_offset == NULL) goto failure;

    for (i = 0; i < count; i++) {
        source_offset[i] = total;
        total += lengths[i];
        if (lengths[i] > max_length) max_length = lengths[i];
        entries[i].length = lengths[i];
        entries[i].origin = i;
    }

    qsort(entries, (size_t)count, sizeof(mrs_sort_entry), mrs_compare_entries);

    index->count = count;
    index->max_length = max_length;
    index->data      = (uint32_t *)malloc((size_t)(total ? total : 1) * sizeof(uint32_t));
    index->offset    = (int32_t *)malloc((size_t)count * sizeof(int32_t));
    index->length    = (int32_t *)malloc((size_t)count * sizeof(int32_t));
    index->origin    = (int32_t *)malloc((size_t)count * sizeof(int32_t));
    index->histogram = (uint8_t *)malloc((size_t)count * MRS_HIST);
    index->bucket_start = (int32_t *)malloc((size_t)(max_length + 2) * sizeof(int32_t));
    index->bucket_count = (int32_t *)calloc((size_t)(max_length + 2), sizeof(int32_t));

    if (!index->data || !index->offset || !index->length || !index->origin ||
        !index->histogram || !index->bucket_start || !index->bucket_count) {
        goto failure;
    }

    /* Copy the names into sorted order so a length band is contiguous, which
       is what lets a scan be a flat loop rather than a gather. */
    for (i = 0; i < count; i++) {
        int32_t origin = entries[i].origin;
        int32_t length = entries[i].length;
        index->offset[i] = cursor;
        index->length[i] = length;
        index->origin[i] = origin;
        if (length > 0) {
            memcpy(index->data + cursor, flat + source_offset[origin],
                   (size_t)length * sizeof(uint32_t));
        }
        mrs_histogram(index->data + cursor, length,
                      index->histogram + (size_t)i * MRS_HIST);
        cursor += length;
    }

    for (i = 0; i <= max_length + 1; i++) index->bucket_start[i] = -1;
    for (i = 0; i < count; i++) {
        int32_t length = index->length[i];
        if (index->bucket_start[length] < 0) index->bucket_start[length] = i;
        index->bucket_count[length]++;
    }

    free(entries);
    free(source_offset);
    return index;

failure:
    free(entries);
    free(source_offset);
    if (index) {
        free(index->data); free(index->offset); free(index->length);
        free(index->origin); free(index->histogram);
        free(index->bucket_start); free(index->bucket_count);
        free(index);
    }
    return NULL;
}

MRS_API void mrs_index_free(mrs_index *index)
{
    if (index == NULL) return;
    free(index->data); free(index->offset); free(index->length);
    free(index->origin); free(index->histogram);
    free(index->bucket_start); free(index->bucket_count);
    free(index);
}

MRS_API int32_t mrs_index_count(const mrs_index *index)
{
    return index ? index->count : 0;
}

/* Distance with caller-supplied scratch rows, so the hot loop never mallocs. */
static int32_t mrs_lev_scratch(const uint32_t *a, int32_t la,
                               const uint32_t *b, int32_t lb,
                               int32_t max_distance,
                               int32_t *previous, int32_t *current)
{
    const uint32_t *tmp;
    int32_t i, j, swap, row_min;
    int32_t *swap_row;

    if (la == lb && (la == 0 || memcmp(a, b, (size_t)la * sizeof(uint32_t)) == 0))
        return 0;

    mrs_trim_affixes(&a, &la, &b, &lb);

    if (la > lb) {
        tmp = a; a = b; b = tmp;
        swap = la; la = lb; lb = swap;
    }

    if (la == 0) return (lb > max_distance) ? max_distance + 1 : lb;
    if ((lb - la) > max_distance) return max_distance + 1;

    for (i = 0; i <= la; i++) previous[i] = i;

    for (j = 1; j <= lb; j++) {
        uint32_t char_b = b[j - 1];
        current[0] = j;
        row_min = j;
        for (i = 1; i <= la; i++) {
            int32_t cost = (a[i - 1] == char_b) ? 0 : 1;
            int32_t value = mrs_min3(previous[i] + 1,
                                     current[i - 1] + 1,
                                     previous[i - 1] + cost);
            current[i] = value;
            if (value < row_min) row_min = value;
        }
        if (row_min > max_distance) return max_distance + 1;
        swap_row = previous; previous = current; current = swap_row;
    }

    return previous[la];
}

/* LCS with caller-supplied scratch, for the indel scorer. */
static int32_t mrs_lcs_scratch(const uint32_t *a, int32_t la,
                               const uint32_t *b, int32_t lb,
                               int32_t *previous, int32_t *current)
{
    const uint32_t *tmp;
    int32_t i, j, swap;
    int32_t *swap_row;

    if (la == 0 || lb == 0) return 0;
    if (la > lb) {
        tmp = a; a = b; b = tmp;
        swap = la; la = lb; lb = swap;
    }

    for (i = 0; i <= la; i++) previous[i] = 0;

    for (j = 1; j <= lb; j++) {
        uint32_t char_b = b[j - 1];
        current[0] = 0;
        for (i = 1; i <= la; i++) {
            if (a[i - 1] == char_b) current[i] = previous[i - 1] + 1;
            else current[i] = previous[i] > current[i - 1]
                            ? previous[i] : current[i - 1];
        }
        swap_row = previous; previous = current; current = swap_row;
    }
    return previous[la];
}

typedef struct {
    const mrs_index *index;
    const uint32_t  *query;
    int32_t          query_length;
    const uint8_t   *query_histogram;
    double           threshold;
    int32_t          scorer;
    int32_t          first;      /* sorted positions [first, last) */
    int32_t          last;
    int32_t          best_position;
    double           best_score;
    int32_t          scored;     /* candidates that reached the DP */
    int32_t          examined;   /* candidates the gates looked at */
} mrs_task;

/*
 * The scan, for one slice of the candidate range.
 *
 * The gate order mirrors DrugMatcher.lookup() exactly -- score ceiling, then
 * histogram bound, then the DP -- because the gates are what make this fast and
 * changing their order would change how many candidates reach the DP.
 */
static void mrs_scan(mrs_task *task)
{
    const mrs_index *index = task->index;
    int32_t previous[MRS_STACK_LEN + 1];
    int32_t current[MRS_STACK_LEN + 1];
    int32_t position;

    for (position = task->first; position < task->last; position++) {
        int32_t name_length = index->length[position];
        const uint32_t *name = index->data + index->offset[position];
        const uint8_t *name_histogram =
            index->histogram + (size_t)position * MRS_HIST;
        double ceiling, span, bar, limit, score;
        int32_t difference = 0, bound, distance, max_distance, k;

        task->examined++;

        /* Gate 1: the distance is at least the length difference, which caps
           the achievable score. The bar rises as the best score improves, so
           later candidates are rejected more aggressively than earlier ones. */
        if (task->scorer == MRS_SCORER_LEVENSHTEIN) {
            int32_t longest  = task->query_length > name_length
                             ? task->query_length : name_length;
            int32_t shortest = task->query_length < name_length
                             ? task->query_length : name_length;
            if (longest == 0) continue;
            ceiling = ((double)shortest / (double)longest) * 100.0;
            span = (double)longest;
        } else {
            int32_t total = task->query_length + name_length;
            int32_t delta = task->query_length - name_length;
            if (delta < 0) delta = -delta;
            if (total == 0) continue;
            ceiling = (1.0 - (double)delta / (double)total) * 100.0;
            span = (double)total;
        }

        bar = task->best_score > task->threshold
            ? task->best_score : task->threshold;
        if (ceiling <= bar) continue;

        /* Gate 2: the character-count bound. One Levenshtein edit changes at
           most two bucket counts, so at least ceil(difference / 2) edits are
           needed; indel has no substitution, so each unit needs its own edit.
           The 1e-9 absorbs binary-float error at the threshold, exactly as the
           Python gate does -- see the note in DrugMatcher. */
        for (k = 0; k < MRS_HIST; k++) {
            int delta = (int)task->query_histogram[k] - (int)name_histogram[k];
            difference += (delta < 0) ? -delta : delta;
        }
        bound = (task->scorer == MRS_SCORER_LEVENSHTEIN)
              ? (difference + 1) / 2
              : difference;

        limit = span * (1.0 - bar / 100.0) + 1e-9;
        if ((double)bound > limit) continue;

        /* Gate 3: the real thing. */
        task->scored++;
        max_distance = (int32_t)limit;

        if (task->scorer == MRS_SCORER_LEVENSHTEIN) {
            if (name_length > MRS_STACK_LEN || task->query_length > MRS_STACK_LEN) {
                distance = mrs_levenshtein(task->query, task->query_length,
                                           name, name_length, -1);
            } else {
                distance = mrs_lev_scratch(task->query, task->query_length,
                                           name, name_length,
                                           max_distance, previous, current);
                if (distance > max_distance) continue;  /* a bound, not a value */
            }
            if (distance < 0) continue;
            score = (1.0 - (double)distance / span) * 100.0;
        } else {
            int32_t lcs;
            if (name_length > MRS_STACK_LEN || task->query_length > MRS_STACK_LEN) {
                distance = mrs_indel_distance(task->query, task->query_length,
                                              name, name_length);
            } else {
                lcs = mrs_lcs_scratch(task->query, task->query_length,
                                      name, name_length, previous, current);
                distance = task->query_length + name_length - 2 * lcs;
            }
            if (distance < 0) continue;
            score = (1.0 - (double)distance / span) * 100.0;
        }

        if (score > task->threshold && score > task->best_score) {
            task->best_score = score;
            task->best_position = position;
        }
    }
}

#if defined(_WIN32) || defined(_WIN64)
static DWORD WINAPI mrs_worker(LPVOID argument)
{
    mrs_scan((mrs_task *)argument);
    return 0;
}
#else
static void *mrs_worker(void *argument)
{
    mrs_scan((mrs_task *)argument);
    return NULL;
}
#endif

/* Available cores, used when the caller passes thread_count <= 0. */
MRS_API int32_t mrs_cpu_count(void)
{
#if defined(_WIN32) || defined(_WIN64)
    SYSTEM_INFO info;
    GetSystemInfo(&info);
    return (int32_t)info.dwNumberOfProcessors;
#else
    long count = sysconf(_SC_NPROCESSORS_ONLN);
    return count > 0 ? (int32_t)count : 1;
#endif
}

/*
 * Find the best-matching name for one query.
 *
 * Returns the caller's ORIGINAL index of the winner, or -1 if nothing cleared
 * the threshold. `out_score` receives the winning score, `out_scored` and
 * `out_examined` the gate statistics -- the ratio between those two is what
 * the README's pruning table reports, and having C report it means the claim is
 * measured rather than asserted.
 *
 * thread_count <= 0 means "decide automatically". Below MRS_MIN_PARALLEL
 * candidates the scan stays on the calling thread: spawning costs tens of
 * microseconds and a short band is not worth it.
 */
MRS_API int32_t mrs_index_best_match(const mrs_index *index,
                                     const uint32_t *query, int32_t query_length,
                                     double threshold, int32_t scorer,
                                     int32_t thread_count,
                                     double *out_score,
                                     int32_t *out_scored,
                                     int32_t *out_examined)
{
    uint8_t query_histogram[MRS_HIST];
    mrs_task *tasks = NULL;
    mrs_thread *threads = NULL;
    int32_t lowest, highest, first = -1, last = -1, length;
    int32_t span_count, i, started = 0;
    int32_t best_position = -1;
    double best_score = 0.0;
    int32_t scored = 0, examined = 0;

    if (out_score) *out_score = 0.0;
    if (out_scored) *out_scored = 0;
    if (out_examined) *out_examined = 0;
    if (index == NULL || query_length <= 0) return -1;

    /* The length band, computed exactly as DrugMatcher._candidates() does. */
    if (threshold <= 0.0) {
        lowest = 1;
        highest = index->max_length;
    } else if (scorer == MRS_SCORER_LEVENSHTEIN) {
        double ratio_bound = threshold / 100.0;
        lowest  = (int32_t)(ratio_bound * query_length);
        highest = (int32_t)(query_length / ratio_bound) + 1;
    } else {
        double fraction = (100.0 - threshold) / 100.0;
        if (fraction >= 1.0) {
            lowest = 1; highest = index->max_length;
        } else {
            lowest  = (int32_t)((1.0 - fraction) / (1.0 + fraction) * query_length);
            highest = (int32_t)((1.0 + fraction) / (1.0 - fraction) * query_length) + 1;
        }
    }
    if (lowest < 1) lowest = 1;
    if (highest > index->max_length) highest = index->max_length;

    /* Sorted order makes the band one contiguous run of positions. */
    for (length = lowest; length <= highest; length++) {
        if (index->bucket_start[length] < 0) continue;
        if (first < 0) first = index->bucket_start[length];
        last = index->bucket_start[length] + index->bucket_count[length];
    }
    if (first < 0) return -1;

    span_count = last - first;

    if (thread_count <= 0) {
        /* Automatic: one thread per core, but never so many that a slice drops
           below the break-even size, and never more than the band can feed. */
        thread_count = mrs_cpu_count();
        if (thread_count > 8) thread_count = 8;
        if (span_count < MRS_MIN_PARALLEL) {
            thread_count = 1;
        } else if (thread_count > span_count / MRS_MIN_PER_THREAD) {
            thread_count = span_count / MRS_MIN_PER_THREAD;
        }
    }
    if (thread_count < 1) thread_count = 1;

    tasks = (mrs_task *)calloc((size_t)thread_count, sizeof(mrs_task));
    if (tasks == NULL) return -1;

    mrs_histogram(query, query_length, query_histogram);

    for (i = 0; i < thread_count; i++) {
        tasks[i].index = index;
        tasks[i].query = query;
        tasks[i].query_length = query_length;
        tasks[i].query_histogram = query_histogram;
        tasks[i].threshold = threshold;
        tasks[i].scorer = scorer;
        tasks[i].first = first + (int32_t)((int64_t)span_count * i / thread_count);
        tasks[i].last  = first + (int32_t)((int64_t)span_count * (i + 1) / thread_count);
        tasks[i].best_position = -1;
        tasks[i].best_score = 0.0;
    }

    if (thread_count == 1) {
        mrs_scan(&tasks[0]);
    } else {
        threads = (mrs_thread *)calloc((size_t)thread_count, sizeof(mrs_thread));
        if (threads == NULL) { free(tasks); return -1; }

        /* Thread 0 runs on the calling thread: one fewer spawn, and it gives
           the pool something to do while the others are still starting. */
        for (i = 1; i < thread_count; i++) {
#if defined(_WIN32) || defined(_WIN64)
            threads[i] = CreateThread(NULL, 0, mrs_worker, &tasks[i], 0, NULL);
            if (threads[i] == NULL) break;
#else
            if (pthread_create(&threads[i], NULL, mrs_worker, &tasks[i]) != 0) break;
#endif
            started++;
        }

        mrs_scan(&tasks[0]);

        /* Any slice whose thread failed to start is run here rather than
           dropped -- a spawn failure must cost time, not correctness. */
        for (i = started + 1; i < thread_count; i++) {
            mrs_scan(&tasks[i]);
        }

        for (i = 1; i <= started; i++) {
#if defined(_WIN32) || defined(_WIN64)
            WaitForSingleObject(threads[i], INFINITE);
            CloseHandle(threads[i]);
#else
            pthread_join(threads[i], NULL);
#endif
        }
        free(threads);
    }

    /* Merge. Strictly-greater, so on an equal score the lowest sorted position
       wins -- which is what the sequential scan would have produced. */
    for (i = 0; i < thread_count; i++) {
        scored += tasks[i].scored;
        examined += tasks[i].examined;
        if (tasks[i].best_position < 0) continue;
        if (best_position < 0 ||
            tasks[i].best_score > best_score ||
            (tasks[i].best_score == best_score &&
             tasks[i].best_position < best_position)) {
            best_score = tasks[i].best_score;
            best_position = tasks[i].best_position;
        }
    }

    free(tasks);

    if (out_scored) *out_scored = scored;
    if (out_examined) *out_examined = examined;
    if (best_position < 0) return -1;
    if (out_score) *out_score = best_score;
    return index->origin[best_position];
}
