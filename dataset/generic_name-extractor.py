"""
Build the generic-name list the drug matcher searches.

Reads the openFDA NDC export and writes one lowercased generic name per line.

Paths are resolved relative to this file and to the project root rather than to
the working directory. They used to be bare relative names, which meant the
script only worked when run from inside its own directory — while the README
told you to run it from the project root, where it failed with a
FileNotFoundError. It also wrote its output beside itself rather than into
MRS/database/, where the matcher looks for it.

Usage, from anywhere::

    python dataset/generic_name-extractor.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent

SOURCE = HERE / "drug-ndc.json"
DESTINATION = PROJECT_ROOT / "MRS" / "database" / "ndc_generic-names.txt"


def main() -> int:
    if not SOURCE.is_file():
        print(f"Missing {SOURCE}.", file=sys.stderr)
        print("Download the NDC export first; see the README.", file=sys.stderr)
        return 1

    with open(SOURCE, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    results = data.get("results")

    if not isinstance(results, list):
        print(f"{SOURCE} has no 'results' array; is it the NDC export?", file=sys.stderr)
        return 1

    generic_names = {
        result["generic_name"].strip().lower()
        for result in results
        if isinstance(result, dict)
        and isinstance(result.get("generic_name"), str)
        and result["generic_name"].strip()
    }

    DESTINATION.parent.mkdir(parents=True, exist_ok=True)

    with open(DESTINATION, "w", encoding="utf-8") as handle:
        for name in sorted(generic_names):
            handle.write(name + "\n")

    print(f"Wrote {len(generic_names)} generic names to {DESTINATION}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
