"""Triage agent (A2A). Given an incident ticket, it looks up the affected
asset and related tickets through ContextForge's MCP endpoint, scores the
risk, and writes a triage note back to the legacy system.

Its MCP access is ContextForge's virtual server, authenticated with its own
ContextForge token; the legacy API key lives only inside ContextForge.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from agentmesh import AgentContext, Skill, build_agent_app, mcp_session

# Operating systems past end of standard support: patching risk.
END_OF_SUPPORT = {"RHEL 7": "2024-06-30", "AIX 7.1": "2023-04-30", "Windows Server 2012": "2023-10-10",
                  "CentOS 7": "2024-06-30", "IOS-XE 16": "2024-12-31"}
CRITICAL_OWNERS = {"finance-it", "hr-it"}


def assess(ticket: dict[str, Any], asset: dict[str, Any], related: list[dict[str, Any]]) -> dict[str, Any]:
    reasons, score = [], {"P1": 4, "P2": 3, "P3": 2, "P4": 1}.get(ticket.get("priority"), 2)
    eos = next((f"{prefix} (end of support {d})" for prefix, d in END_OF_SUPPORT.items()
                if asset.get("os", "").startswith(prefix)), None)
    if eos:
        score += 2
        reasons.append(f"asset runs {eos}")
    if asset.get("owner") in CRITICAL_OWNERS:
        score += 1
        reasons.append(f"business-critical owner {asset['owner']}")
    others = [t["ticket_id"] for t in related if t["ticket_id"] != ticket["ticket_id"] and t["status"] == "open"]
    if others:
        score += 1
        reasons.append(f"other open incidents on this asset: {', '.join(others)}")
    if asset.get("status") != "active":
        reasons.append(f"asset status is {asset.get('status')}")
    severity = "high" if score >= 6 else "medium" if score >= 4 else "low"
    recommended = {"high": "P1", "medium": "P2", "low": ticket.get("priority", "P3")}[severity]
    return {"severity": severity, "score": score, "reasons": reasons, "recommended_priority": recommended}


def ticket_id_from(payload: dict[str, Any]) -> str:
    """JSON callers send {"ticket_id": ...}; ContextForge's A2A tool wrapper
    sends free text ("triage INC-5001"), which arrives as {"text": ...}."""
    if payload.get("ticket_id"):
        return payload["ticket_id"]
    match = re.search(r"\bINC-\d+\b", payload.get("text", ""), re.I)
    if not match:
        raise ValueError("no ticket id (INC-nnnn) in request")
    return match.group(0).upper()


def contextforge_access() -> tuple[str, str | None]:
    """Endpoint + token from env, or from the file `legacybridge register`
    writes (how docker compose hands them over after registration)."""
    if os.environ.get("CONTEXTFORGE_MCP_URL"):
        return os.environ["CONTEXTFORGE_MCP_URL"], os.environ.get("CONTEXTFORGE_TOKEN")
    saved = json.loads(Path(os.environ.get("LEGACYBRIDGE_TOKENS", ".cf_tokens.json")).read_text())
    return saved["mcp_url"], saved["tokens"][os.environ.get("CONTEXTFORGE_IDENTITY", "triage-agent")]


async def triage(payload: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    url, token = contextforge_access()
    async with mcp_session(url, token) as tools:
        ticket = await tools.call("legacy-get-ticket", ticket_id=ticket_id_from(payload))
        asset = await tools.call("legacy-get-asset", asset_id=ticket["asset_id"])
        related = (await tools.call("legacy-list-tickets", asset_id=ticket["asset_id"]))["tickets"]
        result = assess(ticket, asset, related)
        note = (f"[triage-agent] severity={result['severity']} recommend {result['recommended_priority']}: "
                + ("; ".join(result["reasons"]) or "no aggravating factors"))
        if payload.get("write_back", True):
            await tools.call("legacy-add-comment", ticket_id=ticket["ticket_id"], text=note, author="triage-agent")
    return {"ticket_id": ticket["ticket_id"], "asset": asset["asset_id"], **result, "note": note}


def app(url: str | None = None, mesh=None):
    return build_agent_app(
        name="triage", description="Triages service-desk incidents using the legacy asset and ticket systems",
        url=url or os.environ.get("TRIAGE_PUBLIC_URL", "http://127.0.0.1:9310"), handler=triage, mesh=mesh,
        skills=[Skill("triage_incident", "Triage incident", "Scores an incident and writes a triage note",
                      tags=["itsm", "triage"], examples=['{"ticket_id": "INC-5001"}'])],
    )
