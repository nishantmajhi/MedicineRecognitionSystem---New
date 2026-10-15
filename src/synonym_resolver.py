"""
Resolve regional drug names to the US generic name the data sources use.

The problem
-----------
Packaging sold in Nepal, the UK and most of the Commonwealth prints the INN
(International Nonproprietary Name). The NDC list and openFDA both index the
USAN (United States Adopted Name). For many drugs these differ completely:

    paracetamol  ->  acetaminophen
    adrenaline   ->  epinephrine
    salbutamol   ->  albuterol
    lignocaine   ->  lidocaine

Why the fuzzy matcher cannot do this
------------------------------------
``DrugMatcher`` compares spelling. These pairs are *synonyms*, not misspellings,
and their spellings are unrelated::

    levenshtein_ratio("paracetamol", "acetaminophen")  ==  31
    levenshtein_ratio("adrenaline",  "epinephrine")    ==  36

Against a threshold of 75 they never match, and no threshold fixes it: 31 is low
enough that admitting it would match essentially everything to everything.
Edit distance answers "was this mistyped?"; this is "is this the same drug under
a different name?", which needs a vocabulary, not a string metric.

How this module answers it
--------------------------
Three sources are consulted in order, cheapest and most reliable first:

1. **Local overrides** (:data:`INN_TO_USAN`). Offline, instant, and covers the
   common cases. Also the only place that can hold South Asian brand names,
   which a US vocabulary will never carry.
2. **The SQLite cache**, so a name is resolved over the network only once.
3. **RxNorm**, via the NLM's public RxNav API. Free, no key, 20 requests per
   second. It normalises a name to a concept and reports that concept's
   ingredient, which is the USAN.

If every source fails -- no override, nothing cached, network down -- the
original name is returned unchanged. A resolver that raised would turn a
temporary NLM outage into a failed scan, which is a bad trade for a lookup that
is only occasionally needed.

Where this is used
------------------
Two places, and both matter:

* ``FDAClient`` resolves before querying openFDA, replacing a hardcoded
  ``if name == "paracetamol"``.
* ``tools/expand_name_list.py`` expands ``ndc_generic-names.txt`` with INN
  spellings *offline*, so ``DrugMatcher`` can find "paracetamol" on a package
  at all. Resolving only at the API stage is not enough: the matcher runs first,
  and if the printed name is absent from its list the request fails before the
  resolver is ever reached.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, NamedTuple, Optional

from src import http_client

logger = logging.getLogger("synonym_resolver.py")

RXNAV_BASE_URL = "https://rxnav.nlm.nih.gov/REST"

# INN (used in Nepal, India, the UK) -> USAN (used by the NDC list and openFDA).
#
# Keep this small and high-confidence. It exists so the common cases work with
# no network at all, and to hold names RxNorm does not carry. It is not meant to
# grow into a drug dictionary -- that is what RxNorm is for.
INN_TO_USAN: Dict[str, str] = {
    "paracetamol": "acetaminophen",
    "adrenaline": "epinephrine",
    "noradrenaline": "norepinephrine",
    "salbutamol": "albuterol",
    "lignocaine": "lidocaine",
    "frusemide": "furosemide",
    "rifampicin": "rifampin",
    "amoxycillin": "amoxicillin",
    "cephalexin": "cefalexin",
    "glibenclamide": "glyburide",
    "gliclazide": "gliclazide",
    "metamizole": "dipyrone",
    "pethidine": "meperidine",
    "thiopentone": "thiopental",
    "oestradiol": "estradiol",
    "beclometasone": "beclomethasone",
    "chlorphenamine": "chlorpheniramine",
    "dicyclomine": "dicycloverine",
    "trimethoprim": "trimethoprim",
    "phenobarbitone": "phenobarbital",
}


class Resolution(NamedTuple):
    """The outcome of resolving one name."""

    source_name: str
    """The name as printed on the package, lowercased."""

    resolved_name: str
    """The name to query the US data sources with."""

    rxcui: Optional[str]
    """The RxNorm concept identifier, when one was found."""

    source: str
    """Which source answered: ``override``, ``cache``, ``rxnorm`` or ``none``."""

    @property
    def changed(self) -> bool:
        """Whether resolution actually renamed the drug."""
        return self.source_name != self.resolved_name


class SynonymResolver:
    """
    Maps a printed drug name to the generic name the US data sources index.

    Args:
        db_path: Where to cache resolutions. Defaults to the project database.
        use_network: When False, only the override table and cache are
            consulted. Useful for tests and for running fully offline.
        timeout: Per-request timeout for RxNav, in seconds.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        use_network: bool = True,
        timeout: int = 5,
    ):
        from models import database

        self._database = database
        self.db_path = Path(db_path) if db_path else database.DEFAULT_DB_PATH
        self.use_network = use_network
        self.timeout = timeout

    # -- public API ---------------------------------------------------------

    def resolve(self, name: str) -> Resolution:
        """
        Resolve one drug name, consulting overrides, then cache, then RxNorm.

        Never raises. An unresolvable name comes back unchanged with
        ``source="none"``, so callers can always use ``resolved_name``.
        """
        cleaned = (name or "").strip().lower()
        if not cleaned:
            return Resolution("", "", None, "none")

        override = INN_TO_USAN.get(cleaned)
        if override:
            return Resolution(cleaned, override, None, "override")

        cached = self._read_cache(cleaned)
        if cached:
            return cached

        if not self.use_network:
            return Resolution(cleaned, cleaned, None, "none")

        resolved = self._query_rxnorm(cleaned)
        if resolved:
            self._write_cache(resolved)
            return resolved

        return Resolution(cleaned, cleaned, None, "none")

    def resolved_name(self, name: str) -> str:
        """Just the resolved name. Convenience for the common call."""
        return self.resolve(name).resolved_name

    # -- cache --------------------------------------------------------------

    def _read_cache(self, name: str) -> Optional[Resolution]:
        try:
            row = self._database.get_synonym(name, self.db_path)
        except Exception as e:
            logger.warning(f"Synonym cache unreadable: {e}")
            return None

        if not row:
            return None
        return Resolution(
            row["source_name"], row["resolved_name"], row["rxcui"], "cache"
        )

    def _write_cache(self, resolution: Resolution) -> None:
        try:
            self._database.save_synonym(
                resolution.source_name,
                resolution.resolved_name,
                resolution.rxcui,
                resolution.source,
                self.db_path,
            )
        except Exception as e:
            # A cache write failure must not fail the resolution itself.
            logger.warning(f"Could not cache synonym: {e}")

    # -- RxNorm -------------------------------------------------------------

    def _query_rxnorm(self, name: str) -> Optional[Resolution]:
        """
        Ask RxNav for the ingredient concept behind a name.

        Two calls: find the concept id for the name, then ask for that
        concept's ingredient. ``search=2`` requests RxNorm's normalised search,
        which is what matches synonym spellings such as "Paracetamol" onto the
        acetaminophen concept.
        """
        rxcui = self._find_rxcui(name)
        if not rxcui:
            return None

        ingredient = self._find_ingredient_name(rxcui)
        if not ingredient:
            return None

        return Resolution(name, ingredient.lower(), rxcui, "rxnorm")

    def _find_rxcui(self, name: str) -> Optional[str]:
        payload = self._get_json(
            f"{RXNAV_BASE_URL}/rxcui.json",
            {"name": name, "search": "2"},
        )
        if not payload:
            return None

        identifiers = (payload.get("idGroup") or {}).get("rxnormId") or []
        return identifiers[0] if identifiers else None

    def _find_ingredient_name(self, rxcui: str) -> Optional[str]:
        """
        The ingredient (``tty=IN``) behind a concept, which is the US generic
        name. Falls back to the concept's own name if no ingredient is linked --
        querying an ingredient concept directly returns no further ingredient.
        """
        payload = self._get_json(
            f"{RXNAV_BASE_URL}/rxcui/{rxcui}/related.json", {"tty": "IN"}
        )
        if payload:
            groups = (payload.get("relatedGroup") or {}).get("conceptGroup") or []
            for group in groups:
                for concept in group.get("conceptProperties") or []:
                    concept_name = concept.get("name")
                    if concept_name:
                        return concept_name

        properties = self._get_json(f"{RXNAV_BASE_URL}/rxcui/{rxcui}/properties.json")
        if properties:
            return (properties.get("properties") or {}).get("name")
        return None

    def _get_json(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        """GET and parse JSON, turning every failure into None."""
        try:
            response = http_client.get(url, timeout=self.timeout, params=params)
        except http_client.RequestException as e:
            logger.warning(f"RxNav unreachable: {e}")
            return None

        if response.status_code != 200:
            logger.warning(f"RxNav returned {response.status_code} for {url}")
            return None

        try:
            return response.json()
        except ValueError as e:
            logger.warning(f"RxNav returned unparseable JSON: {e}")
            return None
