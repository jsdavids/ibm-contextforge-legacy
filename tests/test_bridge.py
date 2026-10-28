import httpx
import jwt
import pytest
from starlette.testclient import TestClient

from legacybridge.cli import unwrap_a2a
from legacybridge.contextforge import _service_password, mint_token
from legacybridge.legacy_api import OPENAPI, create_app
from legacybridge.openapi_tools import kebab, openapi_to_tools
from legacybridge.triage import assess, ticket_id_from

EXPOSE = {"listAssets": "List", "getAsset": "Get", "createTicket": "Create", "addComment": "Comment"}


@pytest.fixture
def legacy():
    return TestClient(create_app("k" * 20))


def test_legacy_api_requires_key(legacy):
    assert legacy.get("/api/v1/assets").status_code == 401
    assert legacy.get("/api/v1/assets", headers={"x-api-key": "wrong"}).status_code == 401
    r = legacy.get("/api/v1/assets", params={"status": "retired"}, headers={"x-api-key": "k" * 20})
    assert [a["asset_id"] for a in r.json()["assets"]] == ["SRV-0999"]


def test_legacy_api_ticket_flow(legacy):
    h = {"x-api-key": "k" * 20}
    created = legacy.post("/api/v1/tickets", json={"asset_id": "SRV-1002", "summary": "disk full", "priority": "P2"}, headers=h)
    assert created.status_code == 201
    tid = created.json()["ticket_id"]
    assert legacy.post(f"/api/v1/tickets/{tid}/comments", json={"text": "on it"}, headers=h).status_code == 201
    assert legacy.get(f"/api/v1/tickets/{tid}", headers=h).json()["comments"][0]["text"] == "on it"
    assert legacy.post("/api/v1/tickets", json={"asset_id": "NOPE", "summary": "x"}, headers=h).status_code == 422
    assert legacy.post("/api/v1/tickets", json={"asset_id": "SRV-1002", "summary": "x", "priority": "P9"}, headers=h).status_code == 422


def test_openapi_to_tools_allowlist_and_secrets():
    tools = openapi_to_tools(OPENAPI, "http://legacy:9300", EXPOSE, api_key="secret-key")
    by_name = {t["name"]: t for t in tools}
    assert set(by_name) == {"legacy-list-assets", "legacy-get-asset", "legacy-create-ticket", "legacy-add-comment"}
    assert "legacy-decommission-asset" not in by_name  # DELETE never becomes a tool
    get = by_name["legacy-get-asset"]
    assert get["url"] == "http://legacy:9300/api/v1/assets/{asset_id}" and get["request_type"] == "GET"
    assert get["input_schema"]["required"] == ["asset_id"]
    comment = by_name["legacy-add-comment"]["input_schema"]
    assert set(comment["properties"]) == {"ticket_id", "text", "author"} and comment["required"] == ["ticket_id", "text"]
    for t in tools:  # key travels only as an encrypted auth header, never in plain headers
        assert "headers" not in t and t["auth_headers"] == [{"key": "X-API-Key", "value": "secret-key"}]
    with pytest.raises(KeyError):
        openapi_to_tools(OPENAPI, "http://x", {"noSuchOp": "?"}, api_key="k")


def test_kebab():
    assert kebab("listAssets") == "list-assets" and kebab("getTicket") == "get-ticket"


def test_tokens_match_contextforge_shape():
    secret = "s" * 48
    claims = jwt.decode(mint_token(secret, "triage-agent@example.com", server_id="abc"), secret,
                        algorithms=["HS256"], audience="mcpgateway-api", issuer="mcpgateway")
    assert claims["user"]["is_admin"] is False and claims["teams"] == []
    assert claims["scopes"]["server_id"] == "abc"
    admin = jwt.decode(mint_token(secret, "admin@example.com", admin=True), secret, algorithms=["HS256"],
                       audience="mcpgateway-api")
    assert admin["user"]["is_admin"] is True and admin["teams"] is None


def test_service_password_passes_policy():
    for _ in range(50):
        pw = _service_password()
        assert len(pw) == 28 and not any(a == b for a, b in zip(pw, pw[1:]))


def test_triage_scoring():
    ticket = {"ticket_id": "INC-1", "priority": "P2"}
    risky = assess(ticket, {"os": "RHEL 7.9", "owner": "finance-it", "status": "active"},
                   [ticket, {"ticket_id": "INC-2", "status": "open"}])
    assert risky["severity"] == "high" and risky["recommended_priority"] == "P1" and len(risky["reasons"]) == 3
    calm = assess({"ticket_id": "INC-3", "priority": "P4"}, {"os": "RHEL 9.4", "owner": "web", "status": "active"}, [])
    assert calm["severity"] == "low" and calm["recommended_priority"] == "P4"


def test_ticket_id_from_text_or_json():
    assert ticket_id_from({"ticket_id": "INC-9"}) == "INC-9"
    assert ticket_id_from({"text": "please triage inc-5001 asap"}) == "INC-5001"
    with pytest.raises(ValueError):
        ticket_id_from({"text": "hello"})


def test_unwrap_a2a_result():
    raw = {"result": {"task": {"artifacts": [{"parts": [{"text": '{"severity": "high"}', "mediaType": "application/json"}]}]}}}
    assert unwrap_a2a(raw) == {"severity": "high"}
    assert unwrap_a2a("plain") == "plain"


def test_triage_reads_registration_file(tmp_path, monkeypatch):
    from legacybridge.triage import contextforge_access

    f = tmp_path / "tokens.json"
    f.write_text('{"mcp_url": "http://cf/servers/1/mcp", "tokens": {"triage-agent": "t1", "analyst": "t2"}}')
    monkeypatch.delenv("CONTEXTFORGE_MCP_URL", raising=False)
    monkeypatch.setenv("LEGACYBRIDGE_TOKENS", str(f))
    assert contextforge_access() == ("http://cf/servers/1/mcp", "t1")
    monkeypatch.setenv("CONTEXTFORGE_MCP_URL", "http://other/mcp")
    monkeypatch.setenv("CONTEXTFORGE_TOKEN", "env-token")
    assert contextforge_access() == ("http://other/mcp", "env-token")
