"""ELSTER MCP server: German tax e-filing through the official ERiC library."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import config as cfg
from . import eric

INSTRUCTIONS = (
    "Tools for German tax e-filing via ELSTER using the official ERiC library on this machine. "
    "Read-only checks (tax ID, IBAN, tax number, tax offices, certificate) never contact ELSTER. "
    "validate_return checks a return locally without sending. submit_return sends to ELSTER: "
    "test submissions by default; live filing only when the operator enabled it AND the user confirmed. "
    "Only file returns for the certificate holder themselves; filing for others needs a Steuerberater."
)

mcp = MCPServer("elster", instructions=INSTRUCTIONS)
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _safe(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs).as_dict()
    except cfg.ConfigError as exc:
        return {"ok": False, "code": -1, "message": str(exc)}


@mcp.tool(annotations=READ_ONLY)
def elster_system_check() -> dict:
    """Check that ERiC loads on this machine and show what is configured (certificate, Hersteller-ID, live mode). Secrets are never returned."""
    return _safe(eric.system_check)


@mcp.tool(annotations=READ_ONLY)
def elster_check_tax_id(tax_id: str) -> dict:
    """Validate a German tax ID (steuerliche Identifikationsnummer, 11 digits) with ERiC's official check. Offline."""
    return _safe(eric.check_tax_id, tax_id)


@mcp.tool(annotations=READ_ONLY)
def elster_check_iban(iban: str) -> dict:
    """Validate an IBAN with ERiC's check-digit rules. Offline."""
    return _safe(eric.check_iban, iban)


@mcp.tool(annotations=READ_ONLY)
def elster_check_tax_number(elster_tax_number: str) -> dict:
    """Validate a tax number already in the 13-digit ELSTER format. Use elster_convert_tax_number first if you only have the number from a tax notice."""
    return _safe(eric.check_tax_number, elster_tax_number)


@mcp.tool(annotations=READ_ONLY)
def elster_convert_tax_number(tax_number: str, state: str) -> dict:
    """Convert a tax number as printed on a tax notice (e.g. 053 815 08158) into the 13-digit ELSTER format.
    state is the 2-digit state number from elster_list_states (e.g. 26 = Hessen) or the 4-digit tax office number.
    Bavarian and Berlin numbers in the format BBB/UUUUP need the 4-digit tax office number."""
    return _safe(eric.make_elster_tax_number, tax_number, state)


@mcp.tool(annotations=READ_ONLY)
def elster_list_test_tax_offices() -> dict:
    """List ELSTER's test tax offices (numbers to use as recipient in test submissions)."""
    return _safe(eric.list_test_tax_offices)


@mcp.tool(annotations=READ_ONLY)
def elster_list_states() -> dict:
    """List the federal-state numbers ELSTER uses for tax offices (e.g. 26 = Hessen)."""
    return _safe(eric.list_states)


@mcp.tool(annotations=READ_ONLY)
def elster_list_tax_offices(state_number: str) -> dict:
    """List all tax offices (Finanzämter) of one federal state, by the state number from elster_list_states."""
    return _safe(eric.list_tax_offices, state_number)


@mcp.tool(annotations=READ_ONLY)
def elster_tax_office_info(tax_office_number: str) -> dict:
    """Look up a tax office (Finanzamt) by its 4-digit number: name, address, contact details."""
    return _safe(eric.tax_office_info, tax_office_number)


@mcp.tool(annotations=READ_ONLY)
def elster_certificate_info() -> dict:
    """Open the configured ELSTER certificate (.pfx) and report its validity. The password is never returned."""
    return _safe(eric.certificate_info)


def _read_xml(xml: str | None, xml_path: str | None) -> str:
    if xml_path:
        return Path(xml_path).expanduser().read_text(encoding="utf-8")
    if xml:
        return xml
    raise cfg.ConfigError("Provide either xml (the ELSTER XML as text) or xml_path (path to the file).")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def elster_validate_return(
    data_type_version: str,
    xml: str | None = None,
    xml_path: str | None = None,
    preview_pdf_path: str | None = None,
) -> dict:
    """Validate a tax return locally with ERiC, without sending it. data_type_version is e.g. UStVA_2026 or ESt_2025.
    Optionally writes a preview PDF. Requires ELSTER_HERSTELLER_ID."""
    try:
        return eric.process(
            _read_xml(xml, xml_path),
            data_type_version,
            send=False,
            test=True,
            pdf_path=preview_pdf_path,
        ).as_dict()
    except cfg.ConfigError as exc:
        return {"ok": False, "code": -1, "message": str(exc)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def elster_submit_return(
    data_type_version: str,
    xml: str | None = None,
    xml_path: str | None = None,
    live: bool = False,
    user_confirmed_live: bool = False,
    receipt_pdf_path: str | None = None,
) -> dict:
    """Send a return to ELSTER, signed with the configured certificate.
    Default is a TEST submission (Testmerker 700000004): nothing is filed with the tax office.
    A LIVE filing needs live=true, user_confirmed_live=true (only after the user explicitly approved this exact return),
    and the operator setting ELSTER_ALLOW_LIVE=1. Only file for the certificate holder."""
    if live and not user_confirmed_live:
        return {
            "ok": False,
            "code": -1,
            "message": "Live filing needs the user's explicit approval of this exact return (user_confirmed_live=true).",
        }
    try:
        return eric.process(
            _read_xml(xml, xml_path),
            data_type_version,
            send=True,
            test=not live,
            pdf_path=receipt_pdf_path,
        ).as_dict()
    except cfg.ConfigError as exc:
        return {"ok": False, "code": -1, "message": str(exc)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
