"""A stand-in "legacy" IT service desk: plain REST + JSON, a static API key,
no MCP, no OAuth. Typical of the internal systems an enterprise wants
agents to use without rewriting them.

    GET    /api/v1/assets?status=&owner=     list assets
    GET    /api/v1/assets/{asset_id}         one asset
    DELETE /api/v1/assets/{asset_id}         decommission (never exposed to agents)
    GET    /api/v1/tickets?status=&asset_id= list tickets
    GET    /api/v1/tickets/{ticket_id}       one ticket with comments
    POST   /api/v1/tickets                   open a ticket
    POST   /api/v1/tickets/{ticket_id}/comments
    GET    /openapi.json
"""

from __future__ import annotations

import copy
import hmac
import os
from datetime import UTC, datetime
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

SEED_ASSETS = [
    {"asset_id": "SRV-1001", "type": "server", "hostname": "payroll-db-01", "owner": "finance-it",
     "status": "active", "os": "RHEL 7.9", "location": "DC-East"},
    {"asset_id": "SRV-1002", "type": "server", "hostname": "hr-app-02", "owner": "hr-it",
     "status": "active", "os": "RHEL 9.4", "location": "DC-West"},
    {"asset_id": "NET-2001", "type": "switch", "hostname": "core-sw-01", "owner": "network",
     "status": "active", "os": "IOS-XE 17.9", "location": "DC-East"},
    {"asset_id": "SRV-0999", "type": "server", "hostname": "legacy-mainframe-gw", "owner": "finance-it",
     "status": "retired", "os": "AIX 7.1", "location": "DC-East"},
]
SEED_TICKETS = [
    {"ticket_id": "INC-5001", "asset_id": "SRV-1001", "summary": "Nightly payroll batch exceeded window",
     "priority": "P2", "status": "open", "opened": "2026-10-20T03:12:00Z", "comments": []},
    {"ticket_id": "INC-5002", "asset_id": "NET-2001", "summary": "Intermittent packet loss on uplink 2",
     "priority": "P3", "status": "resolved", "opened": "2026-10-11T14:40:00Z",
     "comments": [{"author": "network", "text": "Replaced SFP; monitoring.", "at": "2026-10-12T09:00:00Z"}]},
]
PRIORITIES = {"P1", "P2", "P3", "P4"}

OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Legacy Service Desk", "version": "2.4.1"},
    "components": {"securitySchemes": {"apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}},
    "security": [{"apiKey": []}],
    "paths": {
        "/api/v1/assets": {"get": {"operationId": "listAssets", "parameters": [
            {"name": "status", "in": "query", "schema": {"type": "string", "enum": ["active", "retired"]}},
            {"name": "owner", "in": "query", "schema": {"type": "string"}}]}},
        "/api/v1/assets/{asset_id}": {
            "get": {"operationId": "getAsset", "parameters": [{"name": "asset_id", "in": "path", "required": True, "schema": {"type": "string"}}]},
            "delete": {"operationId": "decommissionAsset", "parameters": [{"name": "asset_id", "in": "path", "required": True, "schema": {"type": "string"}}]}},
        "/api/v1/tickets": {
            "get": {"operationId": "listTickets", "parameters": [
                {"name": "status", "in": "query", "schema": {"type": "string", "enum": ["open", "resolved"]}},
                {"name": "asset_id", "in": "query", "schema": {"type": "string"}}]},
            "post": {"operationId": "createTicket", "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "required": ["asset_id", "summary"],
                "properties": {"asset_id": {"type": "string"}, "summary": {"type": "string"},
                               "priority": {"type": "string", "enum": sorted(PRIORITIES)}}}}}}}},
        "/api/v1/tickets/{ticket_id}": {"get": {"operationId": "getTicket", "parameters": [
            {"name": "ticket_id", "in": "path", "required": True, "schema": {"type": "string"}}]}},
        "/api/v1/tickets/{ticket_id}/comments": {"post": {"operationId": "addComment", "parameters": [
            {"name": "ticket_id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "required": ["text"], "properties": {"text": {"type": "string"}, "author": {"type": "string"}}}}}}}},
    },
}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_app(api_key: str | None = None) -> Starlette:
    api_key = api_key or os.environ.get("LEGACY_API_KEY", "")
    if not api_key:
        raise RuntimeError("set LEGACY_API_KEY")
    assets = {a["asset_id"]: copy.deepcopy(a) for a in SEED_ASSETS}
    tickets = {t["ticket_id"]: copy.deepcopy(t) for t in SEED_TICKETS}
    counter = {"next": 5003}

    def authorized(request: Request) -> bool:
        return hmac.compare_digest(request.headers.get("x-api-key", ""), api_key)

    def guard(handler):
        async def wrapped(request: Request):
            if not authorized(request):
                return JSONResponse({"error": "invalid or missing X-API-Key"}, status_code=401)
            return await handler(request)
        return wrapped

    async def list_assets(request: Request):
        q = request.query_params
        rows = [a for a in assets.values()
                if (not q.get("status") or a["status"] == q["status"]) and (not q.get("owner") or a["owner"] == q["owner"])]
        return JSONResponse({"assets": rows, "count": len(rows)})

    async def asset(request: Request):
        asset_id = request.path_params["asset_id"]
        if asset_id not in assets:
            return JSONResponse({"error": f"asset {asset_id} not found"}, status_code=404)
        if request.method == "DELETE":
            assets[asset_id]["status"] = "decommissioned"
        return JSONResponse(assets[asset_id])

    async def list_tickets(request: Request):
        q = request.query_params
        rows = [t for t in tickets.values()
                if (not q.get("status") or t["status"] == q["status"]) and (not q.get("asset_id") or t["asset_id"] == q["asset_id"])]
        return JSONResponse({"tickets": rows, "count": len(rows)})

    async def create_ticket(request: Request):
        body: dict[str, Any] = await request.json()
        if body.get("asset_id") not in assets:
            return JSONResponse({"error": "unknown asset_id"}, status_code=422)
        if not str(body.get("summary", "")).strip():
            return JSONResponse({"error": "summary is required"}, status_code=422)
        priority = body.get("priority", "P3")
        if priority not in PRIORITIES:
            return JSONResponse({"error": f"priority must be one of {sorted(PRIORITIES)}"}, status_code=422)
        ticket_id = f"INC-{counter['next']}"
        counter["next"] += 1
        tickets[ticket_id] = {"ticket_id": ticket_id, "asset_id": body["asset_id"], "summary": body["summary"][:200],
                              "priority": priority, "status": "open", "opened": _now(), "comments": []}
        return JSONResponse(tickets[ticket_id], status_code=201)

    async def ticket(request: Request):
        ticket_id = request.path_params["ticket_id"]
        if ticket_id not in tickets:
            return JSONResponse({"error": f"ticket {ticket_id} not found"}, status_code=404)
        return JSONResponse(tickets[ticket_id])

    async def add_comment(request: Request):
        ticket_id = request.path_params["ticket_id"]
        if ticket_id not in tickets:
            return JSONResponse({"error": f"ticket {ticket_id} not found"}, status_code=404)
        body = await request.json()
        comment = {"author": body.get("author", "agent"), "text": str(body.get("text", ""))[:2000], "at": _now()}
        tickets[ticket_id]["comments"].append(comment)
        return JSONResponse(tickets[ticket_id], status_code=201)

    async def openapi(request: Request):
        return JSONResponse(OPENAPI)

    async def health(request: Request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[
        Route("/health", health),
        Route("/openapi.json", openapi),
        Route("/api/v1/assets", guard(list_assets)),
        Route("/api/v1/assets/{asset_id}", guard(asset), methods=["GET", "DELETE"]),
        Route("/api/v1/tickets", guard(list_tickets), methods=["GET"]),
        Route("/api/v1/tickets", guard(create_ticket), methods=["POST"]),
        Route("/api/v1/tickets/{ticket_id}", guard(ticket)),
        Route("/api/v1/tickets/{ticket_id}/comments", guard(add_comment), methods=["POST"]),
    ])


def app():
    return create_app()
