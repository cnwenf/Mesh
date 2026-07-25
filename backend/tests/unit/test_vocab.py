"""Event vocabulary registry — kept drift-free against README §6.7."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from mesh.events.vocab import (
    EVENT_VOCABULARY,
    OUTBOX_INTERNAL_EVENT_TYPES,
    UnregisteredEventError,
    is_realtime_event,
    require_realtime_event,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKER_PATH = REPO_ROOT / "tests" / "docs" / "check_event_vocab.py"
SPECS_DIR = REPO_ROOT / "docs" / "specs"


def _load_checker_module():
    spec = importlib.util.spec_from_file_location("check_event_vocab", CHECKER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_event_vocab"] = module
    spec.loader.exec_module(module)
    return module


def test_vocabulary_baseline_is_96_events():
    assert len(EVENT_VOCABULARY) == 96


def test_vocabulary_matches_readme_registry_exactly():
    checker = _load_checker_module()
    registered = checker.parse_registry(SPECS_DIR / "README.md")
    missing_in_code = registered - EVENT_VOCABULARY
    extra_in_code = EVENT_VOCABULARY - registered
    assert not missing_in_code, f"events registered in README but missing in code: {missing_in_code}"
    assert not extra_in_code, f"events in code but not registered in README: {extra_in_code}"


def test_outbox_internal_types_match_docs_whitelist():
    checker = _load_checker_module()
    assert OUTBOX_INTERNAL_EVENT_TYPES == checker.OUTBOX_EVENT_TYPES


def test_is_realtime_event():
    assert is_realtime_event("issue.updated")
    assert is_realtime_event("error")  # §6.8 in-stream frame
    assert is_realtime_event("ping")
    assert not is_realtime_event("agent.run_started")  # historical drift, forbidden
    assert not is_realtime_event("issue.assigned")  # outbox domain event, not realtime


def test_require_registered_rejects_unregistered():
    assert require_realtime_event("execution.completed") == "execution.completed"
    with pytest.raises(UnregisteredEventError) as excinfo:
        require_realtime_event("agent.run_started")
    assert excinfo.value.event == "agent.run_started"
