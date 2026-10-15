# Code Review — Medicine Recognition System (FastAPI)

Original review 2026-09-04. **Updated 2026-09-05 after the fixes on branch
`cleanup/2026-09-05` (unpushed).**

**Moved 2026-10-01.** This project used to sit at the archive root as
`MedicineRecognitionSystem/`, beside an empty `Web/FastAPI/MedicineRecognitionSystem - New/`
that held only a review saying the move had been planned and never done. The
author chose to finish the move, so the FastAPI version now sits next to the
Django one, `Web/Django/MedicineRecognitionSystem - Old/`. The repository moved
whole, with its branch and history intact. The suite gave 426 passed both
before and after. Nothing inside the project depended on where it lived. The
placeholder review is not kept: this paragraph is its outcome.

## Status

| # | Finding | State |
|---|---|---|
| 1 | Path traversal in the upload handler | **Fixed** |
| 2 | Same traversal reachable a second time via `ImageProcessor.process_image` | **Fixed** |
| 3 | `eval()` on OCR cache contents (code execution) | **Fixed** |
| 4 | Temp files never cleaned up | **Fixed** |
| 5 | No upload size limit | **Fixed** |
| 6 | Uploaded filenames logged verbatim | **Fixed** — the digest is logged |
| 7 | Safety framing buried at line 986 of a 992-line README | **Fixed** — now the second section |
| 8 | `test/` versus `tests/` | **Fixed** — now `dataset/` |
| 9 | Not under version control | **Fixed** — repo initialised |
| 10 | One failing test on a clean checkout | **Fixed** |
| 11 | No confidence score in the response | **Open** — highest value remaining |
| 12 | No rate limiting | **Open** |
| 13 | `swetsandhya/` is a Flutter app nested in this repo | **Open** — your call |
| 14 | FDA client has no retry or backoff | **Open** |

Two further defects were found while fixing the above:

- `dataset/generic_name-extractor.py` used bare relative paths, so the command
  the README documented (`python test/generic_name-extractor.py`, from the
  project root) failed with `FileNotFoundError`, and it wrote its output beside
  itself rather than into `MRS/database/` where the matcher reads it. Fixed.
- `tests/test_http_client.py::test_unreachable_host_raises_connection_error`
  failed on this machine. Not a flaky test: a connection that times out was
  classified as `Timeout` only, so code catching `ConnectionError` to mean
  "could not reach the server" missed it. Whether an unreachable host times out
  or refuses depends on the network, not the caller. `requests` models this with
  `ConnectTimeout` inheriting from both; that class now exists here too.

## What to check first

1. **Run a real scan.** Everything below the argument builder is unexercised:
   `MRS/model/` is empty on this machine and EasyOCR runs with
   `download_enabled=False`, so no OCR could actually run. The tests inject a
   fake reader throughout.
2. **Your three OCR cache files were converted** from the old `repr()` format to
   JSON, in place. I verified all three parse to detection-for-detection
   identical content, and the originals are backed up at
   `%TEMP%\mrs-cache-backup` if you want to compare. If you have caches on
   another machine, run `python tools/convert_ocr_cache.py` there.
3. **`git diff main`** shows everything. The baseline commit is the code exactly
   as it stood, so the diff is only my changes.

## The remaining work, in the order I would do it

**A confidence score in the response.** This is the one that matters. The API
returns a generic name as though it were certain; the matcher already knows how
close the match was and simply discards it. For software that tells someone what
medicine they are holding, "ibuprofen, 94% match" and "ibuprofen, 76% match" are
different statements and only one of them should be acted on. It needs a field
in the response and something in both front ends to show it — which is why I did
not do it: it needs a UI change, and you asked me not to make those.

**Rate limiting.** OCR is expensive and `/api/upload` is unauthenticated.

**Move `swetsandhya/` out.** An entire Flutter application with its own
`pubspec.yaml`, `android/`, `ios/` and test suite lives inside this Python
repository. It is invisible to anyone reading the README and gets swept up by
anything that walks this tree. Your `Android/Flutter/` directory is where it
would naturally live.

**Retry and backoff on the FDA client.** A ten-second timeout with no retry
means an openFDA outage is a ten-second hang on every request.

## Notes for whoever works on this next

The upload path is the part to be careful with. `utils/helper/uploads.py` is
deliberately paranoid and `tests/test_upload_endpoint.py` proves the endpoint
uses it — eight of those tests fail against the previous handler. If you add an
accepted image format, add its signature to `_SIGNATURES` and a case to the
tests; an extension without a signature is refused, which fails closed and is
the intended behaviour.

The README is now an entry point rather than a manual. The engineering
documentation that used to be in it — the Levenshtein implementations, the
compiled kernel, the matching strategy, the synonym resolver — is intact in
`docs/`, unedited apart from heading levels.

## Licence, 2026-10-02

The author chose one licence for all of their projects: free for personal,
non-commercial use, modifiable for the user's own use, and not to be
distributed, original or modified. No standard licence says that, so `LICENSE`
is a short custom text, the same in every project apart from the year and what
it excludes. The README's licence section says the same. Anything above that
calls the licence an open question, or calls the project MIT, is superseded.

## CI, 2026-10-02

`.github/workflows/ci.yml` runs `pytest` on Python 3.12, `ubuntu-latest`. It
installs only what the suite imports, with `requirements.txt` as constraints:
EasyOCR and Torch are faked in every test and would cost gigabytes. Nothing
has been pushed, so it has not run on GitHub yet. Every job was run before
committing, from a fresh clone of this branch, the way a runner would run it:
Linux jobs in a throwaway Ubuntu 24.04 (the image `ubuntu-latest` uses) with
the same tool versions. 426.
