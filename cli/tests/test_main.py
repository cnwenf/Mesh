"""Entry-point discipline: exit codes, aliases, usage errors (cli.md §3.4/C27)."""

from __future__ import annotations

from click.testing import CliRunner

from meshcli import main as main_mod


def _run(args, tmp_path, monkeypatch):
    """Invoke through the real entry point (exit-code discipline included)."""
    monkeypatch.setenv("MESH_CONFIG", str(tmp_path / "mesh"))
    return main_mod.main(args)


def test_version_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MESH_CONFIG", str(tmp_path / "mesh"))
    rc = main_mod.main(["version"])
    assert rc == 0


def test_unknown_command_exits_3_not_2(tmp_path, monkeypatch):
    """Usage errors are exit 3 — 2 is auth-exclusive (review M1)."""
    rc = _run(["definitely-not-a-command"], tmp_path, monkeypatch)
    assert rc == 3


def test_unknown_flag_exits_3(tmp_path, monkeypatch):
    rc = _run(["version", "--no-such-flag"], tmp_path, monkeypatch)
    assert rc == 3


def test_alias_expansion_single_level(tmp_path, monkeypatch):
    """config aliases expand once, positional args pass through (C27)."""
    config_dir = tmp_path / "mesh"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        "version: 1\naliases:\n  ver: version\n  rec: a b\n  a: ver\n"
    )
    monkeypatch.setenv("MESH_CONFIG", str(config_dir))
    from meshcli.config import expand_alias, load_aliases

    aliases = load_aliases()
    # ver → version
    assert expand_alias(["ver"], aliases) == ["version"]
    # positional passthrough
    assert expand_alias(["ver", "--output", "json"], aliases) == [
        "version",
        "--output",
        "json",
    ]
    # single level: rec → "a b" — the resulting "a" is NOT re-expanded
    assert expand_alias(["rec", "x"], aliases) == ["a", "b", "x"]


def test_help_exits_zero(tmp_path, monkeypatch):
    runner = CliRunner()
    main_mod._ensure_commands()
    result = runner.invoke(main_mod.cli, ["--help"])
    assert result.exit_code == 0
    assert "issue" in result.output


def test_group_help_three_layers(tmp_path, monkeypatch):
    runner = CliRunner()
    main_mod._ensure_commands()
    assert runner.invoke(main_mod.cli, ["issue", "--help"]).exit_code == 0
    result = runner.invoke(main_mod.cli, ["issue", "create", "--help"])
    assert result.exit_code == 0
    assert "--description-file" in result.output  # command-level flag docs


def test_config_set_rejects_insecure_via_cli(tmp_path, monkeypatch):
    rc = _run(["config", "set", "insecure", "true"], tmp_path, monkeypatch)
    assert rc == 3


def test_config_set_rejects_userinfo_proxy_via_cli(tmp_path, monkeypatch):
    rc = _run(
        ["config", "set", "proxy", "http://user:pass@proxy:3128"], tmp_path, monkeypatch
    )
    assert rc == 3
