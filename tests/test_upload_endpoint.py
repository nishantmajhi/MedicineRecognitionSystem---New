"""
End-to-end tests for POST /api/upload, driven through the real HTTP layer.

tests/test_uploads.py covers the rules in isolation. These prove the endpoint
actually applies them, because the defect being guarded against was not a wrong
rule but a correct-looking handler that never consulted one: it took
``photo.filename`` and joined it onto a directory.

EasyOCR is never loaded. The image processor and the drug lookups are replaced
with stubs, so nothing here needs a model or a network.
"""

from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.api as api

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

PHP_BYTES = b"<?php system($_GET['c']); ?>"


class StubProcessor:
    """Records the name it is asked to store under, and returns fixed text."""

    def __init__(self):
        self.names = []

    def process_image(self, image, file_name, md5_hash):
        self.names.append(file_name)
        return ["ibuprofen"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A test client with the OCR and lookup layers stubbed out."""
    monkeypatch.chdir(tmp_path)

    processor = StubProcessor()
    monkeypatch.setattr(api, "image_processor", processor)
    monkeypatch.setattr(
        api.drug_matcher, "identify_drugs", lambda texts: ["ibuprofen"]
    )
    monkeypatch.setattr(
        api.drug_info_service,
        "get_multiple_drugs_info",
        lambda drugs: {
            "ibuprofen": {
                "generic_name": "ibuprofen",
                "purpose": "pain relief",
                "do_not_use": "if allergic to nsaids",
            }
        },
    )

    app = FastAPI()
    app.include_router(api.router)

    test_client = TestClient(app)
    test_client.processor = processor
    test_client.root = tmp_path

    return test_client


def upload(client, filename, content):
    return client.post(
        "/api/upload",
        files={"photo": (filename, content, "image/png")},
    )


class TestTraversal:
    """
    The regression tests for the defect this endpoint had until 2026-09-05.

    ``temp_dir / photo.filename`` does not confine anything. A relative name
    walks up; an absolute one replaces the directory outright.
    """

    @pytest.mark.parametrize(
        "filename",
        [
            "../escaped.png",
            "../../escaped.png",
            "..\\..\\escaped.png",
            "../../../escaped.png",
            "subdir/escaped.png",
        ],
    )
    def test_a_traversing_filename_writes_nothing_outside_the_temp_directory(
        self, client, filename
    ):
        response = upload(client, filename, PNG_BYTES)

        # Rather than guessing where a given payload would land, assert on every
        # file that appeared: the only ones permitted are inside MRS/media and
        # MRS/temp. Checking a fixed list of candidate paths would let a payload
        # that escaped somewhere unanticipated pass unnoticed.
        allowed_roots = [
            (client.root / "MRS" / "media").resolve(),
            (client.root / "MRS" / "temp").resolve(),
            (client.root / "MRS" / "cache").resolve(),
        ]

        for written in client.root.rglob("*"):
            if not written.is_file():
                continue

            resolved = written.resolve()

            assert any(
                root == resolved.parent or root in resolved.parents
                for root in allowed_roots
            ), f"{filename} caused a write to {resolved}"

        # And nothing at all above the working directory.
        assert not (client.root.parent / "escaped.png").exists(), \
            f"{filename} escaped above the working directory"

        assert response.status_code in (200, 415)

    def test_an_absolute_filename_does_not_choose_the_destination(self, client, tmp_path):
        target = tmp_path.parent / "absolute-escape.png"

        upload(client, str(target), PNG_BYTES)

        assert not target.exists(), "an absolute filename must not decide where the file lands"

    def test_the_stored_name_never_comes_from_the_request(self, client):
        upload(client, "my-medicine-photo.png", PNG_BYTES)

        assert client.processor.names, "the processor should have been called"

        for name in client.processor.names:
            assert "my-medicine-photo" not in name
            assert "/" not in name and "\\" not in name
            assert name.endswith(".png")


class TestContentChecking:
    def test_a_script_named_as_an_image_is_refused(self, client):
        response = upload(client, "shell.png", PHP_BYTES)

        assert response.status_code == 415

    def test_an_image_named_as_a_script_is_refused(self, client):
        response = upload(client, "shell.php", PNG_BYTES)

        assert response.status_code == 415

    def test_a_genuine_image_is_accepted(self, client):
        response = upload(client, "tablet.png", PNG_BYTES)

        assert response.status_code == 200
        assert response.json()["genericName"] == "Ibuprofen"

    def test_an_empty_upload_is_refused(self, client):
        response = upload(client, "empty.png", b"")

        assert response.status_code == 415


class TestTemporaryFiles:
    def test_the_temporary_copy_is_removed(self, client):
        upload(client, "tablet.png", PNG_BYTES)

        temp_dir = client.root / "MRS" / "temp"

        # The directory may exist; it must not still hold the upload.
        leftovers = list(temp_dir.glob("*")) if temp_dir.is_dir() else []
        assert leftovers == [], f"temporary files were left behind: {leftovers}"

    def test_the_temporary_copy_is_removed_even_when_processing_fails(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            api.image_processor, "process_image", lambda *args, **kwargs: None
        )

        response = upload(client, "tablet.png", PNG_BYTES)

        assert response.status_code == 500

        temp_dir = client.root / "MRS" / "temp"
        leftovers = list(temp_dir.glob("*")) if temp_dir.is_dir() else []
        assert leftovers == [], f"temporary files were left behind: {leftovers}"
