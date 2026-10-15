# How matching is made fast

Matching is the only stage of the request written in pure Python, so it is the
only one whose speed is in this project's hands. OCR is Torch, image decoding is
OpenCV, and the database is SQLite — all native code already.

`identify_drugs` calls `lookup()` once per OCR fragment (roughly ten per photo),
and each call must find the best-scoring name in a list of ~13,000. Scoring
every name with the full dynamic program is far too slow, so `lookup()` applies
two cheap gates first. Each computes a **lower bound on the edit distance** —
equivalently, an upper bound on the achievable score — and skips the candidate
when even that bound cannot clear the bar. Both are exact: they only ever
discard candidates that provably could not have won.

The gates below are described as `lookup()`'s Python path implements them.
`levenshtein.c` implements the same three, in the same order, for the compiled
path — see [Moving the whole scan into C](#moving-the-whole-scan-into-c). The
order matters and is not arbitrary: each gate is cheaper than the one after it,
so reordering them would change how much work reaches the dynamic program.

For the query `ibuprofn` against 13,003 names:

| Gate | Survivors | Cost per candidate |
|------|-----------|--------------------|
| — | 13,003 | — |
| 1. Length band | 3,794 | free (dict lookup) |
| 2. Score ceiling | 2,587 | ~2 arithmetic ops |
| 3. Character count | **2** | ~26 ops |
| Full Levenshtein DP | 2 | ~96 cell computations |

**Gate 1, the length band.** The edit distance is at least the difference in
lengths, so the score cannot exceed `min/max × 100`. At threshold 75 only names
between roughly 0.75× and 1.33× the query length can match. Names are bucketed
by length once, so this costs nothing at query time.

**Gate 2, the score ceiling.** The same bound, re-checked against the best score
found *so far* rather than the fixed threshold. As `match_ratio` climbs the bar
rises, so later candidates are rejected more aggressively than earlier ones.

**Gate 3, the character count.** Compare the letter frequencies of the two
strings and sum the absolute differences:

```
ibuprofn   ->  {i:1, b:1, u:1, p:1, r:1, o:1, f:1, n:1}
ibuprofen  ->  {i:1, b:1, u:1, p:1, r:1, o:1, f:1, e:1, n:1}
total difference D = 1
```

A single Levenshtein edit changes at most two letter counts — a substitution
removes one letter and adds another — so at least `ceil(D / 2)` edits are
needed. Under indel scoring substitution is unavailable, so each unit of
difference needs its own insertion or deletion and the bound is the full `D`.

This gate is effective because of the complexity gap: the dynamic program is
`O(m × n)` while comparing two counters is flat `O(alphabet)`. Roughly 26 cheap
operations avoid roughly 96 expensive ones, and it succeeds over 99% of the
time.

```
13,003 names, threshold 75, averaged over 6 queries
  length band + score ceiling      156.2 ms/lookup   -> matching stage ~1.56 s
  + character-count prefilter       21.5 ms/lookup   -> matching stage ~0.22 s
```

**7.3x on the stage**, for an 85 ms one-time index build. Because matching is
only 15–35% of request latency, the end-to-end gain is closer to 1.1–1.3x —
Amdahl's law, not a disappointment. Moving this same loop into C later took the
stage a further ~500x, which is worth roughly 1.2–1.5x end to end for the same
reason. `tests/test_drug_matcher.py` verifies the
bounds never exceed the true distance and that enabling the prefilter changes no
result, across both scorers and six thresholds.

## One subtlety worth knowing

The gate uses exact integer arithmetic while the score is binary floating point.
A candidate sitting exactly on the threshold can score `30.000000000000004`
rather than `30`, which the strictly-greater comparison accepts but an exact
gate would reject. The limit therefore carries a `1e-9` epsilon, so borderline
candidates fall through to the real scorer instead of being discarded. The gate
stays conservative — it can only ever skip candidates that cannot win.

## Approaches that were measured and rejected

A **BK-tree** index was prototyped and is **10x slower** than the character-count
prefilter here (207 ms vs 19.5 ms per lookup). BK-trees prune well when the
search radius is one or two edits; a 75% threshold on a 12-character name means
a radius of about 3, at which the triangle-inequality bound eliminates almost no
subtrees while still costing a full distance computation at every node visited.
The ratio-based threshold also forces one tree per length bucket, since a
BK-tree needs a fixed radius.

Rewriting the **matcher loop** in C — the gate loop in `lookup()`, not just the
distance function — was originally rejected here and has since been done. It
turned out to be the whole story: see
[Moving the whole scan into C](#moving-the-whole-scan-into-c). Compiling the
distance function alone measured at *zero* end-to-end gain, because the gates
above had already reduced it to about eighteen calls per lookup. Moving the
gates themselves into C is what produced the speed-up.

The earlier version of this section predicted a 1.1–1.5x end-to-end ceiling and
treated the toolchain requirement as disqualifying. The ceiling was right and
still stands: matching is 15–35% of a request, so a 500x matching stage is worth
about 1.2–1.5x overall. What the prediction got wrong was *where* the time went
— compiling the distance function alone measured at zero, because the gates had
already taken the DP off the critical path. The toolchain concern was addressed
differently, by making the build optional and the Python fallback permanent.

---

# Regional drug names: the synonym resolver

Packaging sold in Nepal, India and the UK prints the **INN** (International
Nonproprietary Name). The NDC list and openFDA both index the **USAN** (United
States Adopted Name). For many drugs these are completely different words:

| Nepal / UK (INN) | US (USAN) | Levenshtein score |
|------------------|-----------|-------------------|
| paracetamol | acetaminophen | **31** |
| adrenaline | epinephrine | **36** |
| salbutamol | albuterol | 70 |
| lignocaine | lidocaine | 84 |

## Why the matcher cannot solve this

`DrugMatcher` compares spelling, and these are synonyms with unrelated
spellings. Against the threshold of 75 they simply do not match, and no
threshold fixes it — admitting a score of 31 would match nearly everything to
everything. Edit distance answers *"was this mistyped?"*; this question is
*"is this the same drug under another name?"*, which needs a vocabulary rather
than a string metric.

## It has to be fixed in two places

This is the part that is easy to get wrong. `DrugMatcher` runs **before**
`FDAClient`. If "paracetamol" is absent from `ndc_generic-names.txt`, the
matcher returns nothing and the request fails with 422 — the FDA query stage is
never reached, so a fix applied only there does nothing:

```
BEFORE: NDC list is US-only
  matcher.lookup('paracetamol') -> ''      => request 422s here

AFTER: list expanded with INN spellings
  matcher.lookup('paracetamol') -> 'paracetamol'
  resolver -> query openFDA for 'acetaminophen'
  response renamed back to 'paracetamol' for the user
```

So the resolver is applied twice:

1. **Offline, at list-build time.** `python tools/expand_name_list.py` appends
   the INN spelling of any USAN already in the list, so the matcher can
   recognise the printed name at all. Purely additive and safe to re-run.
2. **Online, before the FDA query.** `FDAClient` resolves the name, queries
   openFDA with the USAN, and renames the answer back — someone who
   photographed a Paracetamol box should not be told about "acetaminophen".

## How resolution works

`src/synonym_resolver.py` consults three sources, cheapest first:

1. **Local overrides** (`INN_TO_USAN`) — offline, instant, covers the common
   cases, and the only place that can hold South Asian brand names.
2. **The SQLite cache** — each name reaches the network at most once.
3. **RxNorm**, through the NLM's public [RxNav API](https://rxnav.nlm.nih.gov/).
   Free, no API key, 20 requests/second. It normalises a name to a concept
   (`/rxcui.json?name=…&search=2`) and reports that concept's ingredient
   (`/rxcui/{id}/related.json?tty=IN`), which is the USAN.

If all three fail — no override, nothing cached, NLM unreachable — the original
name is returned unchanged. The resolver never raises: a temporary NLM outage
should not turn into a failed scan.

## Limits

RxNorm is a **US** vocabulary. It carries INN synonyms such as paracetamol, but
it will not recognise South Asian brand names — a box printed only "Sitamol"
will not resolve. Those need adding to `INN_TO_USAN`, which is why the override
table exists and why it is consulted first.

The RxNav request shapes come from NLM's published documentation and are
exercised against stubbed responses in the tests. Verify them against the live
service on first run.

---

