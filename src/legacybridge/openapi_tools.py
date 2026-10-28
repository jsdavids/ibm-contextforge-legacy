"""Turn selected OpenAPI operations into ContextForge REST tool definitions.

Only operations on the allowlist become tools, so a destructive endpoint
(here DELETE /assets/{id}) is simply never reachable by an agent, whatever
its prompt says. The legacy API key is attached as an encrypted auth header
in ContextForge; agents never see it.
"""

from __future__ import annotations

import re
from typing import Any

_METHODS = ("get", "post", "put", "patch", "delete")


def kebab(operation_id: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", operation_id).lower()


def _input_schema(op: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in op.get("parameters", []):
        props[p["name"]] = {**p.get("schema", {"type": "string"}),
                            "description": p.get("description", f"{p['in']} parameter")}
        if p.get("required"):
            required.append(p["name"])
    body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if body:
        props.update(body.get("properties", {}))
        required += [r for r in body.get("required", []) if r not in required]
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def openapi_to_tools(spec: dict[str, Any], base_url: str, include: dict[str, str], *,
                     api_key: str, api_key_header: str = "X-API-Key", prefix: str = "legacy",
                     tags: list[str] | None = None) -> list[dict[str, Any]]:
    """`include` maps operationId -> description shown to agents."""
    tools, seen = [], set()
    for path, item in spec.get("paths", {}).items():
        for method in _METHODS:
            op = item.get(method)
            if not op or op.get("operationId") not in include:
                continue
            seen.add(op["operationId"])
            tools.append({
                "name": f"{prefix}-{kebab(op['operationId'])}",
                "description": include[op["operationId"]],
                "url": base_url.rstrip("/") + path,
                "integration_type": "REST",
                "request_type": method.upper(),
                "input_schema": _input_schema(op),
                "auth_type": "authheaders",
                "auth_headers": [{"key": api_key_header, "value": api_key}],
                "annotations": {"readOnlyHint": method == "get", "destructiveHint": method == "delete"},
                "tags": tags or ["legacy-service-desk"],
            })
    missing = set(include) - seen
    if missing:
        raise KeyError(f"operations not found in the OpenAPI spec: {sorted(missing)}")
    return tools
