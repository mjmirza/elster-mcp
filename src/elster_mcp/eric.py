"""Thin, thread-safe layer over the official ERiC Python wrapper."""

from __future__ import annotations

import atexit
import importlib.util
import re
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config as cfg

# ERiC processing flags, from the ERiC API reference (bearbeitungsflags).
ERIC_VALIDIERE = 1 << 1
ERIC_SENDE = 1 << 2
ERIC_DRUCKE = 1 << 5

_LOCK = threading.Lock()
_ERIC: Any = None
_API: Any = None
_CONF: cfg.Config | None = None


@dataclass
class Result:
    ok: bool
    code: int
    message: str
    data: dict | None = None

    def as_dict(self) -> dict:
        out = {"ok": self.ok, "code": self.code, "message": self.message}
        if self.data:
            out.update(self.data)
        return out


def _load_wrapper(conf: cfg.Config) -> Any:
    """Load the ERiC Python wrapper from the user's own ERiC download. It is not bundled with this project."""
    pkg = conf.eric_home / "Beispiel" / "ericdemo-python" / "ericdemo" / "ericapi" / "__init__.py"
    if not pkg.is_file():
        raise cfg.ConfigError(f"ERiC Python wrapper not found at {pkg.parent}. Use the full ERiC download for your platform.")
    spec = importlib.util.spec_from_file_location("ericapi", pkg, submodule_search_locations=[str(pkg.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["ericapi"] = module
    spec.loader.exec_module(module)
    return module


def _runtime() -> tuple[Any, cfg.Config]:
    global _ERIC, _CONF, _API
    if _ERIC is None:
        _CONF = cfg.load()
        _API = _load_wrapper(_CONF)
        _ERIC = _API.PyEric(str(_CONF.lib_dir), str(_CONF.log_dir))
        atexit.register(_shutdown)
    return _ERIC, _CONF  # type: ignore[return-value]


def _shutdown() -> None:
    """End ERiC while the wrapper module is still alive; its own __del__ fails during interpreter teardown."""
    global _ERIC
    with _LOCK:
        if _ERIC is not None:
            _ERIC.__del__()
            _ERIC._lib_loaded = False
            _ERIC = None


@contextmanager
def _buffer(eric: Any):
    handle = eric.PyEricRueckgabepufferErzeugen()
    try:
        yield handle
    finally:
        eric.PyEricRueckgabepufferFreigeben(handle)


def _text(eric: Any, handle) -> str:
    return eric.PyEricRueckgabepufferInhalt(handle).decode("utf-8", "replace")


def _error_text(eric: Any, code: int) -> str:
    with _buffer(eric) as h:
        eric.PyEricHoleFehlerText(code, h)
        return _text(eric, h)


def _simple(fn, *args) -> Result:
    with _LOCK:
        eric, _ = _runtime()
        code = fn(eric)(*args)
        return Result(code == 0, code, _error_text(eric, code))


def system_check() -> Result:
    with _LOCK:
        eric, conf = _runtime()
        code = eric.PyEricSystemCheck()
        with _buffer(eric) as h:
            eric.PyEricVersion(h)
            libs = re.findall(r"<Name>([^<]+)</Name>", _text(eric, h))
        return Result(
            code == 0,
            code,
            _error_text(eric, code),
            {
                "eric_home": str(conf.eric_home),
                "libraries_loaded": len(libs),
                "certificate_configured": bool(conf.cert_path),
                "hersteller_id_configured": bool(conf.hersteller_id),
                "live_submission_enabled": conf.allow_live,
            },
        )


def check_tax_id(idnr: str) -> Result:
    return _simple(lambda e: e.PyEricPruefeIdentifikationsMerkmal, _digits(idnr))


def check_iban(iban: str) -> Result:
    return _simple(lambda e: e.PyEricPruefeIBAN, re.sub(r"\s", "", iban).upper())


def check_tax_number(elster_stnr: str) -> Result:
    return _simple(lambda e: e.PyEricPruefeSteuernummer, _digits(elster_stnr))


def make_elster_tax_number(tax_number: str, state_number: str) -> Result:
    """Convert a tax number as printed on a notice (Bescheid) into the 13-digit ELSTER format.
    state_number is the 2-digit state number (e.g. 26) or the 4-digit tax office number (e.g. 2653)."""
    ref = _digits(state_number)
    if len(ref) not in (2, 4):
        return Result(False, -1, "Pass the 2-digit state number (see elster_list_states, e.g. 26 for Hessen) or the 4-digit tax office number.")
    landesnr, bufa = (ref, None) if len(ref) == 2 else (None, ref)
    with _LOCK:
        eric, _ = _runtime()
        with _buffer(eric) as h:
            code = eric.PyEricMakeElsterStnr(tax_number.strip(), landesnr, bufa, h)
            value = _text(eric, h)
        return Result(
            code == 0,
            code,
            _error_text(eric, code),
            {"elster_tax_number": value} if code == 0 else None,
        )


def list_test_tax_offices() -> Result:
    with _LOCK:
        eric, _ = _runtime()
        with _buffer(eric) as h:
            code = eric.PyEricHoleTestfinanzaemter(h)
            xml = _text(eric, h)
        offices = [{"number": n, "name": m} for n, m in re.findall(r"<BuFaNummer>(\d+)</BuFaNummer>\s*<Name>([^<]*)</Name>", xml)]
        return Result(
            code == 0,
            code,
            _error_text(eric, code),
            {"count": len(offices), "offices": offices},
        )


def list_states() -> Result:
    with _LOCK:
        eric, _ = _runtime()
        with _buffer(eric) as h:
            code = eric.PyEricHoleFinanzamtLandNummern(h)
            xml = _text(eric, h)
        states = [{"number": n, "name": m} for n, m in re.findall(r"<FinanzamtLandNummer>(\d+)</FinanzamtLandNummer>\s*<Name>([^<]*)</Name>", xml)]
        return Result(code == 0, code, _error_text(eric, code), {"count": len(states), "states": states})


def list_tax_offices(state_number: str) -> Result:
    with _LOCK:
        eric, _ = _runtime()
        with _buffer(eric) as h:
            code = eric.PyEricHoleFinanzaemter(_digits(state_number), h)
            xml = _text(eric, h)
        offices = [{"number": n, "name": m} for n, m in re.findall(r"<BuFaNummer>(\d+)</BuFaNummer>\s*<Name>([^<]*)</Name>", xml)]
        return Result(code == 0, code, _error_text(eric, code), {"count": len(offices), "offices": offices})


def tax_office_info(bufa_number: str) -> Result:
    with _LOCK:
        eric, _ = _runtime()
        with _buffer(eric) as h:
            code = eric.PyEricHoleFinanzamtsdaten(_digits(bufa_number), h)
            xml = _text(eric, h)
        fields = dict(re.findall(r"<(\w+)>([^<]+)</\1>", xml))
        return Result(
            code == 0,
            code,
            _error_text(eric, code),
            {"office": fields} if code == 0 else None,
        )


def certificate_info() -> Result:
    with _LOCK:
        eric, conf = _runtime()
        if not conf.cert_path or not conf.cert_path.is_file():
            return Result(False, -1, "ELSTER_CERT_PATH is not set or the file does not exist.")
        pin = cfg.cert_password()
        code, token, _ = eric.PyEricGetHandleToCertificate(str(conf.cert_path).encode())
        if code != 0:
            return Result(False, code, _error_text(eric, code))
        try:
            with _buffer(eric) as h:
                code = eric.PyEricHoleZertifikatEigenschaften(token, pin.encode(), h)
                xml = _text(eric, h)
        finally:
            eric.PyEricCloseHandleToCertificate(token)
        valid_until = re.findall(r"<GueltigBis>([^<]+)</GueltigBis>", xml)
        subject = re.findall(r"<Subjekt>([^<]+)</Subjekt>", xml)
        return Result(
            code == 0,
            code,
            _error_text(eric, code),
            {
                "certificate_file": conf.cert_path.name,
                "valid_until": valid_until[0] if valid_until else None,
                "subject": subject[0] if subject else None,
            },
        )


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _prepare(xml: str, conf: cfg.Config, test: bool) -> str:
    if not conf.hersteller_id:
        raise cfg.ConfigError("ELSTER_HERSTELLER_ID is not set. ELSTER rejects returns without your own Hersteller-ID, even for tests.")
    xml = re.sub(
        r"<HerstellerID>[^<]*</HerstellerID>",
        f"<HerstellerID>{conf.hersteller_id}</HerstellerID>",
        xml,
    )
    has_marker = re.search(rf"<Testmerker>{cfg.TEST_MARKER}</Testmerker>", xml) is not None
    if test and not has_marker:
        if "<Testmerker>" in xml:
            xml = re.sub(
                r"<Testmerker>[^<]*</Testmerker>",
                f"<Testmerker>{cfg.TEST_MARKER}</Testmerker>",
                xml,
            )
        else:
            xml = xml.replace(
                "</Verfahren>",
                f"</Verfahren><Testmerker>{cfg.TEST_MARKER}</Testmerker>",
                1,
            )
    if not test and has_marker:
        raise cfg.ConfigError("Live submission requested but the XML carries the test marker. Remove <Testmerker> first.")
    return xml


def process(xml: str, data_type_version: str, *, send: bool, test: bool, pdf_path: str | None) -> Result:
    """Validate (and optionally send) a return. Returns ERiC's response and the server reply."""
    with _LOCK:
        eric, conf = _runtime()
        if send and not test and not conf.allow_live:
            return Result(
                False,
                -1,
                "Live submission is disabled. Set ELSTER_ALLOW_LIVE=1 to allow real filings.",
            )
        prepared = _prepare(xml, conf, test)
        flags = ERIC_VALIDIERE | (ERIC_SENDE if send else 0)
        print_params = None
        if pdf_path:
            flags |= ERIC_DRUCKE
            print_params = _API.eric_druck_parameter_t()
            print_params.version = 4
            print_params.vorschau = 0 if send else 1
            print_params.duplexDruck = 0
            print_params.pdfName = str(Path(pdf_path).expanduser()).encode()
            print_params.fussText = None
            print_params.set_callback(None)
        crypto = None
        token = None
        if send:
            if not conf.cert_path or not conf.cert_path.is_file():
                return Result(
                    False,
                    -1,
                    "Sending needs ELSTER_CERT_PATH pointing at your .pfx certificate.",
                )
            code, token, _ = eric.PyEricGetHandleToCertificate(str(conf.cert_path).encode())
            if code != 0:
                return Result(False, code, _error_text(eric, code))
            crypto = _API.eric_verschluesselungs_parameter_t()
            crypto.version = 3
            crypto.zertifikatHandle = token
            crypto.pin = cfg.cert_password().encode()
        try:
            with _buffer(eric) as resp, _buffer(eric) as server:
                code = eric.PyEricBearbeiteVorgang(
                    datenpuffer=prepared.encode("utf-8"),
                    datenartVersion=data_type_version,
                    bearbeitungsFlags=flags,
                    druckParameter=print_params,
                    cryptoParameter=crypto,
                    rueckgabeXmlPuffer=resp,
                    serverantwortXmlPuffer=server if send else None,
                )
                response = _text(eric, resp)
                server_reply = _text(eric, server) if send else ""
        finally:
            if token is not None:
                eric.PyEricCloseHandleToCertificate(token)
        findings = re.findall(r"<Text>([^<]+)</Text>", response)
        transfer = re.findall(r"<TransferTicket>([^<]+)</TransferTicket>", response + server_reply)
        return Result(
            code == 0,
            code,
            _error_text(eric, code),
            {
                "mode": ("test submission" if test else "LIVE submission") if send else "validation only",
                "findings": findings[:50],
                "transfer_ticket": transfer[0] if transfer else None,
                "pdf": pdf_path if pdf_path and code == 0 else None,
            },
        )
