"""Register the legacy service and the triage agent with ContextForge."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import yaml

from agentmesh.serve import UPSTREAM_KEY_ENV, UPSTREAM_KEY_HEADER
from legacybridge.contextforge import ContextForge, mint_token
from legacybridge.openapi_tools import openapi_to_tools


def load_config(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


def register(config: dict[str, Any], transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    cf_cfg, legacy = config["contextforge"], config["legacy"]
    secret = os.environ[cf_cfg.get("jwt_secret_env", "JWT_SECRET_KEY")]
    admin = ContextForge(cf_cfg["url"], mint_token(secret, cf_cfg["admin_email"], admin=True, ttl_minutes=10),
                         transport=transport)
    admin.health()

    with httpx.Client(transport=transport, timeout=10) as http:
        spec = http.get(legacy["openapi_url"]).raise_for_status().json()
    tools = openapi_to_tools(spec, legacy["base_url"], legacy["expose"],
                             api_key=os.environ[legacy.get("api_key_env", "LEGACY_API_KEY")])
    registered = [admin.upsert_tool(t) for t in tools]

    # ContextForge presents this key to the agent on every call (stored encrypted), and the
    # agent rejects requests without it, so the agent is reachable only through the gateway.
    upstream_key = os.environ.get(UPSTREAM_KEY_ENV)
    agent_auth = ({"auth_type": "authheaders", "auth_headers": [{"key": UPSTREAM_KEY_HEADER, "value": upstream_key}]}
                  if upstream_key else {})
    agents = [admin.upsert_a2a_agent({
        "name": a["name"], "description": a.get("description", ""), "endpoint_url": a["url"],
        "agent_type": "generic", "protocol_version": "1.0", "tags": a.get("tags", []), **agent_auth,
    }) for a in config.get("a2a_agents", [])]

    # ContextForge wraps each A2A agent as an MCP tool named a2a-<name>
    agent_tools = [t for t in admin.tools() if t["name"] in {f"a2a-{a['name']}" for a in agents}]
    server = admin.upsert_server({
        "name": config["virtual_server"]["name"],
        "description": config["virtual_server"].get("description", ""),
        "associated_tools": [t["id"] for t in registered + agent_tools],
        "associated_a2a_agents": [a["id"] for a in agents],
    })
    mcp_url = f"{cf_cfg['url'].rstrip('/')}/servers/{server['id']}/mcp"

    # Least-privilege tokens: scoped to this virtual server, public resources only.
    # A custom role with only what an MCP client needs; ContextForge's RBAC
    # denies tools/call to users without tools.execute.
    role_id = admin.ensure_role("mcp-tool-user", ["servers.read", "servers.use", "tools.read", "tools.execute"],
                                "Call tools on virtual servers; no admin or write access to the catalog")
    for email in config.get("identities", {}).values():
        admin.ensure_user(email)
        admin.grant_role(email, role_id)
    tokens = {
        name: mint_token(secret, email, server_id=server["id"], ttl_minutes=int(cf_cfg.get("token_ttl_minutes", 720)))
        for name, email in config.get("identities", {}).items()
    }
    return {"server_id": server["id"], "mcp_url": mcp_url, "tools": [t["name"] for t in registered + agent_tools],
            "a2a_agents": [a["name"] for a in agents], "tokens": tokens}
