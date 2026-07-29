import pytest

from mesh_runtime.result import (
    RESULT_SCHEMA_VERSION,
    TERMINATIONS,
    Usage,
    build_result,
    validate_result,
)


def usage(**overrides) -> Usage:
    base = dict(
        input_tokens=100,
        cache_creation_tokens=10,
        cache_read_tokens=20,
        output_tokens=30,
        turns=2,
        cost_usd="0.004200",
    )
    base.update(overrides)
    return Usage(**base)


def make_result(**overrides):
    kwargs = dict(
        provider="claude-code",
        version="2.0.0",
        model="claude-sonnet-4",
        session_id="sess-123",
        usage=usage(),
        exit_code=0,
        summary="done",
        termination="completed",
        checkout_id="co-1",
        diff_ref=None,
        rule_version="test-v1",
        hit_count=0,
    )
    kwargs.update(overrides)
    return build_result(**kwargs)


class TestBuildResult:
    def test_shape_matches_spec_3_9(self):
        doc = make_result()
        assert doc["schema_version"] == RESULT_SCHEMA_VERSION
        assert doc["provider"] == {
            "name": "claude-code",
            "version": "2.0.0",
            "model": "claude-sonnet-4",
            "session_id": "sess-123",
        }
        assert doc["usage"]["total_tokens"] == 160  # sum of four token fields
        assert doc["usage"]["cost_usd"] == "0.004200"  # decimal string preserved
        assert doc["outcome"] == {"exit_code": 0, "summary": "done", "termination": "completed"}
        assert doc["artifacts"] == {"checkout_id": "co-1", "diff_ref": None}
        assert doc["redaction"] == {"rule_version": "test-v1", "hit_count": 0}

    def test_session_id_optional(self):
        doc = make_result(session_id=None)
        assert doc["provider"]["session_id"] is None

    def test_rejects_negative_tokens(self):
        with pytest.raises(ValueError, match="non-negative"):
            make_result(usage=usage(input_tokens=-1))

    def test_rejects_non_decimal_cost(self):
        with pytest.raises(ValueError, match="decimal"):
            make_result(usage=usage(cost_usd="12,5"))
        with pytest.raises(ValueError, match="decimal"):
            make_result(usage=usage(cost_usd=0.5))  # type: ignore[arg-type]

    def test_rejects_unknown_termination(self):
        with pytest.raises(ValueError, match="termination"):
            make_result(termination="exploded")

    def test_terminations_vocabulary(self):
        assert "completed" in TERMINATIONS
        assert "awaiting_approval" not in TERMINATIONS  # attempt-level, not result


class TestValidateResult:
    def test_roundtrip_valid(self):
        validate_result(make_result())  # no raise

    def test_missing_schema_version(self):
        doc = make_result()
        del doc["schema_version"]
        with pytest.raises(ValueError, match="schema_version"):
            validate_result(doc)

    def test_unknown_schema_version(self):
        doc = make_result()
        doc["schema_version"] = 99
        with pytest.raises(ValueError, match="schema_version"):
            validate_result(doc)

    def test_missing_section(self):
        doc = make_result()
        del doc["usage"]
        with pytest.raises(ValueError, match="usage"):
            validate_result(doc)

    def test_bad_total_tokens(self):
        doc = make_result()
        doc["usage"]["total_tokens"] = -5
        with pytest.raises(ValueError, match="total_tokens"):
            validate_result(doc)

    def test_non_dict_rejected(self):
        with pytest.raises(ValueError, match="object"):
            validate_result([])  # type: ignore[arg-type]

    def test_rejects_unknown_termination(self):
        doc = make_result()
        doc["outcome"]["termination"] = "exploded"
        with pytest.raises(ValueError, match="termination"):
            validate_result(doc)

    def test_rejects_bool_exit_code(self):
        doc = make_result()
        doc["outcome"]["exit_code"] = False  # isinstance(False, int) is True
        with pytest.raises(ValueError, match="exit_code"):
            validate_result(doc)

    def test_rejects_non_integer_exit_code(self):
        doc = make_result()
        doc["outcome"]["exit_code"] = "0"
        with pytest.raises(ValueError, match="exit_code"):
            validate_result(doc)

    def test_rejects_total_tokens_inconsistent_with_components(self):
        doc = make_result()
        doc["usage"]["total_tokens"] = doc["usage"]["total_tokens"] + 1
        with pytest.raises(ValueError, match="total_tokens"):
            validate_result(doc)

    def test_rejects_bool_usage_field(self):
        doc = make_result()
        doc["usage"]["input_tokens"] = True
        with pytest.raises(ValueError, match="input_tokens"):
            validate_result(doc)


class TestUsageBooleanRejection:
    def test_usage_rejects_bool_token_counts(self):
        # bool is a subclass of int; counts must be genuine non-negative ints.
        with pytest.raises(ValueError, match="input_tokens"):
            usage(input_tokens=True)._validate()

    def test_build_result_rejects_bool_turns(self):
        with pytest.raises(ValueError, match="turns"):
            make_result(usage=usage(turns=False))
