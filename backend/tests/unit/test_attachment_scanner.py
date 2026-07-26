"""AV scanner hook unit tests (attachment.md §3.3/A11)."""

from __future__ import annotations

import pytest

from mesh.attachment.scanner import EICAR_SIGNATURE, ENGINE_NAME, HeuristicScanner

pytestmark = pytest.mark.unit


def test_eicar_test_signature_is_flagged():
    verdict = HeuristicScanner().scan(b"prefix" + EICAR_SIGNATURE + b"suffix", sniffed_mime="text/plain")
    assert verdict.infected is True
    assert verdict.result == "eicar-test-signature"
    assert verdict.engine == ENGINE_NAME


def test_executable_smuggled_under_image_mime_is_flagged():
    # PE header under a declared image MIME — forged content (§4.6 MIME 伪造).
    verdict = HeuristicScanner().scan(b"MZ" + b"\x00" * 128, sniffed_mime="image/png")
    assert verdict.infected is True
    assert verdict.result == "executable-container"


def test_declared_executable_mime_is_not_double_flagged():
    verdict = HeuristicScanner().scan(b"MZ" + b"\x00" * 128, sniffed_mime="application/x-msdownload")
    assert verdict.infected is False


def test_clean_bytes_pass():
    from tests.unit.attachment_support import make_png

    verdict = HeuristicScanner().scan(make_png(), sniffed_mime="image/png")
    assert verdict.infected is False
    assert verdict.result == "clean"
    assert verdict.engine == ENGINE_NAME


def test_plain_text_is_clean():
    verdict = HeuristicScanner().scan(b"a,b,c\n1,2,3\n", sniffed_mime="text/csv")
    assert verdict.infected is False
