"""Integration tests against the real ERiC library. Skipped when ERIC_HOME is not available."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

# Point ERIC_HOME at your extracted ERiC platform folder to run the integration tests.
HOME = Path(os.environ.get("ERIC_HOME", "/nonexistent-eric-home"))
TEST_CERT = HOME / "Beispiel/ericdemo-python/test-softidnr-pse.pfx"
SAMPLE_USTVA = HOME.parent / "Dokumentation/Datenarten/ElsterAnmeldung/UStVA/Beispiele/UStVA_2026.xml"

needs_eric = pytest.mark.skipif(not (HOME / "lib").is_dir(), reason="ERiC not installed")

os.environ["ELSTER_CERT_PATH"] = str(TEST_CERT)
os.environ["ELSTER_CERT_PASSWORD"] = "123456"
os.environ.pop("ELSTER_HERSTELLER_ID", None)
os.environ.pop("ELSTER_ALLOW_LIVE", None)

from elster_mcp import config as cfg
from elster_mcp import eric, server


def _valid_tax_id(prefix: str = "8605731420") -> str:
    """Build a check-digit-valid tax ID from a 10-digit prefix (ISO 7064 MOD 11,10)."""
    p = 10
    for ch in prefix:
        s = (int(ch) + p) % 10 or 10
        p = (s * 2) % 11
    check = (11 - p) % 10
    return prefix + str(check)


@needs_eric
def test_system_check_loads_eric():
    r = eric.system_check()
    assert r.ok, r.message
    assert r.data["libraries_loaded"] > 100
    assert r.data["certificate_configured"] is True
    assert r.data["hersteller_id_configured"] is False


@needs_eric
def test_tax_id_valid_and_tampered():
    good = _valid_tax_id()
    assert eric.check_tax_id(good).ok
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    r = eric.check_tax_id(bad)
    assert not r.ok and "ungültig" in r.message


@needs_eric
def test_tax_id_accepts_spaces():
    good = _valid_tax_id()
    assert eric.check_tax_id(f"{good[:2]} {good[2:5]} {good[5:8]} {good[8:]}").ok


@needs_eric
def test_iban_valid_and_invalid():
    assert eric.check_iban("DE89 3704 0044 0532 0130 00").ok
    assert not eric.check_iban("DE89370400440532013001").ok


@needs_eric
def test_test_tax_offices_listed():
    r = eric.list_test_tax_offices()
    assert r.ok and r.data["count"] > 100


@needs_eric
def test_states_and_hessen_offices():
    states = eric.list_states()
    assert states.ok and states.data["count"] >= 16
    hessen = next(s["number"] for s in states.data["states"] if s["name"].startswith("Hessen"))
    offices = eric.list_tax_offices(hessen)
    assert offices.ok and any("Frankfurt" in o["name"] for o in offices.data["offices"])


@needs_eric
def test_tax_office_info_for_a_test_office():
    # Test offices carry no address data, so look up a real Hessen office instead.
    hessen = next(s["number"] for s in eric.list_states().data["states"] if s["name"].startswith("Hessen"))
    number = eric.list_tax_offices(hessen).data["offices"][0]["number"]
    r = eric.tax_office_info(number)
    assert r.ok, r.message
    assert r.data["office"]


@needs_eric
def test_certificate_info_with_ericdemo_test_cert():
    r = eric.certificate_info()
    assert r.ok, r.message
    assert r.data["valid_until"]
    assert "123456" not in str(r.as_dict())


@needs_eric
def test_validate_requires_own_hersteller_id():
    r = server.elster_validate_return("UStVA_2026", xml_path=str(SAMPLE_USTVA))
    assert not r["ok"] and "ELSTER_HERSTELLER_ID" in r["message"]


@needs_eric
def test_sample_hersteller_id_is_refused(monkeypatch):
    monkeypatch.setenv("ELSTER_HERSTELLER_ID", "74931")
    with pytest.raises(cfg.ConfigError, match="blocked ERiC sample"):
        cfg.load()


def test_live_submit_needs_user_confirmation():
    r = server.elster_submit_return("UStVA_2026", xml_path=str(SAMPLE_USTVA), live=True)
    assert not r["ok"] and "approval" in r["message"]


@needs_eric
def test_live_submit_blocked_without_operator_flag():
    r = server.elster_submit_return("UStVA_2026", xml_path=str(SAMPLE_USTVA), live=True, user_confirmed_live=True)
    assert not r["ok"] and "ELSTER_ALLOW_LIVE" in r["message"]


def test_prepare_sets_hersteller_id_and_test_marker():
    conf = cfg.Config(HOME, Path("/tmp"), None, "12345", False)
    xml = "<Elster><TransferHeader><Verfahren>ElsterAnmeldung</Verfahren><HerstellerID>74931</HerstellerID></TransferHeader></Elster>"
    out = eric._prepare(xml, conf, test=True)
    assert "<HerstellerID>12345</HerstellerID>" in out
    assert f"<Testmerker>{cfg.TEST_MARKER}</Testmerker>" in out


def test_prepare_refuses_live_with_test_marker():
    conf = cfg.Config(HOME, Path("/tmp"), None, "12345", True)
    xml = f"<Verfahren>X</Verfahren><Testmerker>{cfg.TEST_MARKER}</Testmerker><HerstellerID>1</HerstellerID>"
    with pytest.raises(cfg.ConfigError, match="test marker"):
        eric._prepare(xml, conf, test=False)


def test_prepare_allows_live_without_test_marker():
    conf = cfg.Config(HOME, Path("/tmp"), None, "12345", True)
    xml = "<Verfahren>ElsterAnmeldung</Verfahren><HerstellerID>74931</HerstellerID>"
    out = eric._prepare(xml, conf, test=False)
    assert "<Testmerker>" not in out
    assert "<HerstellerID>12345</HerstellerID>" in out


def test_prepare_refuses_live_with_any_test_marker():
    conf = cfg.Config(HOME, Path("/tmp"), None, "12345", True)
    xml = "<Verfahren>X</Verfahren><Testmerker>700000001</Testmerker><HerstellerID>1</HerstellerID>"
    with pytest.raises(cfg.ConfigError, match="test marker"):
        eric._prepare(xml, conf, test=False)


def test_prepare_refuses_live_when_test_marker_is_not_exact():
    conf = cfg.Config(HOME, Path("/tmp"), None, "12345", True)
    xml = f"<Verfahren>X</Verfahren><Testmerker> {cfg.TEST_MARKER} </Testmerker><HerstellerID>1</HerstellerID>"
    with pytest.raises(cfg.ConfigError, match="test marker"):
        eric._prepare(xml, conf, test=False)


def test_prepare_rewrites_other_test_marker_for_test_mode():
    conf = cfg.Config(HOME, Path("/tmp"), None, "12345", False)
    xml = "<Verfahren>X</Verfahren><Testmerker>700000001</Testmerker><HerstellerID>1</HerstellerID>"
    out = eric._prepare(xml, conf, test=True)
    assert out.count(f"<Testmerker>{cfg.TEST_MARKER}</Testmerker>") == 1
    assert "700000001" not in out


def test_prepare_does_not_duplicate_existing_test_marker():
    conf = cfg.Config(HOME, Path("/tmp"), None, "12345", False)
    marker = f"<Testmerker>{cfg.TEST_MARKER}</Testmerker>"
    xml = f"<Verfahren>ElsterAnmeldung</Verfahren>{marker}<HerstellerID>74931</HerstellerID>"
    out = eric._prepare(xml, conf, test=True)
    assert out.count(marker) == 1
    assert "<HerstellerID>12345</HerstellerID>" in out


def test_prepare_refuses_test_xml_that_cannot_carry_the_marker():
    conf = cfg.Config(HOME, Path("/tmp"), None, "12345", False)
    xml = "<Elster><HerstellerID>1</HerstellerID><DatenTeil/></Elster>"
    with pytest.raises(cfg.ConfigError, match="unmarked return"):
        eric._prepare(xml, conf, test=True)


def test_prepare_refuses_duplicate_test_markers():
    conf = cfg.Config(HOME, Path("/tmp"), None, "12345", False)
    marker = f"<Testmerker>{cfg.TEST_MARKER}</Testmerker>"
    xml = f"<Verfahren>X</Verfahren>{marker}{marker}<HerstellerID>1</HerstellerID>"
    with pytest.raises(cfg.ConfigError, match="exactly once"):
        eric._prepare(xml, conf, test=True)


def test_prepare_requires_hersteller_id_element():
    conf = cfg.Config(HOME, Path("/tmp"), None, "12345", False)
    xml = "<Verfahren>ElsterAnmeldung</Verfahren>"
    with pytest.raises(cfg.ConfigError, match="no <HerstellerID>"):
        eric._prepare(xml, conf, test=True)


def test_prepare_rejects_unsafe_hersteller_id():
    conf = cfg.Config(HOME, Path("/tmp"), None, "12</HerstellerID><Injected>", False)
    xml = "<Verfahren>ElsterAnmeldung</Verfahren><HerstellerID>1</HerstellerID>"
    with pytest.raises(cfg.ConfigError, match="only digits"):
        eric._prepare(xml, conf, test=True)


def test_hersteller_id_must_be_digits_and_not_the_sample():
    assert cfg.require_hersteller_id("12345") == "12345"
    with pytest.raises(cfg.ConfigError, match="only digits"):
        cfg.require_hersteller_id("12ab")
    with pytest.raises(cfg.ConfigError, match="only digits"):
        cfg.require_hersteller_id("12²")
    with pytest.raises(cfg.ConfigError, match="blocked ERiC sample"):
        cfg.require_hersteller_id("74931")
    with pytest.raises(cfg.ConfigError, match="not set"):
        cfg.require_hersteller_id(None)


def test_all_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "elster_system_check",
        "elster_check_tax_id",
        "elster_check_iban",
        "elster_check_tax_number",
        "elster_convert_tax_number",
        "elster_list_test_tax_offices",
        "elster_list_states",
        "elster_list_tax_offices",
        "elster_tax_office_info",
        "elster_certificate_info",
        "elster_validate_return",
        "elster_submit_return",
    }


@needs_eric
@pytest.mark.parametrize("elster_number", ["2653081508158", "9198081508152"])
def test_tax_number_round_trip_with_official_samples(elster_number):
    """Official ERiC sample numbers: check, format to notice style, convert back, must match."""
    assert eric.check_tax_number(elster_number).ok
    e, _ = eric._runtime()
    with eric._buffer(e) as h:
        assert e.PyEricFormatStNr(elster_number, h) == 0
        notice = eric._text(e, h)
    back = server.elster_convert_tax_number(notice, elster_number[:4])
    assert back["ok"], back["message"]
    assert back["elster_tax_number"] == elster_number


def test_convert_tax_number_rejects_state_letters():
    r = server.elster_convert_tax_number("053 815 08158", "HE")
    assert not r["ok"] and "2-digit state number" in r["message"]
