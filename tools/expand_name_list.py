"""
Add INN spellings to the NDC generic-name list.

Why this exists
---------------
``DrugMatcher`` runs before ``FDAClient``. If a package prints "Paracetamol" and
that string is absent from ``ndc_generic-names.txt``, the matcher returns no
result and the request fails with 422 -- the synonym resolver in FDAClient is
never reached, because nothing got that far.

Resolving at the API stage alone is therefore not enough. The matcher's own
vocabulary has to contain the INN spellings too, which is what this script adds.

What it does
------------
For every INN/USAN pair in ``SynonymResolver.INN_TO_USAN``, if the USAN appears
in the list, the INN is appended. Purely additive: no existing entry is removed
or changed, so the file stays a valid input for everything else.

Usage
-----
    python tools/expand_name_list.py
    python tools/expand_name_list.py --dry-run
    python tools/expand_name_list.py --input custom.txt --output expanded.txt

Re-running is safe; names already present are skipped.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.synonym_resolver import INN_TO_USAN  # noqa: E402

DEFAULT_LIST = PROJECT_ROOT / "MRS" / "database" / "ndc_generic-names.txt"


def expand(names: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Append INN spellings for any USAN already present.

    Returns:
        The expanded list, and the (inn, usan) pairs that were added.
    """
    existing = {name.strip().lower() for name in names if name.strip()}
    additions: list[tuple[str, str]] = []

    for inn, usan in sorted(INN_TO_USAN.items()):
        if inn == usan:
            continue  # not actually a rename
        if usan.lower() in existing and inn not in existing:
            additions.append((inn, usan))
            existing.add(inn)

    return names + [inn for inn, _ in additions], additions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_LIST)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Defaults to overwriting the input file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be added without writing.",
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"error: name list not found at {args.input}", file=sys.stderr)
        print("See the README for how to build it from the NDC export.", file=sys.stderr)
        return 1

    names = [
        line.strip()
        for line in args.input.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    expanded, additions = expand(names)

    print(f"read    {len(names)} names from {args.input}")
    if not additions:
        print("nothing to add; every known INN spelling is already present")
        return 0

    for inn, usan in additions:
        print(f"  + {inn:<20} (INN spelling of {usan})")

    if args.dry_run:
        print(f"\ndry run: would write {len(expanded)} names")
        return 0

    destination = args.output or args.input
    destination.write_text("\n".join(expanded) + "\n", encoding="utf-8")
    print(f"\nwrote   {len(expanded)} names to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
