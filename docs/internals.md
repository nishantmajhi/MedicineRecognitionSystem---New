# The database layer (`models/database.py`)

Plain SQL over the built-in `sqlite3` module. The application stores one thing —
a cache of medicine information keyed by generic name — and needs three
statements against it, all written out in the module where you can read them:

```sql
CREATE TABLE IF NOT EXISTS medicines (...)
SELECT generic_name, purpose, do_not_use FROM medicines WHERE generic_name = ?
INSERT OR IGNORE INTO medicines (generic_name, purpose, do_not_use) VALUES (?, ?, ?)
```

`MedicineDatabase` wraps these to turn failures into `None`/`False`, so a cache
problem degrades the response instead of failing the request.

## Safety

Every value is passed as a bound parameter (`?`), never formatted into the SQL
string. This — not the ORM that used to sit here — is what provides the
protection. Tests assert that a name such as `'); DROP TABLE medicines;--` is
stored and matched as literal text and that the table survives.

## Concurrency

Each call opens its own connection. FastAPI runs synchronous endpoints in a
thread pool, and a `sqlite3.Connection` is not safe to use from a thread other
than the one that created it; opening per call sidesteps that, and SQLite
handles cross-process locking itself. For a cache read this costs microseconds.

`INSERT OR IGNORE` also closes a race that the previous check-then-insert had.
Two threads could both see the row missing and the loser would violate the
`UNIQUE` constraint. Measured with the window held open by 50 ms, the old
pattern failed in 7 of 8 concurrent threads; `INSERT OR IGNORE` failed in 0 of 8
and left exactly one row. In tight loops SQLite's write serialisation usually
hides the race, which is what makes it the kind of bug that appears only under
load.

---

# The HTTP client (`src/http_client.py`)

A GET-only client over `urllib.request`, with an exception hierarchy mirroring
`requests.exceptions` (`RequestException` → `Timeout`, `ConnectionError`,
`HTTPError`) so `except` clauses read the same.

One behavioural detail is preserved deliberately: **urllib raises `HTTPError`
for 4xx and 5xx, whereas `requests` returns them as ordinary responses.** This
module follows `requests`, because `FDAClient` inspects `status_code` itself and
expects 404 and 429 as return values. The error body stays readable, which
matters because openFDA puts a useful error object in it.

Its tests run against a real `http.server` on localhost rather than mocks: the
module's whole job is translating real socket and protocol behaviour into a
`requests`-shaped API, and that translation is the part that can be wrong.

---

# API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Web client |
| `/api/upload` | POST | Upload a photo (`photo` field), get medicine information |
| `/health` | GET | Health check |

Status codes: **200** success, **406** no photo received, **415** unreadable
image, **422** no medicine identified, **500** internal error.

---

# Custom implementations

Three libraries were removed:

| Removed | Replaced by | Lines | Why |
|---------|-------------|-------|-----|
| `thefuzz`, `RapidFuzz`, `python-Levenshtein` | `src/Levenshtein.py` | ~430 | Project requirement, and it exposed a metric mismatch |
| `SQLAlchemy` | `models/database.py` — plain SQL | ~160 | Three statements did not justify an ORM |
| `requests` | `src/http_client.py` | ~230 | One GET request; `urllib` is in the standard library |

The Levenshtein replacement then went a step further: the same algorithms are
also written in C, compiled during setup, and loaded with `ctypes`.

| Component | Language | Role |
|-----------|----------|------|
| `src/Levenshtein.py` | Python | Public API. Picks a backend, reports which one is live |
| `src/clevenshtein/levenshtein.c` | C | The algorithms, compiled at setup. Used when available |
| `src/_levenshtein_python.py` | Python | The same algorithms. Reference implementation and fallback |
| `src/_levenshtein_native.py` | Python | `ctypes` binding and the marshalling layer |

The SQLAlchemy replacement went through two rounds. A hand-written ORM was
built first (~430 lines: a `Column` descriptor, a metaclass, a query builder),
then discarded — the application issues exactly three statements, so a general
query builder was machinery with no user. Plain SQL over `sqlite3` does the same
job in about 110 lines, and the SQL is visible rather than generated. Writing
the ORM first was not wasted: it is what made the mismatch obvious.

Six packages left `requirements.txt` as a result (`SQLAlchemy`, `requests`, and
`requests`' own dependencies `certifi`, `charset-normalizer`, `idna` and
`urllib3`), taking it from 50 entries to 44.

## A caution about performance

Replacing a library is usually *bad* for speed, and it is worth being clear
about that. The C extension this project removed was far faster than the Python
that first replaced it:

```
5,000 distance computations on 12-character strings
  our pure-Python Levenshtein :  191.1 ms
  C-extension Levenshtein     :    1.8 ms   -> the library was 105x faster
```

That gap is what motivated writing the algorithms in C as well — see
[The compiled kernel](#the-compiled-kernel), which closes most of it. But the
honest summary is still that these replacements are justified by **learning
value and dependency count**, not by throughput. The database layer and
`http_client.py` cost nothing measurable, because SQLite and the network
dominate their runtime either way.

Where performance genuinely improved end to end, it came from *algorithms*
rather than from reimplementation or from compiling. See
[How matching is made fast](#how-matching-is-made-fast), and the measurements in
[What this bought, honestly](#what-this-bought-honestly), which are less
flattering than the kernel benchmarks and more useful.

---

