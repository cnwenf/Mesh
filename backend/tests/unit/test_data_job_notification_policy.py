"""data_job_finished §6.13 matrix policy tests (import-export.md §3.10 / README §6.13)."""

import pytest

from mesh.comment_inbox.notifications import ALLOWED_PREFERENCE_EVENT_TYPES, policy_for
from mesh.db.models.notification import NOTIFICATION_TYPE_VALUES

pytestmark = pytest.mark.unit


class TestDataJobPolicyMatrix:
    def test_type_registered(self):
        assert "data_job_finished" in NOTIFICATION_TYPE_VALUES
        # ALLOWED_PREFERENCE_EVENT_TYPES derives from the tuple — explicit
        # subscription (success row) must be expressible.
        assert "data_job_finished" in ALLOWED_PREFERENCE_EVENT_TYPES

    def test_completed_success_normal_default_off(self):
        policy = policy_for("data_job_finished", data_job_status="completed")
        assert policy.priority == "normal"
        assert policy.default_inbox is False  # stays on the data-jobs page
        assert policy.pierce_quiet_hours is False
        assert policy.reset_unread is False
        assert policy.email_default == "none"

    def test_completed_with_errors_normal_inbox_digest(self):
        policy = policy_for("data_job_finished", data_job_status="completed_with_errors")
        assert policy.priority == "normal"
        assert policy.default_inbox is True  # failed rows need attention
        assert policy.pierce_quiet_hours is False
        assert policy.reset_unread is False
        assert policy.email_default == "digest"

    def test_failed_critical_pierce_reset_realtime(self):
        policy = policy_for("data_job_finished", data_job_status="failed")
        assert policy.priority == "critical"
        assert policy.default_inbox is True
        assert policy.pierce_quiet_hours is True
        assert policy.reset_unread is True
        assert policy.email_default == "realtime"

    def test_unset_status_treated_as_success_row(self):
        policy = policy_for("data_job_finished")
        assert policy.priority == "normal"
        assert policy.default_inbox is False

    def test_unknown_type_still_producer_bug(self):
        with pytest.raises(ValueError):
            policy_for("data_job_finished_and_a_half")

    def test_execution_finished_unaffected(self):
        failed = policy_for("execution_finished", execution_status="failed")
        assert failed.priority == "critical"
        ok = policy_for("execution_finished", execution_status="completed")
        assert ok.default_inbox is False
