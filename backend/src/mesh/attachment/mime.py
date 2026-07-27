"""Magic-byte MIME sniffing (attachment.md §3.3/§4.6 — server-side truth).

The quarantine worker never trusts the client's ``Content-Type`` or the
object's extension: the real MIME is sniffed from the first bytes of the
object. A curated signature table covers every MIME on the default allowlist
plus the common executable containers (so a forged image extension over PE/ELF
bytes is detected). Unknown bytes sniff to ``application/octet-stream``.
"""

from __future__ import annotations

OCTET_STREAM = "application/octet-stream"

# (offset, magic bytes, mime) — first match wins, longest-first within offset.
_MAGIC_SIGNATURES: tuple[tuple[int, bytes, str], ...] = (
    (0, b"\x89PNG\r\n\x1a\n", "image/png"),
    (0, b"\xff\xd8\xff", "image/jpeg"),
    (0, b"GIF87a", "image/gif"),
    (0, b"GIF89a", "image/gif"),
    (0, b"RIFF", "image/webp"),  # second-stage WEBP check below
    (0, b"BM", "image/bmp"),
    (0, b"%PDF-", "application/pdf"),
    (0, b"PK\x03\x04", "application/zip"),
    (0, b"PK\x05\x06", "application/zip"),
    (0, b"PK\x07\x08", "application/zip"),
    (0, b"\x1f\x8b", "application/gzip"),
    (0, b"MZ", "application/x-msdownload"),  # PE/DLL/exe
    (0, b"\x7fELF", "application/x-elf"),
    (0, b"\xfe\xed\xfa", "application/x-mach-binary"),
    (0, b"\xcf\xfa\xed\xfe", "application/x-mach-binary"),
    (0, b"\xca\xfe\xba\xbe", "application/x-mach-binary"),
)

# Canonical extension per sniffed MIME (worker writes blob.extension).
MIME_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/svg+xml": "svg",
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/csv": "csv",
    "application/zip": "zip",
    "application/gzip": "gz",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/octet-stream": "bin",
}

IMAGE_MIMES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/svg+xml"}
)

# Executable containers — never inline-rendered, downloads forced to
# attachment with a warning (§3.4/§3.6).
EXECUTABLE_MIMES: frozenset[str] = frozenset(
    {"application/x-msdownload", "application/x-elf", "application/x-mach-binary"}
)


def sniff_mime(data: bytes) -> str:
    """Best-effort MIME from magic bytes; ``application/octet-stream`` fallback."""
    if not data:
        return OCTET_STREAM
    for offset, magic, mime in _MAGIC_SIGNATURES:
        if data[offset : offset + len(magic)] == magic:
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue  # RIFF container but not WebP
            return mime
    if _looks_like_svg(data):
        return "image/svg+xml"
    if _looks_like_text(data):
        return "text/plain"
    return OCTET_STREAM


def _looks_like_svg(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    if head.startswith(b"<svg"):
        return True
    return head.startswith(b"<?xml") and b"<svg" in data[:4096].lower()


def _looks_like_text(data: bytes) -> bool:
    """Heuristic: a printable UTF-8 prefix with no NUL bytes reads as text."""
    sample = data[:8192]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def extension_for_mime(mime: str) -> str:
    return MIME_EXTENSIONS.get(mime, "bin")
