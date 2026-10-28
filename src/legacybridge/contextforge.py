"""Minimal ContextForge (IBM mcp-context-forge 1.1.x) admin client.

Uses the REST admin API (`MCPGATEWAY_ADMIN_API_ENABLED=true`):
    POST /tools     register a REST endpoint as an MCP tool
    POST /a2a       register an A2A agent
    POST /servers   bundle tools + agents into a virtual MCP server,
                    served at /servers/{id}/mcp
"""

from __future__ import annotations

import secrets
import time
import uuid
from typing import Any

import httpx
import jwt


def mint_token(secret: str, email: str, *, admin: bool = False, ttl_minutes: int = 60,
               server_id: str | None = None, permissions: list[str] | None = None,
               teams: list[str] | None = None) -> str:
    """A token in the shape ContextForge's own `create_jwt_token` utility emits.

    Non-admin tokens default to `teams: []`, which ContextForge treats as
    public-only access; `server_id`/`permissions` narrow it further."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": email, "username": email, "iss": "mcpgateway", "aud": "mcpgateway-api",
        "iat": now, "exp": now + ttl_minutes * 60, "jti": str(uuid.uuid4()),
        "user": {"email": email, "full_name": email.split("@")[0], "is_admin": admin, "auth_provider": "cli"},
        "teams": None if admin else (teams or []),
    }
    if server_id or permissions:
        claims["scopes"] = {"server_id": server_id, "permissions": permissions or ["*"],
                            "ip_restrictions": [], "time_restrictions": {}}
    return jwt.encode(claims, secret, algorithm="HS256")


def _service_password(length: int = 28) -> str:
    """Random password that passes ContextForge's policy, which rejects runs
    like "abc"/"321" and repeated characters."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789!#%*+-=?@"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        runs = any(abs(ord(a.lower()) - ord(b.lower())) == 1 and ord(b.lower()) - ord(a.lower()) == ord(c.lower()) - ord(b.lower())
                   for a, b, c in zip(pw, pw[1:], pw[2:]))
        repeats = any(a == b for a, b in zip(pw, pw[1:]))
        classes = all(any(f(ch) for ch in pw) for f in (str.isupper, str.islower, str.isdigit, lambda ch: not ch.isalnum()))
        if classes and not runs and not repeats:
            return pw


class ContextForge:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, transport=transport, timeout=30.0,
                                 headers={"authorization": f"Bearer {token}"})

    def _check(self, resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            raise RuntimeError(f"{resp.request.method} {resp.request.url.path} -> {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else None

    def _list(self, path: str) -> list[dict[str, Any]]:
        data = self._check(self.http.get(path, params={"include_inactive": "false", "limit": 0}))
        if isinstance(data, dict):  # cursor-paginated form
            data = data.get("items") or data.get("tools") or data.get("servers") or data.get("agents") or []
        return data

    def health(self) -> dict[str, Any]:
        return self._check(self.http.get("/health"))

    def upsert_tool(self, tool: dict[str, Any]) -> dict[str, Any]:
        existing = {t["name"]: t for t in self._list("/tools")}
        if tool["name"] in existing:
            return self._check(self.http.put(f"/tools/{existing[tool['name']]['id']}", json=tool))
        return self._check(self.http.post("/tools", json={"tool": tool, "visibility": tool.get("visibility", "public")}))

    def upsert_a2a_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        existing = {a["name"]: a for a in self._list("/a2a")}
        if agent["name"] in existing:
            return self._check(self.http.put(f"/a2a/{existing[agent['name']]['id']}", json=agent))
        return self._check(self.http.post("/a2a", json={"agent": agent, "visibility": "public"}))

    def upsert_server(self, server: dict[str, Any]) -> dict[str, Any]:
        existing = {s["name"]: s for s in self._list("/servers")}
        if server["name"] in existing:
            return self._check(self.http.put(f"/servers/{existing[server['name']]['id']}", json=server))
        return self._check(self.http.post("/servers", json={"server": server, "visibility": "public"}))

    def ensure_user(self, email: str, full_name: str | None = None) -> None:
        """Create a non-admin service identity. ContextForge rejects tokens for
        users it doesn't know (REQUIRE_USER_IN_DB), so agents need a real
        account; the random password is discarded because agents only ever
        authenticate with scoped tokens."""
        resp = self.http.get(f"/auth/email/admin/users/{email}")
        if resp.status_code == 200:
            return
        self._check(self.http.post("/auth/email/admin/users", json={
            "email": email, "password": _service_password(),
            "full_name": full_name or email.split("@")[0], "is_admin": False,
        }))

    def ensure_role(self, name: str, permissions: list[str], description: str = "") -> str:
        roles = {r["name"]: r for r in self._check(self.http.get("/rbac/roles"))}
        if name in roles:
            return roles[name]["id"]
        return self._check(self.http.post("/rbac/roles", json={
            "name": name, "description": description, "scope": "global", "permissions": permissions}))["id"]

    def grant_role(self, email: str, role_id: str) -> None:
        current = self._check(self.http.get(f"/rbac/users/{email}/roles"))
        if any(r.get("role_id") == role_id for r in current):
            return
        self._check(self.http.post(f"/rbac/users/{email}/roles", json={"role_id": role_id, "scope": "global"}))

    def tools(self) -> list[dict[str, Any]]:
        return self._list("/tools")
