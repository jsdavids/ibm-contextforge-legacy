"""legacybridge command line.

    legacybridge register              publish the legacy tools + triage agent in ContextForge
    legacybridge triage INC-5001       ask the triage agent (via ContextForge) as the analyst
    legacybridge tools                 list what the analyst's scoped token can see
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from agentmesh import mcp_session
from legacybridge.register import load_config, register

TOKENS_FILE = Path(os.environ.get("LEGACYBRIDGE_TOKENS", ".cf_tokens.json"))


def unwrap_a2a(result: Any) -> Any:
    """ContextForge returns the agent's raw A2A JSON-RPC response; pull out
    the JSON result artifact."""
    task = (result or {}).get("result", {}).get("task", {}) if isinstance(result, dict) else {}
    for artifact in task.get("artifacts", []):
        for part in artifact.get("parts", []):
            if part.get("mediaType") == "application/json":
                return json.loads(part["text"])
    return result


def _load_tokens() -> dict[str, Any]:
    if not TOKENS_FILE.exists():
        raise SystemExit("run `legacybridge register` first")
    return json.loads(TOKENS_FILE.read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="legacybridge")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_reg = sub.add_parser("register")
    p_reg.add_argument("--config", default="bridge.yaml")
    p_tri = sub.add_parser("triage")
    p_tri.add_argument("ticket_id")
    p_tri.add_argument("--as", dest="identity", default="analyst")
    p_tools = sub.add_parser("tools")
    p_tools.add_argument("--as", dest="identity", default="analyst")
    args = parser.parse_args(argv)

    if args.cmd == "register":
        out = register(load_config(args.config))
        TOKENS_FILE.write_text(json.dumps({"mcp_url": out["mcp_url"], "tokens": out["tokens"]}, indent=2))
        os.chmod(TOKENS_FILE, 0o600)
        print(f"virtual server {out['server_id']}\nMCP endpoint  {out['mcp_url']}")
        print(f"tools         {', '.join(out['tools'])}")
        print(f"tokens for {', '.join(out['tokens'])} written to {TOKENS_FILE} (mode 600)")
        print(f"\nstart the agent with:\n  CONTEXTFORGE_MCP_URL={out['mcp_url']} CONTEXTFORGE_TOKEN=$(jq -r '.tokens[\"triage-agent\"]' {TOKENS_FILE}) \\\n"
              "  python -m agentmesh.serve legacybridge.triage:app --port 9310")
        return 0

    saved = _load_tokens()

    async def run() -> Any:
        async with mcp_session(saved["mcp_url"], saved["tokens"][args.identity]) as tools:
            if args.cmd == "tools":
                return await tools.names()
            return unwrap_a2a(await tools.call("a2a-triage", query=f"triage {args.ticket_id}"))

    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
