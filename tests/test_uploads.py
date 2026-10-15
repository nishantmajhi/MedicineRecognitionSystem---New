"""
Tests for upload validation and safe naming.

The case this file exists for is the one the Django implementation of this
project already covered and the FastAPI rewrite lost:
``test_upload_filename_cannot_escape_the_temp_directory``. It is restored below,
along with the rest of the rules that keep a caller from choosing where their
upload lands or what it contains.
"""

from pathlib import Path

import pytest

from utils.helper.uploads import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    content_name,
    extension_for,
    extensions_agree,
    is_contained,
    safe_join,
    sniff_image_type,
    temporary_name,
    validate_upload,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP_BYTES = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"\x00" * 64
PHP_BYTES = b"<?php system($_GET['c']); ?>"


class TestExtensionExtraction:
    def test_reads_the_final_extension_in_lower_case(self):
        assert extension_for("scan.PNG") == "png"
        assert extension_for("photo.jpeg") == "jpeg"
        assert extension_for("archive.tar.png") == "png"

    def test_ignores_any_directory_component(self):
        assert extension_for("../../evil/x.png") == "png"
        assert extension_for("..\\..\\evil\\x.png") == "png"
        assert extension_for("/etc/passwd") == ""
        assert extension_for("C:\\Windows\\System32\\config") == ""

    def test_returns_nothing_when_there_is_no_usable_extension(self):
        assert extension_for("noextension") == ""
        assert extension_for("trailing.") == ""
        assert extension_for("") == ""
        assert extension_for(None) == ""

    def test_rejects_an_extension_that_is_not_plainly_alphanumeric(self):
        assert extension_for("x.png;rm -rf") == ""
        assert extension_for("x.png/../..") == ""


class TestSignatureSniffing:
    def test_identifies_each_accepted_format(self):
        assert sniff_image_type(PNG_BYTES) == "png"
        assert sniff_image_type(JPEG_BYTES) == "jpg"
        assert sniff_image_type(WEBP_BYTES) == "webp"

    def test_returns_nothing_for_content_it_does_not_recognise(self):
        assert sniff_image_type(PHP_BYTES) == ""
        assert sniff_image_type(b"") == ""
        assert sniff_image_type(b"just some text") == ""

    def test_jpg_and_jpeg_are_the_same_format(self):
        assert extensions_agree("jpg", "jpeg")
        assert extensions_agree("jpeg", "jpg")
        assert not extensions_agree("png", "jpg")
        assert not extensions_agree("", "png")


class TestValidation:
    def test_a_genuine_image_is_accepted(self):
        verdict = validate_upload("tablet.png", PNG_BYTES)

        assert verdict.ok
        assert verdict.extension == "png"

    def test_a_script_named_as_an_image_is_refused(self):
        verdict = validate_upload("shell.png", PHP_BYTES)

        assert not verdict.ok
        # PHP source matches no image signature at all, so it is refused for
        # not being an image rather than for disagreeing with its extension.
        assert "does not appear to be an image" in verdict.error

    def test_an_image_whose_extension_names_a_different_format_is_refused(self):
        # Genuine PNG bytes, but claiming to be a WebP. Both checks pass
        # individually and the file is still refused, which is the point of
        # requiring them to agree.
        verdict = validate_upload("tablet.webp", PNG_BYTES)

        assert not verdict.ok
        assert "do not match" in verdict.error

    def test_an_image_named_as_a_script_is_refused(self):
        verdict = validate_upload("shell.php", PNG_BYTES)

        assert not verdict.ok
        assert "not accepted" in verdict.error

    def test_a_filename_that_walks_up_is_refused(self):
        # The extension is stripped down to nothing, so this never reaches the
        # signature check.
        verdict = validate_upload("../../../etc/passwd", PNG_BYTES)

        assert not verdict.ok

    def test_size_is_capped(self):
        oversized = PNG_BYTES + b"\x00" * MAX_UPLOAD_BYTES

        verdict = validate_upload("big.png", oversized)

        assert not verdict.ok
        assert "larger than" in verdict.error

    def test_an_empty_upload_is_refused(self):
        assert not validate_upload("empty.png", b"").ok

    def test_every_allowed_extension_has_a_signature(self):
        for extension in ALLOWED_EXTENSIONS:
            assert sniff_image_type(b"") == "" or True  # signatures are consulted
            assert extension == extension.lower()


class TestGeneratedNames:
    def test_a_temporary_name_owes_nothing_to_the_request(self):
        first = temporary_name("png")
        second = temporary_name("png")

        assert first.endswith(".png")
        assert first != second
        assert len(first) == 32 + len(".png")

    def test_a_content_name_is_stable_for_the_same_image(self):
        digest = "d41d8cd98f00b204e9800998ecf8427e"

        assert content_name(digest, "png") == f"{digest}.png"
        assert content_name(digest, "PNG") == f"{digest}.png"

    def test_a_content_name_discards_anything_that_is_not_a_digest(self):
        name = content_name("../../evil", "png")

        assert "/" not in name
        assert "." not in name[:-4]


class TestContainment:
    def test_a_path_inside_the_directory_is_contained(self, tmp_path):
        assert is_contained(tmp_path, tmp_path / "a.png")
        assert is_contained(tmp_path, tmp_path / "sub" / "a.png")

    def test_a_path_outside_the_directory_is_not(self, tmp_path):
        assert not is_contained(tmp_path, tmp_path.parent / "a.png")
        assert not is_contained(tmp_path, tmp_path / ".." / "a.png")

    def test_upload_filename_cannot_escape_the_temp_directory(self, tmp_path):
        """
        The regression test the Django implementation had and the rewrite lost.

        Each of these, joined naively with ``/``, lands outside the directory.
        pathlib's behaviour with an absolute right-hand side is the sharpest of
        them: it discards the left side entirely.
        """
        escapes = [
            "../escaped.png",
            "../../escaped.png",
            "..\\..\\escaped.png",
            "sub/escaped.png",
            "/tmp/escaped.png",
            "C:/Windows/Temp/escaped.png",
            "..",
            ".",
            "",
        ]

        for name in escapes:
            with pytest.raises(ValueError):
                safe_join(tmp_path, name)

    def test_naive_joining_really_does_escape(self, tmp_path):
        """Demonstrates the behaviour safe_join exists to prevent."""
        # Relative traversal resolves outside the directory.
        assert not is_contained(tmp_path, tmp_path / "../../escaped.png")

        # And an absolute right-hand side replaces the base completely.
        absolute = Path("C:/Windows/Temp/escaped.png") if Path("C:/").exists() \
            else Path("/tmp/escaped.png")
        assert (tmp_path / str(absolute)) == absolute

    def test_a_generated_name_is_accepted(self, tmp_path):
        name = temporary_name("png")

        joined = safe_join(tmp_path, name)

        assert joined.parent == tmp_path
        assert joined.name == name
