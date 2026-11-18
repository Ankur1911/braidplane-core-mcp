"""Mocked-HTTP tests (no Core instance needed). The live check is scripts/live_check.py."""
import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
from mcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
BASE = "http://core.test/api"
ENV = "/organizations/acme/projects/etl/environments/prod"
NOW = datetime.now(timezone.utc)


def iso(delta: timedelta) -> str:
    return (NOW - delta).isoformat()


def run(slug, pipe, status, age_h, **extra):
    return {"run_slug": slug, "pipeline_slug": pipe, "status": status, "started_at": iso(timedelta(hours=age_h)),
            "ended_at": iso(timedelta(hours=age_h)), "duration_ms": 1500, "step_slug": "load", "error_summary": extra.pop("err", None), **extra}


@pytest.fixture
def mod(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", BASE)
    monkeypatch.setenv("API_TOKEN", "pat-secret")
    monkeypatch.setenv("MCP_ENV_FILE", "/nonexistent/.env")  # ignore a local .env
    monkeypatch.delenv("CORE_ENVIRONMENT", raising=False)
    monkeypatch.delenv("CORE_ORG", raising=False)
    monkeypatch.delenv("CORE_PROJECT", raising=False)
    for m in ("server", "mcp_runtime", "triage_tools"):
        sys.modules.pop(m, None)
    return importlib.import_module("server")


def text(r):
    return "".join(c.text for c in r.content)


async def call(mod, tool, args=None):
    async with Client(mod.mcp) as c:
        return await c.call_tool(tool, args or {})


SCOPE = {"organization": "acme", "project": "etl", "environment": "prod"}
FAILED_DETAIL = {"timeline": {"failure": {
    "step": "load", "error": "relation crm.customers does not exist", "reason_code": "source_query_failed",
    "kind": "data_source", "side": "source", "action_hint": "Check that the source table exists."}}}


async def test_tools_listed_read_only_and_token_not_an_argument(mod):
    async with Client(mod.mcp) as c:
        tools = (await c.list_tools()).tools
    names = {t.name for t in tools}
    assert len(tools) == 11 and {"triage_failed_runs", "find_stale_pipelines"} <= names
    assert all(t.annotations.read_only_hint for t in tools)
    assert all(o["method"] == "GET" for o in mod.OPERATIONS.values())
    for t in tools:
        props = {p.lower() for p in t.input_schema.get("properties", {})}
        assert not props & {"token", "api_token", "api_key", "authorization", "password", "secret"}


@respx.mock
async def test_triage_reports_failing_recovered_and_healthy(mod):
    runs = respx.get(f"{BASE}{ENV}/runs").mock(return_value=httpx.Response(200, json={"has_more": False, "runs": [
        run("cust-3", "cust", "failed", 3), run("cust-2", "cust", "failed", 27), run("cust-1", "cust", "succeeded", 51),
        run("ord-2", "orders", "succeeded", 2), run("ord-1", "orders", "failed", 26),
        run("inv-1", "inventory", "succeeded", 5)]}))
    respx.get(f"{BASE}{ENV}/runs/cust-3").mock(return_value=httpx.Response(200, json=FAILED_DETAIL))
    respx.get(f"{BASE}{ENV}/runs/cust-3/completion").mock(return_value=httpx.Response(
        200, json={"closed": False, "waiting_for_retry": False, "attempts": [{"attempt_number": 1}]}))
    out = text(await call(mod, "triage_failed_runs", SCOPE))
    assert runs.calls.last.request.headers["authorization"] == "Bearer pat-secret"
    assert runs.calls.last.request.url.params["limit"] == "100"
    assert "1 failing now, 1 recovered after a failure, 1 with no failures" in out
    assert "FAILING  cust" in out and "2 consecutive failed run(s); last success cust-1" in out
    assert "source_query_failed (data_source, source side)" in out
    assert "Check that the source table exists." in out
    assert "no retry is waiting" in out
    assert "RECOVERED  orders" in out
    assert "inventory" not in out.split("RECOVERED")[0].split("FAILING")[-1]
    assert "pat-secret" not in out


@respx.mock
async def test_triage_nothing_failed(mod):
    respx.get(f"{BASE}{ENV}/runs").mock(return_value=httpx.Response(200, json={"runs": [run("a-1", "a", "succeeded", 1)]}))
    out = text(await call(mod, "triage_failed_runs", SCOPE))
    assert "0 failing now" in out and "Nothing to triage" in out


@respx.mock
async def test_triage_pipeline_filter_is_passed_through(mod):
    r = respx.get(f"{BASE}{ENV}/runs").mock(return_value=httpx.Response(200, json={"runs": []}))
    await call(mod, "triage_failed_runs", {**SCOPE, "pipeline": "cust"})
    assert r.calls.last.request.url.params["pipeline_slug"] == "cust"


@respx.mock
async def test_triage_degrades_when_detail_call_fails(mod):
    respx.get(f"{BASE}{ENV}/runs").mock(return_value=httpx.Response(200, json={"runs": [run("c-1", "c", "failed", 1, err="boom")]}))
    respx.get(f"{BASE}{ENV}/runs/c-1").mock(return_value=httpx.Response(500, json={"error": {"code": "INTERNAL", "message": "x"}}))
    respx.get(f"{BASE}{ENV}/runs/c-1/completion").mock(return_value=httpx.Response(500))
    out = text(await call(mod, "triage_failed_runs", SCOPE))
    assert "FAILING  c" in out and "boom" in out and "details unavailable" in out


@respx.mock
async def test_triage_401_is_a_readable_error_without_the_token(mod):
    respx.get(f"{BASE}{ENV}/runs").mock(return_value=httpx.Response(
        401, json={"error": {"code": "AUTH_UNAUTHORIZED", "message": "Authentication required"}}))
    r = await call(mod, "triage_failed_runs", SCOPE)
    assert r.is_error
    msg = text(r)
    assert "HTTP 401" in msg and "API_TOKEN" in msg and "pat-secret" not in msg


@respx.mock
async def test_unreachable_core_is_a_readable_error(mod):
    respx.get(f"{BASE}{ENV}/runs").mock(side_effect=httpx.ConnectError("refused"))
    r = await call(mod, "triage_failed_runs", SCOPE)
    assert r.is_error and "could not connect" in text(r)


async def test_missing_scope_explains_how_to_set_it(mod):
    r = await call(mod, "triage_failed_runs", {})
    assert r.is_error and "CORE_ORG" in text(r)


@respx.mock
async def test_scope_defaults_come_from_environment(mod, monkeypatch):
    monkeypatch.setenv("CORE_ORG", "acme")
    monkeypatch.setenv("CORE_PROJECT", "etl")
    monkeypatch.setenv("CORE_ENVIRONMENT", "prod")
    r = respx.get(f"{BASE}{ENV}/runs").mock(return_value=httpx.Response(200, json={"runs": []}))
    out = text(await call(mod, "triage_failed_runs"))
    assert r.called and "acme/etl (environment prod)" in out


def recency(outcome, ok_h=None, max_h=24, configured=True):
    deadline = NOW - timedelta(hours=ok_h) + timedelta(hours=max_h) if ok_h is not None else None
    return {"configured": configured, "enabled": configured, "outcome": outcome, "maximum_age_seconds": max_h * 3600,
            "succeeded_at": iso(timedelta(hours=ok_h)) if ok_h is not None else None,
            "success_run_slug": "r-ok" if ok_h is not None else None,
            "deadline_at": deadline.isoformat() if deadline else None, "evaluated_at": NOW.isoformat(),
            "unavailable_reason": "no_deployment_evidence" if outcome == "unavailable" else None}


@respx.mock
async def test_stale_pipelines_classified(mod):
    deps = [{"pipeline_slug": p} for p in ("late", "fresh", "bare", "odd")]
    respx.get(f"{BASE}{ENV}/pipeline-deployments").mock(return_value=httpx.Response(200, json={"deployments": deps}))
    for slug, body in {"late": recency("breached", ok_h=100), "fresh": recency("healthy", ok_h=2),
                       "bare": recency("healthy", configured=False), "odd": recency("unavailable")}.items():
        respx.get(f"{BASE}{ENV}/pipelines/{slug}/successful-run-recency").mock(return_value=httpx.Response(200, json=body))
    lr = respx.get(f"{BASE}{ENV}/runs").mock(return_value=httpx.Response(200, json={"runs": [run("late-9", "late", "failed", 1)]}))
    out = text(await call(mod, "find_stale_pipelines", SCOPE))
    assert lr.calls.last.request.url.params["pipeline_slug"] == "late"
    assert "1 stale, 1 healthy, 1 without a recency expectation, 1 unavailable" in out
    assert "STALE  late" in out and "overdue by 3.2d" in out and "late-9 failed" in out and "triage_failed_runs" in out
    assert "HEALTHY  fresh" in out
    assert "NO EXPECTATION  bare" in out and "UNAVAILABLE  odd" in out and "no_deployment_evidence" in out


@respx.mock
async def test_stale_one_pipeline_error_does_not_hide_the_rest(mod):
    respx.get(f"{BASE}{ENV}/pipeline-deployments").mock(return_value=httpx.Response(
        200, json={"deployments": [{"pipeline_slug": "a"}, {"pipeline_slug": "b"}]}))
    respx.get(f"{BASE}{ENV}/pipelines/a/successful-run-recency").mock(return_value=httpx.Response(200, json=recency("healthy", ok_h=1)))
    respx.get(f"{BASE}{ENV}/pipelines/b/successful-run-recency").mock(return_value=httpx.Response(403, json={}))
    out = text(await call(mod, "find_stale_pipelines", SCOPE))
    assert "HEALTHY  a" in out and "UNAVAILABLE  b" in out and "HTTP 403" in out


@respx.mock
async def test_only_get_requests_are_ever_sent(mod):
    respx.get(f"{BASE}{ENV}/runs").mock(return_value=httpx.Response(200, json={"runs": [run("c-1", "c", "failed", 1)]}))
    respx.get(f"{BASE}{ENV}/runs/c-1").mock(return_value=httpx.Response(200, json=FAILED_DETAIL))
    respx.get(f"{BASE}{ENV}/runs/c-1/completion").mock(return_value=httpx.Response(200, json={"attempts": []}))
    respx.get(f"{BASE}{ENV}/pipeline-deployments").mock(return_value=httpx.Response(200, json={"deployments": []}))
    await call(mod, "triage_failed_runs", SCOPE)
    await call(mod, "find_stale_pipelines", SCOPE)
    assert respx.calls and all(c.request.method == "GET" for c in respx.calls)


def test_helpers():
    from triage_tools import _dur, analyse_runs
    assert [_dur(s) for s in (30, 600, 7200, 3 * 86400)] == ["30s", "10m", "2h", "3d"]
    a = analyse_runs([run("x-2", "x", "failed", 1), run("x-3", "x", "running", 0.1), run("x-1", "x", "succeeded", 5)])
    assert a["x"]["failed_streak"] == 1 and a["x"]["latest"]["run_slug"] == "x-2" and len(a["x"]["active"]) == 1
