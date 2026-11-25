"""Configuration checks that do not need the ERiC library, so CI runs them."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from elster_mcp import config as cfg


@pytest.fixture
def eric_home(tmp_path: Path) -> Path:
    home = tmp_path / "eric"
    (home / "lib").mkdir(parents=True)
    return home


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, eric_home: Path, tmp_path: Path):
    monkeypatch.setenv("ERIC_HOME", str(eric_home))
    monkeypatch.setenv("ELSTER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("ELSTER_CERT_PATH", raising=False)
    monkeypatch.delenv("ELSTER_HERSTELLER_ID", raising=False)
    monkeypatch.delenv("ELSTER_ALLOW_LIVE", raising=False)
    monkeypatch.delenv("ELSTER_CERT_PASSWORD", raising=False)
    monkeypatch.delenv("ELSTER_CERT_PASSWORD_CMD", raising=False)


def test_load_requires_eric_home(monkeypatch, configured):
    monkeypatch.delenv("ERIC_HOME")
    with pytest.raises(cfg.ConfigError, match="ERIC_HOME is not set"):
        cfg.load()


def test_load_requires_lib_folder(monkeypatch, tmp_path, configured):
    monkeypatch.setenv("ERIC_HOME", str(tmp_path))
    with pytest.raises(cfg.ConfigError, match="no lib folder"):
        cfg.load()


def test_load_reads_paths_and_leaves_live_filing_off(monkeypatch, eric_home, tmp_path, configured):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ERIC_HOME", "~/eric")
    monkeypatch.setenv("ELSTER_LOG_DIR", "~/logs")
    monkeypatch.setenv("ELSTER_CERT_PATH", "~/id.pfx")
    monkeypatch.setenv("ELSTER_HERSTELLER_ID", "  12345  ")
    conf = cfg.load()
    assert conf.eric_home == eric_home.resolve()
    assert conf.lib_dir == eric_home.resolve() / "lib"
    assert conf.log_dir == tmp_path / "logs"
    assert conf.log_dir.is_dir()
    assert conf.cert_path == tmp_path / "id.pfx"
    assert conf.hersteller_id == "12345"
    assert conf.allow_live is False


def test_load_refuses_the_sample_hersteller_id_after_stripping(monkeypatch, configured):
    monkeypatch.setenv("ELSTER_HERSTELLER_ID", "  74931  ")
    with pytest.raises(cfg.ConfigError, match="blocked ERiC sample"):
        cfg.load()


def test_blank_hersteller_id_is_unset(monkeypatch, configured):
    monkeypatch.setenv("ELSTER_HERSTELLER_ID", "   ")
    assert cfg.load().hersteller_id is None


def test_live_filing_turns_on_only_for_exact_one(monkeypatch, configured):
    monkeypatch.setenv("ELSTER_ALLOW_LIVE", "1")
    assert cfg.load().allow_live is True


@pytest.mark.parametrize("value", ["", "0", "true", "TRUE", "yes", "1 ", "01", "2"])
def test_live_filing_stays_off_for_any_other_flag(monkeypatch, configured, value):
    monkeypatch.setenv("ELSTER_ALLOW_LIVE", value)
    assert cfg.load().allow_live is False


def _python(code: str) -> str:
    return shlex.join([sys.executable, "-c", code])


def test_direct_password_is_returned_unchanged(monkeypatch, configured):
    monkeypatch.setenv("ELSTER_CERT_PASSWORD", "pw\n")
    monkeypatch.setenv("ELSTER_CERT_PASSWORD_CMD", "definitely-not-a-command")
    assert cfg.cert_password() == "pw\n"


def test_empty_direct_password_uses_the_command(monkeypatch, configured):
    monkeypatch.setenv("ELSTER_CERT_PASSWORD", "")
    monkeypatch.setenv("ELSTER_CERT_PASSWORD_CMD", _python("import sys; sys.stdout.write('from-cmd\\n')"))
    assert cfg.cert_password() == "from-cmd"


def test_command_strips_a_trailing_newline(monkeypatch, configured):
    monkeypatch.setenv("ELSTER_CERT_PASSWORD_CMD", _python("import sys; sys.stdout.write('sekret\\r\\n')"))
    assert cfg.cert_password() == "sekret"


def test_command_keeps_spaces_inside_the_password(monkeypatch, configured):
    monkeypatch.setenv("ELSTER_CERT_PASSWORD_CMD", _python("import sys; sys.stdout.write('a b  \\n')"))
    assert cfg.cert_password() == "a b  "


@pytest.mark.parametrize("code", ["import sys; sys.stdout.write('\\n')", "import sys"])
def test_command_that_prints_nothing_is_refused(monkeypatch, configured, code):
    monkeypatch.setenv("ELSTER_CERT_PASSWORD_CMD", _python(code))
    with pytest.raises(cfg.ConfigError, match="failed or printed nothing"):
        cfg.cert_password()


def test_failed_command_hides_stdout(monkeypatch, configured):
    code = "import sys; sys.stdout.write('s3cret-out\\n'); sys.stderr.write('s3cret-err\\n'); sys.exit(2)"
    monkeypatch.setenv("ELSTER_CERT_PASSWORD_CMD", _python(code))
    with pytest.raises(cfg.ConfigError, match="failed or printed nothing") as raised:
        cfg.cert_password()
    message = str(raised.value)
    assert "s3cret-out" not in message
    assert "s3cret-err" not in message


def test_missing_command_is_a_config_error(monkeypatch, configured):
    monkeypatch.setenv("ELSTER_CERT_PASSWORD_CMD", "/nonexistent/elster-secret-cmd")
    with pytest.raises(cfg.ConfigError, match="failed or printed nothing") as raised:
        cfg.cert_password()
    assert "elster-secret-cmd" not in str(raised.value)


@pytest.mark.parametrize("cmd", ["echo 'unterminated", "   "])
def test_unusable_command_string_is_a_config_error(monkeypatch, configured, cmd):
    monkeypatch.setenv("ELSTER_CERT_PASSWORD_CMD", cmd)
    with pytest.raises(cfg.ConfigError, match="not a valid command"):
        cfg.cert_password()


def test_command_timeout_hides_the_command(monkeypatch, configured):
    monkeypatch.setenv("ELSTER_CERT_PASSWORD_CMD", "keeper get super-secret-item")

    def boom(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 30))

    monkeypatch.setattr(cfg.subprocess, "run", boom)
    with pytest.raises(cfg.ConfigError, match="timed out") as raised:
        cfg.cert_password()
    assert "super-secret-item" not in str(raised.value)


def test_undecodable_command_output_is_a_config_error(monkeypatch, configured):
    monkeypatch.setenv("ELSTER_CERT_PASSWORD_CMD", "secret-helper")

    def boom(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(cfg.subprocess, "run", boom)
    with pytest.raises(cfg.ConfigError, match="usable password") as raised:
        cfg.cert_password()
    assert "secret-helper" not in str(raised.value)


def test_password_is_required(configured):
    with pytest.raises(cfg.ConfigError, match="ELSTER_CERT_PASSWORD or ELSTER_CERT_PASSWORD_CMD"):
        cfg.cert_password()
