"""Runtime configuration, read from environment variables only."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

TEST_MARKER = "700000004"
_SAMPLE_HERSTELLER_ID = "74931"
# ASCII digits only. str.isdigit() accepts other Unicode digits, which must not be written into XML.
_HERSTELLER_ID = re.compile(r"[0-9]+")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    eric_home: Path
    log_dir: Path
    cert_path: Path | None
    hersteller_id: str | None
    allow_live: bool

    @property
    def lib_dir(self) -> Path:
        return self.eric_home / "lib"


def require_hersteller_id(value: str | None) -> str:
    """Return a Hersteller-ID that is safe to write into a return.

    The ID is stamped into XML, so only ASCII digits are accepted, and the ERiC sample ID is refused.
    """
    if not value:
        raise ConfigError("ELSTER_HERSTELLER_ID is not set. ELSTER rejects returns without your own Hersteller-ID, even for tests.")
    if value == _SAMPLE_HERSTELLER_ID:
        raise ConfigError("ELSTER_HERSTELLER_ID 74931 is the blocked ERiC sample ID. Use your own Hersteller-ID.")
    if not _HERSTELLER_ID.fullmatch(value):
        raise ConfigError("ELSTER_HERSTELLER_ID must contain only digits.")
    return value


def load() -> Config:
    home = os.environ.get("ERIC_HOME")
    if not home:
        raise ConfigError("ERIC_HOME is not set. Point it at the unpacked ERiC platform folder, e.g. .../ERiC-45.1.5.0/Darwin-universal")
    eric_home = Path(home).expanduser().resolve()
    if not (eric_home / "lib").is_dir():
        raise ConfigError(f"ERIC_HOME has no lib folder: {eric_home}")
    log_dir = Path(os.environ.get("ELSTER_LOG_DIR", Path.home() / ".elster-mcp" / "logs")).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    cert = os.environ.get("ELSTER_CERT_PATH")
    hid = (os.environ.get("ELSTER_HERSTELLER_ID") or "").strip() or None
    if hid is not None:
        require_hersteller_id(hid)
    return Config(
        eric_home=eric_home,
        log_dir=log_dir,
        cert_path=Path(cert).expanduser() if cert else None,
        hersteller_id=hid,
        allow_live=os.environ.get("ELSTER_ALLOW_LIVE") == "1",
    )


def cert_password() -> str:
    """Certificate password from ELSTER_CERT_PASSWORD or the stdout of ELSTER_CERT_PASSWORD_CMD."""
    direct = os.environ.get("ELSTER_CERT_PASSWORD")
    if direct:
        return direct
    cmd = os.environ.get("ELSTER_CERT_PASSWORD_CMD")
    if cmd:
        out = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=30, check=False)
        if out.returncode == 0 and out.stdout:
            return out.stdout.rstrip("\n")
        raise ConfigError("ELSTER_CERT_PASSWORD_CMD failed or printed nothing.")
    raise ConfigError("Set ELSTER_CERT_PASSWORD or ELSTER_CERT_PASSWORD_CMD to unlock the certificate.")
