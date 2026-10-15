"""Tests for utils.helper.hash_generator."""

import hashlib

from utils.helper.hash_generator import HashGenerator


class TestGenerateMd5:
    def test_matches_hashlib(self, tmp_path):
        path = tmp_path / "image.jpg"
        payload = b"\xff\xd8\xff\xe0 fake jpeg bytes"
        path.write_bytes(payload)

        assert HashGenerator.generate_md5(str(path)) == hashlib.md5(payload).hexdigest()

    def test_is_deterministic(self, tmp_path):
        path = tmp_path / "image.jpg"
        path.write_bytes(b"same content")
        assert HashGenerator.generate_md5(str(path)) == HashGenerator.generate_md5(str(path))

    def test_identical_content_hashes_alike(self, tmp_path):
        """Cache hits depend on content, not filename."""
        first = tmp_path / "a.jpg"
        second = tmp_path / "b.jpg"
        first.write_bytes(b"identical")
        second.write_bytes(b"identical")

        assert HashGenerator.generate_md5(str(first)) == HashGenerator.generate_md5(str(second))

    def test_different_content_hashes_differently(self, tmp_path):
        first = tmp_path / "a.jpg"
        second = tmp_path / "b.jpg"
        first.write_bytes(b"one")
        second.write_bytes(b"two")

        assert HashGenerator.generate_md5(str(first)) != HashGenerator.generate_md5(str(second))

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.jpg"
        path.write_bytes(b"")
        assert HashGenerator.generate_md5(str(path)) == hashlib.md5(b"").hexdigest()

    def test_missing_file_returns_empty_string(self, tmp_path):
        """Failure is swallowed and logged rather than raised."""
        assert HashGenerator.generate_md5(str(tmp_path / "nope.jpg")) == ""

    def test_hash_is_32_hex_characters(self, tmp_path):
        path = tmp_path / "image.jpg"
        path.write_bytes(b"content")
        digest = HashGenerator.generate_md5(str(path))

        assert len(digest) == 32
        assert all(character in "0123456789abcdef" for character in digest)
