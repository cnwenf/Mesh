"""Upload policy: size limits, MIME/extension allowlists (§3.6 — defaults).

All checks run at ``upload-request`` time BEFORE any bytes move (§3.6: 配额
前置校验,避免传完才发现超限浪费带宽). Per-workspace overrides live in
``attachment_quotas``; the defaults here mirror the spec.
"""

from __future__ import annotations

from dataclasses import dataclass

from mesh.attachment.mime import IMAGE_MIMES
from mesh.errors import (
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationError,
)

# MIME → acceptable extensions (防伪造: MIME 与扩展名必须匹配, §3.2).
ALLOWED_MIME_EXTENSIONS: dict[str, frozenset[str]] = {
    "image/png": frozenset({"png"}),
    "image/jpeg": frozenset({"jpg", "jpeg"}),
    "image/gif": frozenset({"gif"}),
    "image/webp": frozenset({"webp"}),
    "image/svg+xml": frozenset({"svg"}),
    "application/pdf": frozenset({"pdf"}),
    "text/plain": frozenset({"txt", "log", "text"}),
    "text/markdown": frozenset({"md", "markdown"}),
    "text/csv": frozenset({"csv"}),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": frozenset({"xlsx"}),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": frozenset(
        {"docx"}
    ),
    "application/zip": frozenset({"zip"}),
    "application/gzip": frozenset({"gz", "tgz"}),
}

DEFAULT_ALLOWED_MIMES: frozenset[str] = frozenset(ALLOWED_MIME_EXTENSIONS)

# Plain-text types eligible for scan-skip (blob scan_status='skipped' — the
# ONLY source of that state, §3.6): macro-free, non-executable text. The
# worker still magic-byte sniffs and SHA-256 verifies these.
TEXT_SCAN_SKIP_MIMES: frozenset[str] = frozenset({"text/plain", "text/markdown", "text/csv"})
TEXT_SCAN_SKIP_EXTENSIONS: frozenset[str] = frozenset({"txt", "log", "csv", "md", "markdown"})

MAX_FILE_NAME_LENGTH = 255


@dataclass(frozen=True)
class UploadLimits:
    """Effective limits for one workspace (quota row overrides defaults)."""

    max_file_bytes: int
    max_image_bytes: int
    total_bytes: int
    allowed_mimes: frozenset[str]

    def render(self) -> dict[str, int]:
        return {"max_file_bytes": self.max_file_bytes}


def file_extension(file_name: str) -> str:
    _, dot, ext = file_name.rpartition(".")
    if not dot:
        return ""
    return ext.lower()


def validate_upload_request(
    *,
    file_name: str,
    file_size: int,
    mime_type: str,
    limits: UploadLimits,
) -> None:
    """Pre-signature validation (§3.2 服务端签发前校验, §3.5 error codes).

    :raises ValidationError: bad name, non-positive size, MIME/extension
        mismatch (400 ``validation_error``).
    :raises PayloadTooLargeError: 413 ``file_too_large``.
    :raises UnsupportedMediaTypeError: 415 ``unsupported_media_type``.
    """
    if not file_name or not file_name.strip():
        raise ValidationError("file_name must not be empty")
    if len(file_name) > MAX_FILE_NAME_LENGTH:
        raise ValidationError(
            "file_name too long",
            details={"max_length": MAX_FILE_NAME_LENGTH},
        )
    if "/" in file_name or "\\" in file_name or "\x00" in file_name:
        raise ValidationError("file_name must not contain path separators")
    if file_size <= 0:
        raise ValidationError("file_size must be positive")

    declared_mime = (mime_type or "").split(";")[0].strip().lower()
    extension = file_extension(file_name)
    allowed_extensions = ALLOWED_MIME_EXTENSIONS.get(declared_mime)
    if declared_mime not in limits.allowed_mimes or allowed_extensions is None:
        raise UnsupportedMediaTypeError(
            "mime type not allowed", details={"mime_type": declared_mime[:128]}
        )
    if extension not in allowed_extensions:
        # Forged extension: declared MIME and extension disagree (§3.2 防伪造).
        raise ValidationError(
            "mime type does not match file extension",
            details={
                "mime_type": declared_mime[:128],
                "extension": extension[:16],
                "expected_extensions": sorted(allowed_extensions),
            },
        )

    cap = limits.max_file_bytes
    if declared_mime in IMAGE_MIMES:
        cap = min(cap, limits.max_image_bytes)
    if file_size > cap:
        raise PayloadTooLargeError(
            "file exceeds the size limit",
            code="file_too_large",
            details={"max_file_bytes": cap, "file_size": file_size},
        )
