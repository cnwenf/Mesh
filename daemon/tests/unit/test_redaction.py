import base64
from urllib.parse import quote

from mesh_runtime.redaction import REDACTED, RedactionPipeline

SECRET = "sk-live-SuperSecretValue123"


def pipeline(*secrets: str) -> RedactionPipeline:
    return RedactionPipeline(secrets=list(secrets), rule_version="test-v1")


class TestExactMatcher:
    def test_replaces_exact_secret(self):
        result = pipeline(SECRET).redact(f"token is {SECRET} here")
        assert result.text == f"token is {REDACTED} here"
        assert result.hit_count == 1

    def test_multiple_occurrences_counted(self):
        result = pipeline(SECRET).redact(f"{SECRET} and {SECRET}")
        assert result.text == f"{REDACTED} and {REDACTED}"
        assert result.hit_count == 2

    def test_no_secrets_is_passthrough(self):
        result = pipeline().redact("nothing to see")
        assert result.text == "nothing to see"
        assert result.hit_count == 0

    def test_whitespace_only_secrets_ignored(self):
        result = pipeline("   ", "", "\n").redact("   spaced text \n")
        assert result.hit_count == 0

    def test_longest_secret_redacts_greedily(self):
        short = "abc"
        long = "abcdef"
        result = pipeline(short, long).redact("x abcdef y")
        assert result.text == f"x {REDACTED} y"
        assert result.hit_count == 1  # one greedy hit, not two

    def test_multiple_distinct_secrets(self):
        result = pipeline("alpha-secret", "beta-secret").redact("alpha-secret beta-secret")
        assert result.text == f"{REDACTED} {REDACTED}"
        assert result.hit_count == 2


class TestAddSecret:
    def test_added_secret_redacted_after_rotation(self):
        p = RedactionPipeline(secrets=["first-secret"], rule_version="v1")
        assert p.redact("first-secret and mesh_task_later").hit_count == 1
        p.add_secret("mesh_task_later")
        r = p.redact("first-secret and mesh_task_later")
        assert r.hit_count == 2
        assert "mesh_task_later" not in r.text

    def test_added_secret_expands_encoded_forms(self):
        p = RedactionPipeline(secrets=[], rule_version="v1")
        p.add_secret("long-rotated-token-value")
        import base64

        encoded = base64.b64encode(b"long-rotated-token-value").decode()
        assert p.redact(f"x {encoded} y").hit_count == 1

    def test_add_duplicate_or_blank_is_noop(self):
        p = RedactionPipeline(secrets=["dup"], rule_version="v1")
        n = len(p._patterns)
        p.add_secret("dup")
        p.add_secret("")
        p.add_secret("   ")
        assert len(p._patterns) == n


class TestEncodedMatchers:
    def test_base64_encoded_secret_redacted(self):
        encoded = base64.b64encode(SECRET.encode()).decode()
        result = pipeline(SECRET).redact(f"payload {encoded}")
        assert encoded not in result.text
        assert result.hit_count >= 1

    def test_url_encoded_secret_redacted(self):
        encoded = quote(SECRET + " &more", safe="")
        result = pipeline(SECRET + " &more").redact(f"query {encoded}")
        assert encoded not in result.text
        assert result.hit_count >= 1

    def test_base64_of_short_secret_not_matched(self):
        # base64 matcher applies only to secrets >= 8 chars (short encodings
        # are common substrings and would shred innocent text).
        encoded = base64.b64encode(b"ab").decode()
        result = pipeline("ab").redact(f"contains {encoded} innocently")
        assert result.hit_count == 0


class TestLines:
    def test_redact_lines_returns_total_hits(self):
        lines, hits = pipeline(SECRET).redact_lines(
            ["clean line", f"leak {SECRET}", f"double {SECRET} {SECRET}"]
        )
        assert lines == ["clean line", f"leak {REDACTED}", f"double {REDACTED} {REDACTED}"]
        assert hits == 3

    def test_rule_version_exposed(self):
        assert pipeline(SECRET).rule_version == "test-v1"
