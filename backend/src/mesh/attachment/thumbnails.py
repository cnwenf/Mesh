"""Thumbnail generation (attachment.md §1.2 A4 / §3.3 — sm/md/lg renditions).

Runs in the quarantine worker AFTER the blob passes the scan gate; renditions
are uploaded next to the original and their object keys written back to
``attachment_blobs.thumbnail_keys``. Uses Pillow inside a worker thread —
``Image.open`` is wrapped so a corrupt/truncated image raises ``ThumbnailError``
(which the pipeline maps to scan_status='error', never a worker crash), and
``MAX_IMAGE_PIXELS`` guards against decompression-bomb memory blowups.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass

from mesh.attachment.mime import IMAGE_MIMES

# Rendition max edge in pixels (spec: sm/md/lg multi-size).
THUMBNAIL_SIZES: dict[str, int] = {"sm": 320, "md": 640, "lg": 1280}

# Decompression-bomb guard: images above this pixel count are processed but
# Pillow refuses to load monsters — surfaced as a scan error, not a crash.
MAX_IMAGE_PIXELS = 100_000_000


class ThumbnailError(RuntimeError):
    """Image bytes could not be decoded into a bitmap."""


@dataclass(frozen=True)
class ImageInfo:
    """Decoded image metadata + encoded renditions (worker uploads the blobs)."""

    width: int
    height: int
    # size name → (object payload, content_type)
    renditions: dict[str, tuple[bytes, str]]


def is_thumbnailable(mime: str | None) -> bool:
    """SVG is excluded — rasterizing untrusted SVG is an injection surface."""
    return mime in IMAGE_MIMES and mime != "image/svg+xml"


async def make_thumbnails(data: bytes, *, source_mime: str) -> ImageInfo:
    """Decode once; produce width/height + sm/md/lg payloads."""
    return await asyncio.to_thread(_make_thumbnails_sync, data, source_mime)


def _make_thumbnails_sync(data: bytes, source_mime: str) -> ImageInfo:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
            width, height = image.size
            # Normalize for encoding: palette/RGBA flattened onto a RGB canvas
            # for JPEG, preserved for PNG.
            use_png = source_mime == "image/png" or image.mode in {"RGBA", "LA", "P"}
            renditions: dict[str, tuple[bytes, str]] = {}
            for name, edge in THUMBNAIL_SIZES.items():
                thumb = image.copy()
                thumb.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                if use_png:
                    if thumb.mode not in {"RGB", "RGBA"}:
                        thumb = thumb.convert("RGBA")
                    thumb.save(buffer, format="PNG", optimize=True)
                    content_type = "image/png"
                else:
                    if thumb.mode != "RGB":
                        thumb = thumb.convert("RGB")
                    thumb.save(buffer, format="JPEG", quality=85, optimize=True)
                    content_type = "image/jpeg"
                renditions[name] = (buffer.getvalue(), content_type)
    except ThumbnailError:
        raise
    except Exception as exc:  # noqa: BLE001 — any decode failure is a scan error
        raise ThumbnailError(f"image decode failed: {type(exc).__name__}") from exc
    return ImageInfo(width=width, height=height, renditions=renditions)
