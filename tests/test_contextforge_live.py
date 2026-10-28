"""Against a running ContextForge + legacy API + triage agent (see README).

    CONTEXTFORGE_LIVE=1 pytest tests/test_contextforge_live.py
needs JWT_SECRET_KEY, LEGACY_API_KEY and AGENTMESH_UPSTREAM_KEY as used by those services.
"""

import os

import pytest

from agentmesh import ToolCallError, mcp_session
from legacybridge.cli import unwrap_a2a
from legacybridge.register import load_config, register

pytestmark = pytest.mark.skipif(os.environ.get("CONTEXTFORGE_LIVE") != "1", reason="needs a live ContextForge")


@pytest.fixture(scope="module")
def reg():
    return register(load_config(os.path.join(os.path.dirname(__file__), "..", "bridge.yaml")))


async def test_registration_is_idempotent(reg):
    again = register(load_config(os.path.join(os.path.dirname(__file__), "..", "bridge.yaml")))
    assert again["server_id"] == reg["server_id"] and sorted(again["tools"]) == sorted(reg["tools"])


async def test_scoped_token_sees_only_allowlisted_tools(reg):
    async with mcp_session(reg["mcp_url"], reg["tokens"]["analyst"]) as tools:
        names = await tools.names()
        assert "legacy-get-asset" in names and "a2a-triage" in names
        assert not any("decommission" in n for n in names)
        with pytest.raises(ToolCallError):
            await tools.call("legacy-decommission-asset", asset_id="SRV-1001")


async def test_no_token_is_rejected(reg):
    with pytest.raises(ToolCallError):
        async with mcp_session(reg["mcp_url"], None) as tools:
            await tools.names()


async def test_triage_through_contextforge(reg):
    async with mcp_session(reg["mcp_url"], reg["tokens"]["analyst"]) as tools:
        out = unwrap_a2a(await tools.call("a2a-triage", query="triage INC-5001"))
        assert out["ticket_id"] == "INC-5001" and out["severity"] in {"low", "medium", "high"}
        ticket = await tools.call("legacy-get-ticket", ticket_id="INC-5001")
        assert any(c["author"] == "triage-agent" for c in ticket["comments"])
