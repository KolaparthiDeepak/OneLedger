"""MCP server end-to-end: tools forward to the API with a read-only token and return provenance."""

from __future__ import annotations

import asyncio
import json

from oneledger_mcp.server import OneLedgerClient, build_server

from .helpers import account, csv_bytes, import_csv


class _TestClientTransport(OneLedgerClient):
    def __init__(self, test_client, token):  # type: ignore[no-untyped-def]
        self._c, self._h = test_client, {"Authorization": f"Bearer {token}"}

    def call(self, name, args):  # type: ignore[no-untyped-def]
        r = self._c.post(
            f"/api/v1/tools/{name}", json={k: v for k, v in args.items() if v is not None}, headers=self._h
        )
        return r.text


def test_mcp_tools_list_and_call(api, client):
    acct = account(api, "HDFC", opening_date="2026-07-31", opening_balance="1000.00")
    import_csv(api, acct, csv_bytes(["02/08/2026,SWIGGY,,300.00,,700.00"]))
    token = api.ok(api.post("/tokens", json={"label": "mcp"}), 201)["token"]
    server = build_server(_TestClientTransport(client, token))

    async def run():  # type: ignore[no-untyped-def]
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert {"get_accounts", "get_transactions", "get_net_worth", "find_anomalies", "get_financial_goals"} <= names
        assert {"get_upcoming_bills", "get_safe_to_spend", "get_shared_balances"} <= names
        r = await server.call_tool("get_safe_to_spend", {})
        safe = json.loads(r[0][0].text if isinstance(r, tuple) else r.content[0].text)
        assert safe["available"] and safe["metrics"]
        assert all(t.annotations and t.annotations.read_only_hint for t in tools)
        result = await server.call_tool("get_spending_by_category", {"period": "2026-08"})
        return result

    result = asyncio.run(run())
    text = result[0][0].text if isinstance(result, tuple) else result.content[0].text
    data = json.loads(text)
    assert data["data"]["total"] == "300.00"
    assert data["provenance"]["query_id"]


def test_mcp_rejects_session_tokens():
    import pytest

    with pytest.raises(SystemExit):
        OneLedgerClient("http://x", "ols_session")
