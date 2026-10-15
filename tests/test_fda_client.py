"""
Tests for utils.helper.fda_client.

Every test stubs ``requests.get``. The suite must never touch the real openFDA
API: that would make results depend on network availability and on a third
party's rate limits.
"""

import pytest

from models import database
from src import http_client
from src.synonym_resolver import SynonymResolver
from utils.helper.fda_client import FDAClient


@pytest.fixture
def offline_resolver(tmp_path):
    """
    A resolver that never touches the network.

    FDAClient builds a real SynonymResolver by default, and its HTTP calls
    would otherwise land in the same stub as the FDA request, making assertions
    about "the first call" ambiguous. Injecting an offline resolver keeps these
    tests about FDAClient alone.
    """
    path = tmp_path / "synonyms.sqlite3"
    database.init_db(path)
    return SynonymResolver(path, use_network=False)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def stub_get(monkeypatch, response=None, error=None, recorder=None):
    """Replace the HTTP transport with a stub that records the URL it saw."""

    def fake_get(url, timeout=None, **kwargs):
        if recorder is not None:
            recorder.append({"url": url, "timeout": timeout})
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(http_client, "get", fake_get)


class TestQuery:
    def test_returns_parsed_drug_information(self, monkeypatch, offline_resolver, fda_payload):
        stub_get(monkeypatch, FakeResponse(200, fda_payload))

        result = FDAClient(resolver=offline_resolver).query("ibuprofen")

        assert result == {
            "generic_name": "ibuprofen",
            "purpose": "pain reliever/fever reducer",
            "do_not_use": "do not use with other drugs containing nsaids",
        }

    def test_values_are_lowercased(self, monkeypatch, offline_resolver):
        payload = {"results": [{"purpose": ["PAIN RELIEVER"], "do_not_use": ["ASK A DOCTOR"]}]}
        stub_get(monkeypatch, FakeResponse(200, payload))

        result = FDAClient(resolver=offline_resolver).query("aspirin")

        assert result["purpose"] == "pain reliever"
        assert result["do_not_use"] == "ask a doctor"

    def test_queries_the_generic_name_endpoint(self, monkeypatch, offline_resolver, fda_payload):
        calls = []
        stub_get(monkeypatch, FakeResponse(200, fda_payload), recorder=calls)

        FDAClient(resolver=offline_resolver).query("ibuprofen")

        assert "openfda.generic_name" in calls[0]["url"]
        assert "ibuprofen" in calls[0]["url"]

    def test_timeout_is_passed_through(self, monkeypatch, offline_resolver, fda_payload):
        calls = []
        stub_get(monkeypatch, FakeResponse(200, fda_payload), recorder=calls)

        FDAClient(timeout=3, resolver=offline_resolver).query("aspirin")

        assert calls[0]["timeout"] == 3


class TestParacetamolAlias:
    """
    openFDA indexes the US name. A Nepali box says "paracetamol", so the client
    queries "acetaminophen" and translates the answer back.
    """

    def test_queries_acetaminophen_upstream(self, monkeypatch, offline_resolver, fda_payload):
        calls = []
        stub_get(monkeypatch, FakeResponse(200, fda_payload), recorder=calls)

        FDAClient(resolver=offline_resolver).query("paracetamol")

        assert "acetaminophen" in calls[0]["url"]
        assert "paracetamol" not in calls[0]["url"]

    def test_result_is_renamed_back_to_paracetamol(self, monkeypatch, offline_resolver, fda_payload):
        stub_get(monkeypatch, FakeResponse(200, fda_payload))

        result = FDAClient(resolver=offline_resolver).query("paracetamol")

        assert result["generic_name"] == "paracetamol"

    def test_alias_is_case_insensitive(self, monkeypatch, offline_resolver, fda_payload):
        calls = []
        stub_get(monkeypatch, FakeResponse(200, fda_payload), recorder=calls)

        FDAClient(resolver=offline_resolver).query("Paracetamol")

        assert "acetaminophen" in calls[0]["url"]

    def test_substitution_rewrites_body_text(self, monkeypatch, offline_resolver):
        payload = {
            "results": [
                {
                    "purpose": ["Acetaminophen is a pain reliever"],
                    "do_not_use": ["with other ACETAMINOPHEN products"],
                }
            ]
        }
        stub_get(monkeypatch, FakeResponse(200, payload))

        result = FDAClient(resolver=offline_resolver).query("paracetamol")

        # _parse_response lowercases before the substitution runs, so the
        # capitalised/uppercase branches of _replace_generic_name never fire on
        # this path. Everything arrives lowercase.
        assert "acetaminophen" not in result["purpose"]
        assert "paracetamol" in result["purpose"]
        assert result["do_not_use"] == "with other paracetamol products"

    def test_acetaminophen_query_is_not_renamed(self, monkeypatch, offline_resolver, fda_payload):
        stub_get(monkeypatch, FakeResponse(200, fda_payload))

        result = FDAClient(resolver=offline_resolver).query("acetaminophen")

        assert result["generic_name"] == "acetaminophen"


class TestFailureHandling:
    """Upstream problems must degrade to None, never propagate an exception."""

    def test_non_200_status_returns_none(self, monkeypatch, offline_resolver):
        stub_get(monkeypatch, FakeResponse(404, {}))
        assert FDAClient(resolver=offline_resolver).query("nosuchdrug") is None

    def test_rate_limited_returns_none(self, monkeypatch, offline_resolver):
        stub_get(monkeypatch, FakeResponse(429, {}))
        assert FDAClient(resolver=offline_resolver).query("ibuprofen") is None

    def test_timeout_returns_none(self, monkeypatch, offline_resolver):
        stub_get(monkeypatch, error=http_client.Timeout("timed out"))
        assert FDAClient(resolver=offline_resolver).query("ibuprofen") is None

    def test_connection_error_returns_none(self, monkeypatch, offline_resolver):
        stub_get(monkeypatch, error=http_client.ConnectionError("offline"))
        assert FDAClient(resolver=offline_resolver).query("ibuprofen") is None

    def test_empty_results_returns_none(self, monkeypatch, offline_resolver):
        stub_get(monkeypatch, FakeResponse(200, {"results": []}))
        assert FDAClient(resolver=offline_resolver).query("ibuprofen") is None

    def test_missing_results_key_returns_none(self, monkeypatch, offline_resolver):
        stub_get(monkeypatch, FakeResponse(200, {"error": "NOT_FOUND"}))
        assert FDAClient(resolver=offline_resolver).query("ibuprofen") is None

    def test_missing_fields_default_to_empty_strings(self, monkeypatch, offline_resolver):
        stub_get(monkeypatch, FakeResponse(200, {"results": [{}]}))

        result = FDAClient(resolver=offline_resolver).query("ibuprofen")

        assert result == {"generic_name": "ibuprofen", "purpose": "", "do_not_use": ""}
