"""Thumbnail generation unit tests (attachment.md §1.2 A4/§3.3)."""

from __future__ import annotations

import pytest

from mesh.attachment.thumbnails import (
    THUMBNAIL_SIZES,
    ThumbnailError,
    is_thumbnailable,
    make_thumbnails,
)

pytestmark = pytest.mark.unit


async def test_generates_three_renditions_with_dimensions():
    from tests.unit.attachment_support import make_png

    info = await make_thumbnails(make_png(800, 600), source_mime="image/png")
    assert info.width == 800
    assert info.height == 600
    assert set(info.renditions) == set(THUMBNAIL_SIZES)
    for _name, (payload, content_type) in info.renditions.items():
        assert payload[:4] == b"\x89PNG"  # PNG source stays PNG
        assert content_type == "image/png"
        assert len(payload) > 0


async def test_jpeg_source_produces_jpeg_renditions():
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), color=(10, 100, 200)).save(buffer, format="JPEG")
    info = await make_thumbnails(buffer.getvalue(), source_mime="image/jpeg")
    assert info.width == 100
    for _, (payload, content_type) in info.renditions.items():
        assert content_type == "image/jpeg"
        assert payload[:3] == b"\xff\xd8\xff"


async def test_corrupt_image_raises_thumbnail_error():
    with pytest.raises(ThumbnailError):
        await make_thumbnails(b"\x89PNG\r\n\x1a\nGARBAGE-NOT-REALLY-PNG", source_mime="image/png")


def test_thumbnailable_predicate():
    assert is_thumbnailable("image/png") is True
    assert is_thumbnailable("image/jpeg") is True
    # SVG rasterization is an injection surface — excluded.
    assert is_thumbnailable("image/svg+xml") is False
    assert is_thumbnailable("application/pdf") is False
    assert is_thumbnailable(None) is False
