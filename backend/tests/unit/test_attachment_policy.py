"""Upload policy unit tests (attachment.md §3.6)."""

from __future__ import annotations

import pytest

from mesh.attachment.policy import (
    DEFAULT_ALLOWED_MIMES,
    TEXT_SCAN_SKIP_EXTENSIONS,
    TEXT_SCAN_SKIP_MIMES,
    UploadLimits,
    file_extension,
    validate_upload_request,
)
from mesh.errors import (
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationError,
)

pytestmark = pytest.mark.unit

MEGABYTE = 1024 * 1024


def _limits(**overrides) -> UploadLimits:
    base = {
        "max_file_bytes": 100 * MEGABYTE,
        "max_image_bytes": 25 * MEGABYTE,
        "total_bytes": 1024 * MEGABYTE,
        "allowed_mimes": DEFAULT_ALLOWED_MIMES,
    }
    return UploadLimits(**(base | overrides))


def _validate(file_name="shot.png", file_size=1024, mime_type="image/png", limits=None):
    validate_upload_request(
        file_name=file_name,
        file_size=file_size,
        mime_type=mime_type,
        limits=limits or _limits(),
    )


def test_accepts_allowed_image():
    _validate()


def test_accepts_document_types():
    _validate(file_name="report.pdf", mime_type="application/pdf")
    _validate(file_name="notes.md", mime_type="text/markdown")
    _validate(file_name="data.csv", mime_type="text/csv")
    _validate(file_name="log.txt", mime_type="text/plain")
    _validate(file_name="archive.zip", mime_type="application/zip")


@pytest.mark.parametrize(
    "name",
    ["", "   ", "a" * 256, "path/sep.png", "back\\slash.png", "nul\x00byte.png"],
)
def test_rejects_bad_file_names(name):
    with pytest.raises(ValidationError):
        _validate(file_name=name)


def test_rejects_non_positive_size():
    with pytest.raises(ValidationError):
        _validate(file_size=0)


def test_rejects_disallowed_mime():
    with pytest.raises(UnsupportedMediaTypeError):
        _validate(file_name="run.exe", mime_type="application/x-msdownload")


def test_rejects_mime_extension_mismatch():
    # Forged extension: declared MIME and extension disagree (§3.2 防伪造).
    with pytest.raises(ValidationError):
        _validate(file_name="shot.exe", mime_type="image/png")


def test_file_too_large():
    with pytest.raises(PayloadTooLargeError) as excinfo:
        _validate(file_size=101 * MEGABYTE)
    assert excinfo.value.code == "file_too_large"


def test_image_size_cap_is_lower():
    # Images cap at 25 MB even though the general cap is 100 MB.
    with pytest.raises(PayloadTooLargeError):
        _validate(file_size=26 * MEGABYTE)
    # A 26 MB non-image passes.
    _validate(file_name="report.pdf", mime_type="application/pdf", file_size=26 * MEGABYTE)


def test_mime_with_parameters_is_normalized():
    _validate(mime_type="image/png; charset=binary")


def test_quota_allowlist_override_restricts():
    limits = _limits(allowed_mimes=frozenset({"image/png"}))
    with pytest.raises(UnsupportedMediaTypeError):
        validate_upload_request(
            file_name="notes.md", file_size=10, mime_type="text/markdown", limits=limits
        )


def test_render_exposes_max_file_bytes():
    assert _limits().render() == {"max_file_bytes": 100 * MEGABYTE}


def test_file_extension_helper():
    assert file_extension("a.TAR.gz") == "gz"
    assert file_extension("noext") == ""


def test_text_scan_skip_whitelist_is_conservative():
    assert TEXT_SCAN_SKIP_MIMES == frozenset({"text/plain", "text/markdown", "text/csv"})
    assert "exe" not in TEXT_SCAN_SKIP_EXTENSIONS
    assert {"txt", "log", "csv", "md"} <= TEXT_SCAN_SKIP_EXTENSIONS
