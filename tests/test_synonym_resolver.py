"""
Tests for src.synonym_resolver and tools/expand_name_list.

The network is never touched. RxNav responses are stubbed, because a test that
depended on the NLM being reachable would fail for reasons unrelated to this
code.
"""

import json

import pytest

from models import database
from src import http_client
from src.synonym_resolver import (
    INN_TO_USAN,
    RXNAV_BASE_URL,
    Resolution,
    SynonymResolver,
)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "synonyms.sqlite3"
    database.init_db(path)
    return path


@pytest.fixture
def offline_resolver(db_path):
    """Overrides and cache only; never reaches for the network."""
    return SynonymResolver(db_path, use_network=False)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


def stub_rxnav(monkeypatch, routes, recorder=None):
    """
    Replace the HTTP transport with a stub.

    Args:
        routes: Maps a URL fragment to a payload, or to an exception to raise.
        recorder: Optional list that receives each (url, params) pair.
    """

    def fake_get(url, timeout=None, params=None, **kwargs):
        if recorder is not None:
            recorder.append({"url": url, "params": params})
        for fragment, payload in routes.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)
        return FakeResponse({}, status_code=404)

    monkeypatch.setattr(http_client, "get", fake_get)


RXCUI_PAYLOAD = {"idGroup": {"rxnormId": ["161"]}}
INGREDIENT_PAYLOAD = {
    "relatedGroup": {
        "conceptGroup": [
            {"tty": "IN", "conceptProperties": [{"rxcui": "161", "name": "acetaminophen"}]}
        ]
    }
}


class TestWhyThisModuleExists:
    """
    Pins down the fact that motivates the whole module: the fuzzy matcher
    cannot bridge these names, so a vocabulary lookup is required.
    """

    @pytest.mark.parametrize(
        "inn, usan",
        [
            ("paracetamol", "acetaminophen"),
            ("adrenaline", "epinephrine"),
            ("salbutamol", "albuterol"),
        ],
    )
    def test_edit_distance_cannot_bridge_inn_and_usan(self, inn, usan):
        from src.Levenshtein import ratio

        assert ratio(inn, usan) < 75, (
            "if this ever clears the matcher threshold, revisit whether the "
            "resolver is still needed for this pair"
        )

    def test_resolver_bridges_them(self, offline_resolver):
        assert offline_resolver.resolved_name("paracetamol") == "acetaminophen"
        assert offline_resolver.resolved_name("adrenaline") == "epinephrine"


class TestOverrides:
    def test_paracetamol_resolves_offline(self, offline_resolver):
        resolution = offline_resolver.resolve("paracetamol")

        assert resolution.resolved_name == "acetaminophen"
        assert resolution.source == "override"
        assert resolution.changed is True

    def test_lookup_is_case_insensitive(self, offline_resolver):
        assert offline_resolver.resolved_name("Paracetamol") == "acetaminophen"
        assert offline_resolver.resolved_name("PARACETAMOL") == "acetaminophen"

    def test_surrounding_whitespace_is_ignored(self, offline_resolver):
        assert offline_resolver.resolved_name("  paracetamol  ") == "acetaminophen"

    def test_us_names_pass_through_unchanged(self, offline_resolver):
        resolution = offline_resolver.resolve("ibuprofen")

        assert resolution.resolved_name == "ibuprofen"
        assert resolution.changed is False

    def test_unknown_name_passes_through(self, offline_resolver):
        assert offline_resolver.resolved_name("unobtainium") == "unobtainium"

    def test_empty_name(self, offline_resolver):
        resolution = offline_resolver.resolve("")
        assert resolution.resolved_name == ""
        assert resolution.source == "none"

    def test_none_name(self, offline_resolver):
        assert offline_resolver.resolve(None).resolved_name == ""

    def test_override_table_maps_to_lowercase_usan(self):
        """Downstream matching is case-sensitive, so the values must be lower."""
        for inn, usan in INN_TO_USAN.items():
            assert inn == inn.lower()
            assert usan == usan.lower()

    def test_overrides_do_not_chain(self):
        """A USAN must not itself be an INN key, or resolution would be unstable."""
        for usan in INN_TO_USAN.values():
            if usan in INN_TO_USAN:
                assert INN_TO_USAN[usan] == usan, f"{usan} would resolve twice"


class TestRxNormLookup:
    def test_resolves_via_rxnorm_when_not_overridden(self, db_path, monkeypatch):
        stub_rxnav(
            monkeypatch,
            {"rxcui.json": RXCUI_PAYLOAD, "/related.json": INGREDIENT_PAYLOAD},
        )
        resolver = SynonymResolver(db_path)

        resolution = resolver.resolve("acetaminofen")

        assert resolution.resolved_name == "acetaminophen"
        assert resolution.rxcui == "161"
        assert resolution.source == "rxnorm"

    def test_uses_normalised_search(self, db_path, monkeypatch):
        """search=2 is what matches synonym spellings onto a concept."""
        calls = []
        stub_rxnav(
            monkeypatch,
            {"rxcui.json": RXCUI_PAYLOAD, "/related.json": INGREDIENT_PAYLOAD},
            recorder=calls,
        )

        SynonymResolver(db_path).resolve("acetaminofen")

        assert calls[0]["params"]["search"] == "2"
        assert calls[0]["url"].startswith(RXNAV_BASE_URL)

    def test_asks_for_the_ingredient_term_type(self, db_path, monkeypatch):
        calls = []
        stub_rxnav(
            monkeypatch,
            {"rxcui.json": RXCUI_PAYLOAD, "/related.json": INGREDIENT_PAYLOAD},
            recorder=calls,
        )

        SynonymResolver(db_path).resolve("acetaminofen")

        assert calls[1]["params"]["tty"] == "IN"

    def test_falls_back_to_concept_name_without_an_ingredient(self, db_path, monkeypatch):
        """Querying an ingredient concept returns no further ingredient."""
        stub_rxnav(
            monkeypatch,
            {
                "rxcui.json": RXCUI_PAYLOAD,
                "/related.json": {"relatedGroup": {"conceptGroup": []}},
                "/properties.json": {"properties": {"name": "Acetaminophen"}},
            },
        )

        assert SynonymResolver(db_path).resolved_name("xyz") == "acetaminophen"

    def test_unknown_name_returns_unchanged(self, db_path, monkeypatch):
        stub_rxnav(monkeypatch, {"rxcui.json": {"idGroup": {}}})

        resolution = SynonymResolver(db_path).resolve("notadrug")

        assert resolution.resolved_name == "notadrug"
        assert resolution.source == "none"

    def test_overrides_take_priority_over_the_network(self, db_path, monkeypatch):
        """An override must not cost an HTTP request."""
        calls = []
        stub_rxnav(monkeypatch, {"rxcui.json": RXCUI_PAYLOAD}, recorder=calls)

        assert SynonymResolver(db_path).resolved_name("paracetamol") == "acetaminophen"
        assert calls == []


class TestNetworkFailures:
    """A resolver outage must degrade to the original name, never raise."""

    def test_connection_error_returns_original(self, db_path, monkeypatch):
        stub_rxnav(monkeypatch, {"rxcui.json": http_client.ConnectionError("offline")})

        assert SynonymResolver(db_path).resolved_name("acetaminofen") == "acetaminofen"

    def test_timeout_returns_original(self, db_path, monkeypatch):
        stub_rxnav(monkeypatch, {"rxcui.json": http_client.Timeout("slow")})

        assert SynonymResolver(db_path).resolved_name("acetaminofen") == "acetaminofen"

    def test_error_status_returns_original(self, db_path, monkeypatch):
        def fake_get(url, timeout=None, params=None, **kwargs):
            return FakeResponse({}, status_code=503)

        monkeypatch.setattr(http_client, "get", fake_get)

        assert SynonymResolver(db_path).resolved_name("acetaminofen") == "acetaminofen"

    def test_unparseable_json_returns_original(self, db_path, monkeypatch):
        stub_rxnav(monkeypatch, {"rxcui.json": "<html>not json</html>"})

        assert SynonymResolver(db_path).resolved_name("acetaminofen") == "acetaminofen"

    def test_overrides_still_work_with_the_network_down(self, db_path, monkeypatch):
        stub_rxnav(monkeypatch, {"rxcui.json": http_client.ConnectionError("offline")})

        assert SynonymResolver(db_path).resolved_name("paracetamol") == "acetaminophen"


class TestCaching:
    def test_rxnorm_result_is_cached(self, db_path, monkeypatch):
        stub_rxnav(
            monkeypatch,
            {"rxcui.json": RXCUI_PAYLOAD, "/related.json": INGREDIENT_PAYLOAD},
        )
        SynonymResolver(db_path).resolve("acetaminofen")

        assert database.get_synonym("acetaminofen", db_path)["resolved_name"] == (
            "acetaminophen"
        )

    def test_second_lookup_skips_the_network(self, db_path, monkeypatch):
        calls = []
        stub_rxnav(
            monkeypatch,
            {"rxcui.json": RXCUI_PAYLOAD, "/related.json": INGREDIENT_PAYLOAD},
            recorder=calls,
        )
        resolver = SynonymResolver(db_path)

        resolver.resolve("acetaminofen")
        before = len(calls)
        resolution = resolver.resolve("acetaminofen")

        assert len(calls) == before, "cached name should not hit RxNav again"
        assert resolution.source == "cache"
        assert resolution.resolved_name == "acetaminophen"

    def test_cache_survives_a_new_resolver(self, db_path, monkeypatch):
        stub_rxnav(
            monkeypatch,
            {"rxcui.json": RXCUI_PAYLOAD, "/related.json": INGREDIENT_PAYLOAD},
        )
        SynonymResolver(db_path).resolve("acetaminofen")

        offline = SynonymResolver(db_path, use_network=False)
        assert offline.resolve("acetaminofen").source == "cache"

    def test_failed_resolution_is_not_cached(self, db_path, monkeypatch):
        stub_rxnav(monkeypatch, {"rxcui.json": {"idGroup": {}}})
        SynonymResolver(db_path).resolve("notadrug")

        assert database.get_synonym("notadrug", db_path) is None

    def test_unwritable_cache_does_not_break_resolution(self, tmp_path, monkeypatch):
        stub_rxnav(
            monkeypatch,
            {"rxcui.json": RXCUI_PAYLOAD, "/related.json": INGREDIENT_PAYLOAD},
        )
        resolver = SynonymResolver(tmp_path / "nonexistent" / "db.sqlite3")

        def explode(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(resolver._database, "save_synonym", explode)

        assert resolver.resolved_name("acetaminofen") == "acetaminophen"


class TestResolution:
    def test_changed_reflects_renaming(self):
        assert Resolution("paracetamol", "acetaminophen", "161", "override").changed
        assert not Resolution("ibuprofen", "ibuprofen", None, "none").changed


class TestNameListExpansion:
    """
    The matcher runs before the resolver, so its own list needs the INN
    spellings. This is the half a query-time-only fix misses.
    """

    def test_inn_is_added_when_the_usan_is_present(self):
        from tools.expand_name_list import expand

        expanded, additions = expand(["acetaminophen", "ibuprofen"])

        assert "paracetamol" in expanded
        assert ("paracetamol", "acetaminophen") in additions

    def test_inn_is_not_added_when_the_usan_is_absent(self):
        from tools.expand_name_list import expand

        expanded, _ = expand(["ibuprofen", "aspirin"])

        assert "paracetamol" not in expanded

    def test_existing_names_are_preserved(self):
        from tools.expand_name_list import expand

        original = ["acetaminophen", "ibuprofen", "aspirin"]
        expanded, _ = expand(list(original))

        assert expanded[: len(original)] == original

    def test_running_twice_adds_nothing_further(self):
        from tools.expand_name_list import expand

        once, _ = expand(["acetaminophen"])
        twice, additions = expand(once)

        assert additions == []
        assert once == twice

    def test_matcher_can_find_the_inn_after_expansion(self, tmp_path):
        """The end-to-end point: without this the request 422s before resolving."""
        from tools.expand_name_list import expand
        from utils.helper.drug_matcher import DrugMatcher

        us_only = tmp_path / "us.txt"
        us_only.write_text("acetaminophen\nibuprofen\n", encoding="utf-8")
        assert DrugMatcher(str(us_only)).lookup("paracetamol") == ""

        expanded, _ = expand(["acetaminophen", "ibuprofen"])
        both = tmp_path / "both.txt"
        both.write_text("\n".join(expanded) + "\n", encoding="utf-8")
        assert DrugMatcher(str(both)).lookup("paracetamol") == "paracetamol"
