"""
Validation and safe naming for uploaded images.

Two things here are load-bearing.

**The stored name never comes from the request.** ``photo.filename`` is chosen
by whoever is calling. Joining it onto a directory with ``Path`` does not
confine it: ``Path("MRS/temp") / "../../main.py"`` is a path that resolves
outside the directory, and — the sharper case — when the right-hand side is
absolute ``pathlib`` discards the left entirely, so
``Path("MRS/temp") / "C:/inetpub/wwwroot/x.png"`` *is*
``C:/inetpub/wwwroot/x.png``. Anything the process can write, a caller could
overwrite.

**The file is identified by its bytes, not by its name.** An extension check
alone accepts a script called ``x.png``; a signature check alone accepts a real
PNG called ``x.py``. Both are required to agree.

The Django implementation of this project used ``get_valid_filename`` and had a
regression test named ``test_upload_filename_cannot_escape_the_temp_directory``.
Both were lost in the rewrite to FastAPI. This module and
``tests/test_uploads.py`` restore them.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Largest upload accepted, in bytes. Enforced here rather than left to the
# server so the limit is visible in code and the same wherever this runs.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Extension -> the leading bytes a genuine file of that type begins with.
# WebP needs two checks: bytes 0-3 are "RIFF", 4-7 are the length, 8-11 "WEBP".
_SIGNATURES: dict[str, tuple[tuple[int, bytes], ...]] = {
    "png": ((0, b"\x89PNG\r\n\x1a\n"),),
    "jpg": ((0, b"\xff\xd8\xff"),),
    "jpeg": ((0, b"\xff\xd8\xff"),),
    "webp": ((0, b"RIFF"), (8, b"WEBP")),
    "bmp": ((0, b"BM"),),
    "tif": ((0, b"II*\x00"),),
    "tiff": ((0, b"MM\x00*"),),
}

# Extensions that name the same format, so a signature match under one spelling
# is a match under the other.
_EQUIVALENT = {
    "jpg": "jpeg",
    "jpeg": "jpg",
    "tif": "tiff",
    "tiff": "tif",
}

ALLOWED_EXTENSIONS = frozenset(_SIGNATURES)


@dataclass(frozen=True)
class UploadVerdict:
    """The outcome of checking one upload."""

    ok: bool
    error: str = ""
    extension: str = ""


def extension_for(client_filename: Optional[str]) -> str:
    """
    The lowercased extension of a client-supplied filename, without the dot.

    Only the final path segment is considered, so a name carrying directory
    separators of either kind cannot smuggle one into the result.
    """
    if not client_filename:
        return ""

    base = Path(str(client_filename).replace("\\", "/")).name
    _, dot, suffix = base.rpartition(".")

    if not dot or not suffix:
        return ""

    suffix = suffix.lower()

    # An "extension" containing anything but letters and digits is not one.
    return suffix if re.fullmatch(r"[a-z0-9]{1,8}", suffix) else ""


def sniff_image_type(data: bytes) -> str:
    """
    Identify image bytes by their signature, returning an extension or "".

    Used instead of a MIME library so the answer does not depend on which
    optional packages happen to be installed on the host.
    """
    for extension, checks in _SIGNATURES.items():
        if all(data[offset:offset + len(magic)] == magic for offset, magic in checks):
            return extension

    return ""


def extensions_agree(claimed: str, sniffed: str) -> bool:
    """Whether a claimed extension and a sniffed one describe the same format."""
    if not claimed or not sniffed:
        return False

    return claimed == sniffed or _EQUIVALENT.get(claimed) == sniffed


def validate_upload(
    client_filename: Optional[str],
    data: bytes,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> UploadVerdict:
    """Decide whether an uploaded file may be processed."""
    if not data:
        return UploadVerdict(False, "The uploaded file is empty.")

    if len(data) > max_bytes:
        return UploadVerdict(
            False,
            f"The image is larger than the {max_bytes // (1024 * 1024)} MB limit.",
        )

    claimed = extension_for(client_filename)

    if not claimed:
        return UploadVerdict(False, "The file has no usable extension.")

    if claimed not in ALLOWED_EXTENSIONS:
        return UploadVerdict(False, f'Files of type ".{claimed}" are not accepted.')

    sniffed = sniff_image_type(data)

    if not sniffed:
        return UploadVerdict(False, "The file does not appear to be an image.")

    if not extensions_agree(claimed, sniffed):
        return UploadVerdict(False, "The file contents do not match its extension.")

    return UploadVerdict(True, extension=claimed)


def temporary_name(extension: str) -> str:
    """A storage name owing nothing to the request: random hex plus the extension."""
    return f"{secrets.token_hex(16)}.{extension.lower()}"


def content_name(digest: str, extension: str) -> str:
    """
    A storage name derived from the image's own digest.

    Two uploads of the same picture land on the same name, which is what makes
    the media directory de-duplicate instead of accumulating a copy per request.
    """
    safe_digest = re.sub(r"[^0-9a-fA-F]", "", digest)[:64] or secrets.token_hex(16)

    return f"{safe_digest}.{extension.lower()}"


def is_contained(base: Path, candidate: Path) -> bool:
    """
    Whether ``candidate`` resolves to something inside ``base``.

    Both are resolved first, so symlinks and ``..`` are accounted for rather
    than compared as text.
    """
    try:
        resolved_base = base.resolve()
        resolved_candidate = candidate.resolve()
    except OSError:
        return False

    return resolved_candidate == resolved_base or resolved_base in resolved_candidate.parents


def safe_join(base: Path, name: str) -> Path:
    """
    Join a single filename onto a directory, refusing anything else.

    ``name`` must be one plain path component. This is the last line of defence:
    every caller is expected to be passing a name this module generated, and a
    failure here means one of them is not.
    """
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"unsafe upload filename: {name!r}")

    candidate = base / name

    if not is_contained(base, candidate.parent if candidate.parent != base else base):
        raise ValueError(f"upload path escapes its directory: {name!r}")

    return candidate
