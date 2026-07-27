"""MIME sniffing unit tests (attachment.md §3.3/§4.6)."""

from __future__ import annotations

import pytest

from mesh.attachment.mime import (
    EXECUTABLE_MIMES,
    IMAGE_MIMES,
    OCTET_STREAM,
    extension_for_mime,
    sniff_mime,
)

pytestmark = pytest.mark.unit


def _png() -> bytes:
    from tests.unit.attachment_support import make_png

    return make_png()


def test_sniffs_common_image_types():
    assert sniff_mime(_png()) == "image/png"
    assert sniff_mime(b"\xff\xd8\xff\xe0" + b"\x00" * 32) == "image/jpeg"
    assert sniff_mime(b"GIF89a" + b"\x00" * 16) == "image/gif"
    assert sniff_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff_mime(b"BM" + b"\x00" * 32) == "image/bmp"


def test_riff_without_webp_is_not_webp():
    assert sniff_mime(b"RIFF\x00\x00\x00\x00WAVE\x00fmt ") != "image/webp"


def test_sniffs_documents_and_archives():
    assert sniff_mime(b"%PDF-1.7\n") == "application/pdf"
    assert sniff_mime(b"PK\x03\x04rest") == "application/zip"
    assert sniff_mime(b"\x1f\x8b\x08\x00") == "application/gzip"


def test_sniffs_executable_containers():
    assert sniff_mime(b"MZ" + b"\x00" * 64) == "application/x-msdownload"
    assert sniff_mime(b"\x7fELF\x02\x01\x01") == "application/x-elf"
    assert sniff_mime(b"\xcf\xfa\xed\xfe" + b"\x00" * 16) == "application/x-mach-binary"
    assert "application/x-msdownload" in EXECUTABLE_MIMES


def test_sniffs_svg():
    assert sniff_mime(b"<svg xmlns='x'></svg>") == "image/svg+xml"
    assert sniff_mime(b"<?xml version='1.0'?>\n<svg></svg>") == "image/svg+xml"


def test_plain_text_detection():
    assert sniff_mime(b"hello world\nthis is a log line\n") == "text/plain"


def test_unknown_binary_falls_back():
    assert sniff_mime(bytes(range(128, 192)) * 4) == OCTET_STREAM
    assert sniff_mime(b"") == OCTET_STREAM
    assert "image/png" in IMAGE_MIMES


def test_extension_mapping():
    assert extension_for_mime("image/png") == "png"
    assert extension_for_mime("application/pdf") == "pdf"
    assert extension_for_mime(OCTET_STREAM) == "bin"
    assert extension_for_mime("application/x-unknown") == "bin"
