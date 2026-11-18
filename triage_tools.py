"""Composed, read-only tools for the failed / stale run workflow.

Both tools only issue GET requests against Core's public REST API and return a
short, readable diagnosis instead of raw JSON. Authentication comes from the
API_TOKEN environment variable (see mcp_runtime.ApiClient); it is never a tool
argument. Organization / project / environment slugs are not secrets, so they can
be passed as arguments or defaulted through CORE_ORG, CORE_PROJECT and
CORE_ENVIRONMENT.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_runtime import ApiClient, _float_env

RUN_WINDOW = 100  # most recent runs inspected (Core's max page size)
MAX_FAILING_DETAILED = 5  # failing pipelines that get a drill-down
MAX_PIPELINES_CHECKED = 50
CONCURRENCY = 5

_client = ApiClient("http://127.0.0.1:3001/api", auth_mode="bearer")


# --------------------------------------------------------------------------- HTTP


class CoreReader:
    """Tiny GET-only JSON reader that shares ApiClient's base URL and bearer auth."""

    def __init__(self, client: ApiClient = _client):
        self.client = client
        self._sem = asyncio.Semaphore(CONCURRENCY)

    async def get(self, http: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = {"Accept": "application/json", "User-Agent": os.environ.get("API_USER_AGENT", "braidplane-core-mcp")}
        query: dict[str, Any] = {}
        self.client._auth(headers, query)  # bearer token from API_TOKEN
        base = self.client.base_url
        if not base:
            raise ToolError("No API base URL is configured. Set API_BASE_URL.")
        label = f"GET {path}"
        try:
            async with self._sem:
                resp = await http.get(base + path, params={**(params or {}), **query} or None, headers=headers)
        except httpx.TimeoutException as exc:
            raise ToolError(f"{label} timed out ({type(exc).__name__}). Retry, or raise API_TIMEOUT.") from exc
        except httpx.ConnectError as exc:
            raise ToolError(f"{label}: could not connect to {base}. Check API_BASE_URL and that Core is running.") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"{label} failed: {type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 400:
            hint = {
                401: " Check that API_TOKEN is set and valid for this organization.",
                403: " The token is not allowed to read this resource.",
                404: " Not found; check the organization, project and environment slugs.",
                429: " Rate limited by Core; retry shortly.",
            }.get(resp.status_code, "")
            code = ""
            try:
                err = resp.json().get("error", {})
                code = f" [{err.get('code', '')}] {err.get('message', '')}".rstrip()
            except (ValueError, AttributeError):
                pass
            raise ToolError(f"{label} returned HTTP {resp.status_code}.{code}{hint}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ToolError(f"{label} returned a non-JSON response.") from exc

    def http(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(_float_env("API_TIMEOUT", 30.0), connect=_float_env("API_CONNECT_TIMEOUT", 10.0))
        return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


# --------------------------------------------------------------------------- helpers


def _scope(org: str | None, project: str | None, env: str | None) -> tuple[str, str, str]:
    org = org or os.environ.get("CORE_ORG")
    project = project or os.environ.get("CORE_PROJECT")
    env = env or os.environ.get("CORE_ENVIRONMENT") or "dev"
    missing = [n for n, v in (("organization", org), ("project", project)) if not v]
    if missing:
        raise ToolError(
            f"Missing {' and '.join(missing)}. Pass it as an argument or set CORE_ORG / CORE_PROJECT. "
            "Slugs are not secrets; the API token is read from API_TOKEN only."
        )
    return org, project, env  # type: ignore[return-value]


def _env_path(org: str, project: str, env: str) -> str:
    q = lambda s: quote(s, safe="")  # noqa: E731
    return f"/organizations/{q(org)}/projects/{q(project)}/environments/{q(env)}"


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dur(seconds: float) -> str:
    s = int(abs(seconds))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{round(s / 60)}m"
    if s < 36 * 3600:
        return f"{round(s / 3600)}h"
    return f"{round(s / 86400, 1):g}d"


def _ago(ts: datetime | None, now: datetime) -> str:
    return "unknown time ago" if ts is None else f"{_dur((now - ts).total_seconds())} ago"


def _ms(ms: Any) -> str:
    return f"{ms / 1000:.1f}s" if isinstance(ms, (int, float)) else "?"


# --------------------------------------------------------------------------- triage logic


def analyse_runs(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group runs by pipeline and classify each pipeline from its newest finished run."""
    by_pipe: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        by_pipe.setdefault(r.get("pipeline_slug", "?"), []).append(r)
    out: dict[str, dict[str, Any]] = {}
    for pipe, rs in by_pipe.items():
        rs.sort(key=lambda r: _ts(r.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        finished = [r for r in rs if r.get("status") in ("succeeded", "failed", "canceled")]
        active = [r for r in rs if r.get("status") in ("queued", "running")]
        streak = 0
        for r in finished:
            if r["status"] != "failed":
                break
            streak += 1
        out[pipe] = {
            "runs": rs,
            "latest": finished[0] if finished else None,
            "active": active,
            "failed_streak": streak,
            "failed_in_window": sum(1 for r in finished if r["status"] == "failed"),
            "last_success": next((r for r in finished if r["status"] == "succeeded"), None),
        }
    return out


def render_triage(scope: str, runs_seen: int, has_more: bool, analysis: dict[str, dict[str, Any]],
                  details: dict[str, dict[str, Any]], now: datetime) -> str:
    failing = sorted((p for p, a in analysis.items() if a["latest"] and a["latest"]["status"] == "failed"),
                     key=lambda p: _ts(analysis[p]["latest"].get("started_at")) or now, reverse=True)
    recovered = [p for p, a in analysis.items() if a["failed_in_window"] and p not in failing]
    healthy = len(analysis) - len(failing) - len(recovered)
    lines = [f"Failed-run triage for {scope}",
             f"Inspected the {runs_seen} most recent runs across {len(analysis)} pipeline(s)"
             + (" (more runs exist beyond this window)." if has_more else "."),
             f"{len(failing)} failing now, {len(recovered)} recovered after a failure, {healthy} with no failures in the window."]
    if not failing and not recovered:
        lines.append("Nothing to triage: no failed runs in the window.")
    for p in failing:
        a = analysis[p]
        latest = a["latest"]
        d = details.get(p, {})
        fail = d.get("failure") or {}
        lines += ["", f"FAILING  {p}",
                  f"  Latest run   {latest.get('run_slug')} failed {_ago(_ts(latest.get('started_at')), now)}"
                  f" at step '{fail.get('step') or latest.get('step_slug', '?')}' after {_ms(latest.get('duration_ms'))}"]
        streak = f"{a['failed_streak']} consecutive failed run(s)"
        ls = a["last_success"]
        streak += f"; last success {ls.get('run_slug')} {_ago(_ts(ls.get('ended_at') or ls.get('started_at')), now)}" if ls \
            else "; no success in the inspected window"
        lines.append(f"  Streak       {streak}")
        if fail:
            kind = ", ".join(x for x in (fail.get("kind"), (fail.get("side") + " side") if fail.get("side") else None) if x)
            lines.append(f"  Cause        {fail.get('reason_code') or 'unknown'}" + (f" ({kind})" if kind else ""))
            lines.append(f"  Error        {fail.get('error') or latest.get('error_summary') or 'n/a'}")
            if fail.get("action_hint"):
                lines.append(f"  Suggested    {fail['action_hint']}")
            flags = [t for t, f in (("outcome uncertain: inspect the destination before re-running", fail.get("outcome_uncertain")),
                                    ("automatic retry prohibited", fail.get("automatic_retry_prohibited"))) if f]
            if flags:
                lines.append(f"  Caution      {'; '.join(flags)}")
        else:
            lines.append(f"  Error        {latest.get('error_summary') or 'n/a'}"
                         + (f"   (details unavailable: {d['detail_error']})" if d.get("detail_error") else ""))
        comp = d.get("completion")
        if comp:
            state = "an automatic retry is queued or running" if comp.get("waiting_for_retry") else (
                "closed, no retry pending" if comp.get("closed") else "no retry is waiting; re-run manually after fixing the cause")
            lines.append(f"  Retry state  {state} ({len(comp.get('attempts') or [])} attempt(s) in this run chain)")
        if a["active"]:
            lines.append(f"  Note         {len(a['active'])} newer run(s) queued or running")
        lines.append(f"  Drill down   getPipelineRun / getPipelineRunProgress with run_slug={latest.get('run_slug')}")
    if len(failing) > MAX_FAILING_DETAILED:
        lines += ["", f"{len(failing) - MAX_FAILING_DETAILED} more failing pipeline(s) listed without details: "
                  + ", ".join(failing[MAX_FAILING_DETAILED:])]
    for p in recovered:
        a = analysis[p]
        lines.append("")
        lines.append(f"RECOVERED  {p}: {a['failed_in_window']} failed run(s) in the window, latest run "
                     f"{a['latest']['run_slug']} {a['latest']['status']} {_ago(_ts(a['latest'].get('started_at')), now)}")
    return "\n".join(lines)


def classify_recency(r: dict[str, Any]) -> str:
    if not r.get("configured") or not r.get("enabled"):
        return "no_expectation"
    return r.get("outcome") or "unavailable"


def render_stale(scope: str, rows: list[dict[str, Any]], skipped: int, now: datetime) -> str:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["state"], []).append(row)
    lines = [f"Stale-pipeline check for {scope}",
             f"Checked {len(rows)} deployed pipeline(s): "
             + ", ".join(f"{len(groups.get(k, []))} {label}" for k, label in
                         (("breached", "stale"), ("grace", "in grace period"), ("healthy", "healthy"),
                          ("no_expectation", "without a recency expectation"), ("unavailable", "unavailable")) if groups.get(k))
             + "."]
    if skipped:
        lines.append(f"{skipped} further pipeline(s) were not checked (limit {MAX_PIPELINES_CHECKED}).")
    stale = sorted(groups.get("breached", []), key=lambda r: r["overdue_s"], reverse=True)
    for r in stale:
        lines += ["", f"STALE  {r['pipeline']}",
                  f"  Last success  {r['success_run'] or 'none recorded'}" + (f" {_ago(r['succeeded_at'], now)}" if r["succeeded_at"] else ""),
                  f"  Expectation   a successful run at least every {_dur(r['max_age'])}; deadline passed {_ago(r['deadline'], now)}"
                  f" (overdue by {_dur(r['overdue_s'])})",
                  f"  Latest run    {r['latest_run']}",
                  "  Next step     " + ("run triage_failed_runs for the cause" if r.get("latest_failed")
                                        else f"no failure to blame: check why {r['pipeline']} is not being triggered or finishing")]
    for key, title in (("grace", "IN GRACE"), ("unavailable", "UNAVAILABLE"), ("no_expectation", "NO EXPECTATION")):
        for r in groups.get(key, []):
            extra = {"grace": "recently deployed; first success not yet due",
                     "unavailable": f"state not calculable ({r.get('reason') or 'no reason given'})",
                     "no_expectation": "no enabled successful-run recency expectation, so staleness cannot be judged"}[key]
            lines.append(f"{title}  {r['pipeline']}: {extra}")
    healthy = groups.get("healthy", [])
    if healthy:
        lines.append("")
        lines.append("HEALTHY  " + "; ".join(
            f"{r['pipeline']} (last success {_ago(r['succeeded_at'], now)}, allowed {_dur(r['max_age'])})" for r in healthy))
    if not stale:
        lines.append("No stale pipelines.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- gathering


async def _failure_details(reader: CoreReader, http: httpx.AsyncClient, base: str, run_slug: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    run, comp = await asyncio.gather(
        reader.get(http, f"{base}/runs/{quote(run_slug, safe='')}"),
        reader.get(http, f"{base}/runs/{quote(run_slug, safe='')}/completion"),
        return_exceptions=True,
    )
    if isinstance(run, ToolError):
        out["detail_error"] = str(run)
    elif isinstance(run, dict):
        out["failure"] = (run.get("timeline") or {}).get("failure")
    if isinstance(comp, dict):
        out["completion"] = comp
    return out


def register(mcp: Any) -> None:
    ro = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True)

    @mcp.tool(
        name="triage_failed_runs",
        description=(
            "Diagnose failing pipelines in one project environment. Reads the most recent runs, finds pipelines whose latest "
            "finished run failed, and for each reports the failing step, Core's failure reason code and suggested action, the "
            "failure streak, the last success and the retry state. Also lists pipelines that failed but have recovered. "
            "Read-only; the token comes from API_TOKEN, not from arguments. Organization, project and environment fall back to "
            "CORE_ORG, CORE_PROJECT and CORE_ENVIRONMENT (default 'dev')."
        ),
        annotations=ro,
        structured_output=False,
    )
    async def triage_failed_runs(
        organization: Annotated[str | None, Field(description="Organization slug (default: CORE_ORG)")] = None,
        project: Annotated[str | None, Field(description="Project slug (default: CORE_PROJECT)")] = None,
        environment: Annotated[str | None, Field(description="Environment slug (default: CORE_ENVIRONMENT or 'dev')")] = None,
        pipeline: Annotated[str | None, Field(description="Only inspect this pipeline slug")] = None,
    ) -> str:
        org, proj, env = _scope(organization, project, environment)
        base = _env_path(org, proj, env)
        reader = CoreReader()
        async with reader.http() as http:
            params: dict[str, Any] = {"limit": RUN_WINDOW}
            if pipeline:
                params["pipeline_slug"] = pipeline
            data = await reader.get(http, f"{base}/runs", params)
            runs = data.get("runs") or []
            analysis = analyse_runs(runs)
            failing = sorted((p for p, a in analysis.items() if a["latest"] and a["latest"]["status"] == "failed"),
                             key=lambda p: _ts(analysis[p]["latest"].get("started_at")) or datetime.min.replace(tzinfo=timezone.utc),
                             reverse=True)[:MAX_FAILING_DETAILED]
            fetched = await asyncio.gather(*(_failure_details(reader, http, base, analysis[p]["latest"]["run_slug"]) for p in failing))
        return render_triage(f"{org}/{proj} (environment {env})", len(runs), bool(data.get("has_more")), analysis,
                             dict(zip(failing, fetched)), datetime.now(timezone.utc))

    @mcp.tool(
        name="find_stale_pipelines",
        description=(
            "Find deployed pipelines that have not had a successful run recently. For every pipeline deployed to the environment "
            "it reads Core's successful-run recency state and reports which are stale (deadline passed), how overdue, when the last "
            "success was, and which have no recency expectation configured. Read-only; the token comes from API_TOKEN, not from "
            "arguments. Organization, project and environment fall back to CORE_ORG, CORE_PROJECT and CORE_ENVIRONMENT (default 'dev')."
        ),
        annotations=ro,
        structured_output=False,
    )
    async def find_stale_pipelines(
        organization: Annotated[str | None, Field(description="Organization slug (default: CORE_ORG)")] = None,
        project: Annotated[str | None, Field(description="Project slug (default: CORE_PROJECT)")] = None,
        environment: Annotated[str | None, Field(description="Environment slug (default: CORE_ENVIRONMENT or 'dev')")] = None,
    ) -> str:
        org, proj, env = _scope(organization, project, environment)
        base = _env_path(org, proj, env)
        reader = CoreReader()
        async with reader.http() as http:
            deps = (await reader.get(http, f"{base}/pipeline-deployments")).get("deployments") or []
            slugs = sorted({d["pipeline_slug"] for d in deps if d.get("pipeline_slug")})
            skipped = max(0, len(slugs) - MAX_PIPELINES_CHECKED)
            slugs = slugs[:MAX_PIPELINES_CHECKED]
            results = await asyncio.gather(
                *(reader.get(http, f"{base}/pipelines/{quote(s, safe='')}/successful-run-recency") for s in slugs),
                return_exceptions=True)
        rows = []
        stale_slugs = [s for s, r in zip(slugs, results)
                       if isinstance(r, dict) and classify_recency(r) == "breached"]
        latest_runs: dict[str, Any] = {}
        if stale_slugs:
            async with reader.http() as http:
                got = await asyncio.gather(
                    *(reader.get(http, f"{base}/runs", {"pipeline_slug": s, "limit": 1}) for s in stale_slugs),
                    return_exceptions=True)
            latest_runs = {s: (g.get("runs") or [None])[0] if isinstance(g, dict) else None for s, g in zip(stale_slugs, got)}
        now = datetime.now(timezone.utc)
        for s, r in zip(slugs, results):
            if isinstance(r, Exception):
                rows.append({"pipeline": s, "state": "unavailable", "reason": str(r), "overdue_s": 0})
                continue
            evaluated = _ts(r.get("evaluated_at")) or datetime.now(timezone.utc)
            deadline = _ts(r.get("deadline_at"))
            lr = latest_runs.get(s)
            latest = (f"{lr.get('run_slug')} {lr.get('status')} {_ago(_ts(lr.get('started_at')), now)}" if lr
                      else "no runs found for this pipeline")
            rows.append({
                "latest_run": latest, "latest_failed": bool(lr and lr.get("status") == "failed"),
                "pipeline": s, "state": classify_recency(r), "reason": r.get("unavailable_reason"),
                "success_run": r.get("success_run_slug"), "succeeded_at": _ts(r.get("succeeded_at")),
                "deadline": deadline, "max_age": r.get("maximum_age_seconds") or 0,
                "overdue_s": (evaluated - deadline).total_seconds() if deadline else 0,
            })
        return render_stale(f"{org}/{proj} (environment {env})", rows, skipped, datetime.now(timezone.utc))
