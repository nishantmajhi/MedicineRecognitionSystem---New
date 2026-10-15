"""
Convert OCR cache files from the old Python-repr format to JSON.

Cache files written before 2026-09-05 hold ``repr()`` of the EasyOCR result,
which contains constructor calls such as ``np.int32(212)``. Reading one back
required ``eval()``, and ``eval()`` on the contents of a file is arbitrary code
execution — until the upload path was fixed, any way of writing into the cache
directory was a way of running code in the server process.

The reader now expects JSON. Converting rather than discarding matters here
because EasyOCR is configured with ``download_enabled=False``: on a machine with
no models in ``MRS/model/`` a discarded cache entry cannot be regenerated, and
the images it covered would stop working.

Usage::

    python tools/convert_ocr_cache.py [--dry-run] [--cache-dir MRS/cache]

Files already in JSON are left alone, so this is safe to run more than once.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, List, Optional


def parse_legacy(text: str) -> Optional[List[Any]]:
    """
    Parse the old repr format without executing it.

    ``ast.literal_eval`` refuses ``np.int32(212)`` because a call is not a
    literal, so the numpy constructors are stripped first: every
    ``np.<type>(<literal>)`` is replaced by the literal it wraps. What remains is
    a plain nested list of numbers and strings, which ``literal_eval`` accepts.
    """
    import re

    # np.int32(212) -> 212, np.float64(0.98) -> 0.98, np.str_('x') -> 'x'
    unwrapped = re.sub(r"\bnp\.[A-Za-z_][A-Za-z0-9_]*\(([^()]*)\)", r"\1", text)

    # Repeat until stable, in case of nesting.
    for _ in range(5):
        replaced = re.sub(r"\bnp\.[A-Za-z_][A-Za-z0-9_]*\(([^()]*)\)", r"\1", unwrapped)
        if replaced == unwrapped:
            break
        unwrapped = replaced

    try:
        value = ast.literal_eval(unwrapped)
    except (ValueError, SyntaxError):
        return None

    return list(value) if isinstance(value, (list, tuple)) else None


def to_plain(value: Any) -> Any:
    """Turn tuples into lists so the result round-trips through JSON unchanged."""
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]

    return value


def convert_file(path: Path, dry_run: bool) -> str:
    """Convert one cache file. Returns a one-word status for reporting."""
    text = path.read_text(encoding="utf-8").strip()

    if not text:
        return "empty"

    try:
        json.loads(text)
        return "already-json"
    except ValueError:
        pass

    parsed = parse_legacy(text)

    if parsed is None:
        return "unparseable"

    if not dry_run:
        path.write_text(json.dumps(to_plain(parsed)), encoding="utf-8")

    return "converted"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default="MRS/cache")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)

    if not cache_dir.is_dir():
        print(f"No cache directory at {cache_dir}; nothing to do.")
        return 0

    counts: dict[str, int] = {}

    for path in sorted(cache_dir.glob("*.ocr")):
        status = convert_file(path, args.dry_run)
        counts[status] = counts.get(status, 0) + 1
        print(f"{status:>13}  {path.name}")

    print()
    for status, count in sorted(counts.items()):
        print(f"{count} {status}")

    # An unparseable file is not fatal — the reader treats it as a miss and
    # re-runs OCR — but it is worth a non-zero exit so a caller notices.
    return 1 if counts.get("unparseable") else 0


if __name__ == "__main__":
    sys.exit(main())
