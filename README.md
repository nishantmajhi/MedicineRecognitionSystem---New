# Medicine Recognition System

Photograph a medicine packet; get back what the medicine is for and who should
avoid it.

A FastAPI backend runs OCR over the image, matches the extracted text against
the FDA's NDC generic-name list using a custom Levenshtein implementation, and
looks the result up in the openFDA label database. Two front ends talk to it:
the web page in `static/`, and **SwetSandhya**, the Flutter app in
`swetsandhya/`.

---

## Read this before using it on a real medicine

**This tells you what a medicine probably is. It does not tell you what to
take.** Every step in the chain can be wrong, and none of them announces it:

- **OCR misreads packaging.** Foil, curved surfaces, small print and glare all
  produce wrong characters.
- **The matcher is fuzzy by design.** It finds the closest name above a
  similarity threshold. "Closest" is not "correct", and drug names are full of
  near-neighbours — a wrong match between two real drugs is the dangerous
  failure, and it looks exactly like a right one.
- **The label may not be the one in your hand.** openFDA describes US products.
  The same generic name is sold elsewhere in different strengths and
  combinations.

So: **always verify against the packaging before acting on anything this
returns.** It is a reading aid, not a source of medical advice, and it is not a
substitute for a pharmacist.

That framing is the reason the API returns a generic name rather than a dosage,
and it is why adding a "confidence" figure to the response is the highest-value
change still outstanding — see [Known gaps](#known-gaps).

---

## Requirements

- **Python 3.10 – 3.13.** Developed against 3.12. **Not 3.14** — see below.
- A **C compiler** (`cl`, `gcc` or `clang`), optional. Setup compiles
  `src/clevenshtein/levenshtein.c` if one is present and falls back to pure
  Python if not.
- An internet connection, for the openFDA API and the first-run EasyOCR model
  download.
- **Flutter 3.24+**, only if you want to build the mobile app.

### Why 3.14 does not work

`easyocr` needs `scikit-image` and `torch`, and neither publishes wheels for
3.14 yet. pip falls back to building `scikit-image` from source, that build runs
`pythran`, and pythran's headers need `/std:c++17` while the build passes
`/std:c++14`. You get several thousand lines of MSVC template errors ending in:

```
error C7518: fold expressions require at least '/std:c++17'
...
error: metadata-generation-failed
```

Nothing in that output mentions the Python version, which is why `setup.bat` and
`setup.sh` check the interpreter first and stop with one line instead. If you
have already hit it: install 3.12, delete `.venv`, run setup again. There is
nothing to repair in the project.

---

## Getting it running

```bash
git clone https://github.com/nishantmajhi/MedicineRecognitionSystem
cd MedicineRecognitionSystem

setup.bat          # Windows
./setup.sh         # Linux / macOS

python main.py     # then open http://localhost:8000
```

Manually, on any platform:

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m src.clevenshtein.build     # compile the C kernel; optional
python main.py
```

The compile step is optional; `main.py` also attempts it at startup, and the
pure-Python implementation is used if no compiler is found. To see which is
live:

```bash
python -c "from src.Levenshtein import backend_report; print(backend_report())"
```

### Data you have to fetch

Four things are too large for the repository. The server starts without them,
but OCR and matching will not work.

| Path | What it is | How to get it |
|---|---|---|
| `MRS/model/*.pth` | EasyOCR detection and recognition models | Downloaded automatically on first use, about 100 MB |
| `dataset/drug-ndc.json` | The raw NDC export | Download, below |
| `MRS/database/ndc_generic-names.txt` | The name list the matcher searches | Built from the export, below |
| `logs/application.log` | Application log | Created on first run |

The EasyOCR download happens the first time an image is processed and takes a
few minutes, so run one scan before demonstrating this rather than during it.

The NDC Directory is published by openFDA as a single zipped JSON file:

```bash
mkdir -p dataset MRS/database

# The manifest names the current URL for every openFDA dataset. Read it rather
# than hardcoding a URL: large datasets are split into numbered parts and the
# count changes. NDC is currently one part.
curl -s https://api.fda.gov/download.json \
  | python -c "import json,sys; print(json.load(sys.stdin)['results']['drug']['ndc']['partitions'][0]['file'])"

curl -L -o dataset/drug-ndc.json.zip \
  https://download.open.fda.gov/drug/ndc/drug-ndc-0001-of-0001.json.zip
unzip -o dataset/drug-ndc.json.zip -d dataset/
mv dataset/drug-ndc-0001-of-0001.json dataset/drug-ndc.json
```

Then build the name list:

```bash
python dataset/generic_name-extractor.py   # -> MRS/database/ndc_generic-names.txt
python tools/expand_name_list.py           # adds paracetamol, adrenaline, ...
```

**The second step is not optional if you scan packaging sold outside the US.**
The NDC list is US-only, so it contains "acetaminophen" and not "paracetamol".
See [docs/matching.md](docs/matching.md). Both scripts are idempotent; re-run
both, in order, after a fresh export.

---

## How it fits together

```
POST /api/upload  (routes/api.py)
      |
      |  validate: extension, signature, size          utils/helper/uploads.py
      |  store under a generated name
      v
  OCR, cached by image digest                          utils/image_processor.py
      |
      v
  match text against the NDC name list                 utils/helper/drug_matcher.py
      |  custom Levenshtein, C kernel or pure Python    src/Levenshtein.py
      |  regional names resolved to their INN           src/synonym_resolver.py
      v
  look up the label                                    utils/helper/fda_client.py
      |  minimal urllib-based client                    src/http_client.py
      v
  JSON: genericName, purpose, do_not_use
```

Three dependencies were deliberately removed and reimplemented: the fuzzy
matcher (`thefuzz`), the ORM (`SQLAlchemy`) and the HTTP client (`requests`).
The reasoning, the measurements and the trade-offs are in:

- **[docs/levenshtein.md](docs/levenshtein.md)** — the three implementations,
  the compiled kernel, why ctypes rather than a CPython extension, benchmarks,
  and why threading is off by default.
- **[docs/matching.md](docs/matching.md)** — how the scan is made fast, what was
  measured and rejected, and the synonym resolver for regional drug names.
- **[docs/internals.md](docs/internals.md)** — the database layer, the HTTP
  client, and the custom-implementation rationale.

---

## Layout

```
main.py                  Application entry point
routes/                  HTTP endpoints
utils/                   Image processing and the drug pipeline
  helper/uploads.py      Upload validation and safe naming
src/                     Levenshtein, the C kernel, the HTTP client, synonyms
models/                  The medicine database
dataset/                 The NDC export, its extractor, and sample photographs
tools/                   One-off and maintenance scripts
tests/                   The test suite
docs/                    Design notes
static/                  The web front end
swetsandhya/             The Flutter app
MRS/                     Runtime data: models, media, cache, temp, database
```

`dataset/` was called `test/` until 2026-09-05, one letter from `tests/` and
meaning something entirely different.

---

## Testing

```bash
pytest                        # everything
pytest tests/test_uploads.py  # one file
```

426 tests. EasyOCR is never loaded: the reader is behind a lazy property and
each test injects a fake, so the suite runs in seconds without a model or a
network.

Two areas are worth knowing about before you change them.

**The upload tests are the security boundary.** `tests/test_uploads.py` covers
the rules and `tests/test_upload_endpoint.py` proves the endpoint applies them.
Eight of those endpoint tests fail against the pre-2026-09-05 handler, which is
the point of them.

**The Levenshtein tests run twice.** Once against the C kernel and once against
the pure-Python implementation, because the whole argument for the kernel is
that it is indistinguishable except in speed.

---

## Security notes

**The stored filename never comes from the request.** `photo.filename` is chosen
by the caller, and joining it onto a directory does not confine it: a relative
name walks up, and `pathlib` discards the base entirely when the right-hand side
is absolute, so `Path("MRS/temp") / "C:/inetpub/wwwroot/x.png"` *is* that
absolute path. Uploads are validated and then stored under a generated name.
See `utils/helper/uploads.py`.

**Uploads are checked on two axes.** The extension must be on the allowlist and
the file's leading bytes must identify it as that format. Either check alone is
bypassable in one direction.

**The OCR cache is data, not code.** It holds JSON. It previously held `repr()`
of the OCR result and was read back with `eval()`, so anything able to write
into `MRS/cache/` could run code in the server process — and the upload path
above was exactly such a way. `tools/convert_ocr_cache.py` converts files in the
old format; run it once if you have a cache predating this change.

---

## Known gaps

- **No confidence score in the response.** The API returns a name as though it
  were certain. The matcher knows how close the match was and should say so, and
  the front ends should show it. This is the most valuable thing still missing,
  for the reasons at the top of this file.
- **No rate limiting.** OCR is expensive and the endpoint is unauthenticated.
- **`swetsandhya/` is a whole Flutter application inside this repository**, with
  its own `pubspec.yaml`, `android/`, `ios/` and tests. It should be its own
  repository.
- **`FDAClient._replace_generic_name` has branches that cannot run.**
  `_parse_response` lowercases its values first, so only the lowercase case
  fires. The others are documented as deliberate defensive code; they are
  untested by construction.
- **The similarity threshold is exclusive**, so a score of exactly 75 is
  rejected. Left as it was.
- **The FDA client has a timeout but no retry**, so an openFDA outage becomes a
  ten-second hang per request.

### Behavioural changes made previously, kept here for the record

- `identify_drugs` de-duplicates by default; packaging repeats the drug name and
  the response used to read "Ibuprofen, Ibuprofen". Pass `deduplicate=False` for
  the old behaviour.
- `MedicineDatabase` no longer leaks its database session. It called
  `next(self.get_db())`, starting a generator it never finished, so
  `finally: db.close()` ran whenever that generator was collected. SQLAlchemy
  tolerated it; raw `sqlite3` did not.
- Saving a medicine is one atomic statement.
- Regional drug names are handled by `SynonymResolver` rather than an
  `if generic_name.lower() == "paracetamol"` in `FDAClient`.
- `setup.bat` no longer regenerates the package `__init__.py` files. It used to
  overwrite them on every run with a version importing a module that does not
  exist, so a fresh clone could not start and the failure looked like a broken
  checkout.
- The openFDA URL percent-encodes the drug name. `requests` did that silently;
  `urllib` does not, so multi-word names produced a malformed URL.

---

## License

Free for personal, non-commercial use. You may use, study and modify this
software for yourself, but you may not use it commercially, and you may not
share or publish it, in its original form or modified. See [LICENSE](LICENSE)
for the full terms.

## Contributors

- Manish Majhi
