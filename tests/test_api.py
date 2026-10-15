"""
Tests for routes.api and routes.health.

routes.api builds its service objects at import time, so the tests swap those
module-level instances for fakes. A minimal FastAPI app is assembled here rather
than importing main.py, which mounts a static directory and is sensitive to the
working directory.
"""

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import api as api_module
from routes import health as health_module


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(api_module.router)
    application.include_router(health_module.router)
    return application


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def jpeg_bytes(sample_image):
    ok, buffer = cv2.imencode(".jpg", sample_image)
    assert ok
    return buffer.tobytes()


class FakeImageProcessor:
    def __init__(self, result):
        self.result = result

    def process_image(self, image, file_name, md5_hash):
        return self.result


class FakeDrugMatcher:
    def __init__(self, drugs):
        self.drugs = drugs

    def identify_drugs(self, text_list):
        return self.drugs


class FakeDrugInformation:
    def __init__(self, info):
        self.info = info

    def get_multiple_drugs_info(self, drug_names):
        return self.info


@pytest.fixture
def wire_services(monkeypatch, tmp_path):
    """Install fake services and keep temp image writes inside tmp_path."""
    monkeypatch.chdir(tmp_path)

    def configure(extracted_text=None, drugs=None, info=None):
        monkeypatch.setattr(
            api_module, "image_processor", FakeImageProcessor(extracted_text)
        )
        monkeypatch.setattr(api_module, "drug_matcher", FakeDrugMatcher(drugs or []))
        monkeypatch.setattr(
            api_module, "drug_info_service", FakeDrugInformation(info or {})
        )

    return configure


IBUPROFEN_INFO = {
    0: {
        "generic_name": "ibuprofen",
        "purpose": "pain reliever",
        "do_not_use": "if allergic to nsaids",
    }
}


class TestUploadSuccess:
    def test_returns_medicine_information(self, client, wire_services, jpeg_bytes):
        wire_services(["ibuprofen"], ["ibuprofen"], IBUPROFEN_INFO)

        response = client.post(
            "/api/upload", files={"photo": ("pill.jpg", jpeg_bytes, "image/jpeg")}
        )

        assert response.status_code == 200
        assert response.json() == {
            "genericName": "Ibuprofen",
            "purpose": ["Pain reliever"],
            "do_not_use": ["If allergic to nsaids"],
        }

    def test_generic_name_is_title_cased(self, client, wire_services, jpeg_bytes):
        wire_services(["text"], ["ibuprofen"], IBUPROFEN_INFO)

        response = client.post(
            "/api/upload", files={"photo": ("pill.jpg", jpeg_bytes, "image/jpeg")}
        )

        assert response.json()["genericName"] == "Ibuprofen"

    def test_multiple_medicines_are_comma_separated(self, client, wire_services, jpeg_bytes):
        info = {
            0: {"generic_name": "ibuprofen", "purpose": "pain", "do_not_use": "nsaids"},
            1: {"generic_name": "aspirin", "purpose": "fever", "do_not_use": "ulcers"},
        }
        wire_services(["text"], ["ibuprofen", "aspirin"], info)

        payload = client.post(
            "/api/upload", files={"photo": ("pill.jpg", jpeg_bytes, "image/jpeg")}
        ).json()

        assert payload["genericName"] == "Ibuprofen, Aspirin"
        assert payload["purpose"] == ["Pain", "Fever"]
        assert payload["do_not_use"] == ["Nsaids", "Ulcers"]

    def test_no_trailing_separator(self, client, wire_services, jpeg_bytes):
        wire_services(["text"], ["ibuprofen"], IBUPROFEN_INFO)

        name = client.post(
            "/api/upload", files={"photo": ("pill.jpg", jpeg_bytes, "image/jpeg")}
        ).json()["genericName"]

        assert not name.endswith(", ")

    def test_accepts_png(self, client, wire_services, sample_image):
        wire_services(["ibuprofen"], ["ibuprofen"], IBUPROFEN_INFO)
        ok, buffer = cv2.imencode(".png", sample_image)

        response = client.post(
            "/api/upload", files={"photo": ("pill.png", buffer.tobytes(), "image/png")}
        )

        assert response.status_code == 200


class TestUploadFailures:
    def test_missing_file_is_rejected(self, client, wire_services):
        wire_services(["ibuprofen"], ["ibuprofen"], IBUPROFEN_INFO)
        assert client.post("/api/upload").status_code == 422

    def test_undecodable_image_returns_415(self, client, wire_services):
        wire_services(["ibuprofen"], ["ibuprofen"], IBUPROFEN_INFO)

        response = client.post(
            "/api/upload",
            files={"photo": ("notes.txt", b"this is not an image", "text/plain")},
        )

        assert response.status_code == 415

    def test_no_text_extracted_returns_422(self, client, wire_services, jpeg_bytes):
        wire_services([], [], {})

        response = client.post(
            "/api/upload", files={"photo": ("blank.jpg", jpeg_bytes, "image/jpeg")}
        )

        assert response.status_code == 422

    def test_no_drugs_identified_returns_422(self, client, wire_services, jpeg_bytes):
        wire_services(["tablets", "400mg"], [], {})

        response = client.post(
            "/api/upload", files={"photo": ("box.jpg", jpeg_bytes, "image/jpeg")}
        )

        assert response.status_code == 422

    def test_processing_error_returns_500(self, client, wire_services, jpeg_bytes):
        """process_image returns None to signal an internal failure."""
        wire_services(None, [], {})

        response = client.post(
            "/api/upload", files={"photo": ("pill.jpg", jpeg_bytes, "image/jpeg")}
        )

        assert response.status_code == 500

    def test_unexpected_error_returns_500(self, client, wire_services, jpeg_bytes, monkeypatch):
        wire_services(["ibuprofen"], ["ibuprofen"], IBUPROFEN_INFO)

        class ExplodingService:
            def get_multiple_drugs_info(self, drug_names):
                raise RuntimeError("database is on fire")

        monkeypatch.setattr(api_module, "drug_info_service", ExplodingService())

        response = client.post(
            "/api/upload", files={"photo": ("pill.jpg", jpeg_bytes, "image/jpeg")}
        )

        assert response.status_code == 500


class TestHealth:
    def test_reports_healthy(self, client):
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_includes_version_and_timestamp(self, client):
        payload = client.get("/health").json()

        assert payload["version"] == "2.0.0"
        assert "timestamp" in payload
