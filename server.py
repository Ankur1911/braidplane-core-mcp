"""Read-only MCP server for a self-hosted Braidplane Core.

Nine tools are generated from Core's public api-schema/schema.yml (GET only);
two composed tools (triage_failed_runs, find_stale_pipelines) live in triage_tools.py.
Run with:  uv run server.py
Configuration comes from environment variables or a .env file next to this file
(see .env.example).
"""

import os
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_runtime import ApiClient, load_env_file, setup_logging
import triage_tools

load_env_file(Path(os.environ.get("MCP_ENV_FILE") or Path(__file__).with_name(".env")))

SERVER_NAME = 'braidplane-core'
DEFAULT_BASE_URL = 'http://127.0.0.1:3001/api'

OPERATIONS = {
    'listOrganizations': {'method': 'GET', 'path': '/organizations', 'params': [{'name': 'limit', 'py': 'limit', 'in': 'query', 'explode': True}, {'name': 'cursor', 'py': 'cursor', 'in': 'query', 'explode': True}, {'name': 'sort_by', 'py': 'sort_by', 'in': 'query', 'explode': True}, {'name': 'order', 'py': 'order', 'in': 'query', 'explode': True}, {'name': 'direction', 'py': 'direction', 'in': 'query', 'explode': True}], 'body': None},
    'listProjects': {'method': 'GET', 'path': '/organizations/{slug}/projects', 'params': [{'name': 'slug', 'py': 'slug', 'in': 'path', 'explode': True}, {'name': 'limit', 'py': 'limit', 'in': 'query', 'explode': True}, {'name': 'cursor', 'py': 'cursor', 'in': 'query', 'explode': True}, {'name': 'sort_by', 'py': 'sort_by', 'in': 'query', 'explode': True}, {'name': 'order', 'py': 'order', 'in': 'query', 'explode': True}, {'name': 'direction', 'py': 'direction', 'in': 'query', 'explode': True}], 'body': None},
    'listPipelines': {'method': 'GET', 'path': '/organizations/{slug}/projects/{project_slug}/pipelines', 'params': [{'name': 'slug', 'py': 'slug', 'in': 'path', 'explode': True}, {'name': 'project_slug', 'py': 'project_slug', 'in': 'path', 'explode': True}, {'name': 'limit', 'py': 'limit', 'in': 'query', 'explode': True}, {'name': 'cursor', 'py': 'cursor', 'in': 'query', 'explode': True}, {'name': 'direction', 'py': 'direction', 'in': 'query', 'explode': True}], 'body': None},
    'listPipelineDeployments': {'method': 'GET', 'path': '/organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/pipeline-deployments', 'params': [{'name': 'slug', 'py': 'slug', 'in': 'path', 'explode': True}, {'name': 'project_slug', 'py': 'project_slug', 'in': 'path', 'explode': True}, {'name': 'environment_slug', 'py': 'environment_slug', 'in': 'path', 'explode': True}], 'body': None},
    'getSuccessfulRunRecency': {'method': 'GET', 'path': '/organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/pipelines/{pipeline_slug}/successful-run-recency', 'params': [{'name': 'slug', 'py': 'slug', 'in': 'path', 'explode': True}, {'name': 'project_slug', 'py': 'project_slug', 'in': 'path', 'explode': True}, {'name': 'environment_slug', 'py': 'environment_slug', 'in': 'path', 'explode': True}, {'name': 'pipeline_slug', 'py': 'pipeline_slug', 'in': 'path', 'explode': True}], 'body': None},
    'listPipelineRuns': {'method': 'GET', 'path': '/organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/runs', 'params': [{'name': 'slug', 'py': 'slug', 'in': 'path', 'explode': True}, {'name': 'project_slug', 'py': 'project_slug', 'in': 'path', 'explode': True}, {'name': 'environment_slug', 'py': 'environment_slug', 'in': 'path', 'explode': True}, {'name': 'limit', 'py': 'limit', 'in': 'query', 'explode': True}, {'name': 'cursor', 'py': 'cursor', 'in': 'query', 'explode': True}, {'name': 'sort_by', 'py': 'sort_by', 'in': 'query', 'explode': True}, {'name': 'order', 'py': 'order', 'in': 'query', 'explode': True}, {'name': 'direction', 'py': 'direction', 'in': 'query', 'explode': True}, {'name': 'status', 'py': 'status', 'in': 'query', 'explode': True}, {'name': 'pipeline_slug', 'py': 'pipeline_slug', 'in': 'query', 'explode': True}, {'name': 'mode', 'py': 'mode', 'in': 'query', 'explode': True}, {'name': 'backfill_series_slug', 'py': 'backfill_series_slug', 'in': 'query', 'explode': True}], 'body': None},
    'getPipelineRun': {'method': 'GET', 'path': '/organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/runs/{run_slug}', 'params': [{'name': 'slug', 'py': 'slug', 'in': 'path', 'explode': True}, {'name': 'project_slug', 'py': 'project_slug', 'in': 'path', 'explode': True}, {'name': 'environment_slug', 'py': 'environment_slug', 'in': 'path', 'explode': True}, {'name': 'run_slug', 'py': 'run_slug', 'in': 'path', 'explode': True}], 'body': None},
    'getPipelineRunProgress': {'method': 'GET', 'path': '/organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/runs/{run_slug}/progress', 'params': [{'name': 'slug', 'py': 'slug', 'in': 'path', 'explode': True}, {'name': 'project_slug', 'py': 'project_slug', 'in': 'path', 'explode': True}, {'name': 'environment_slug', 'py': 'environment_slug', 'in': 'path', 'explode': True}, {'name': 'run_slug', 'py': 'run_slug', 'in': 'path', 'explode': True}, {'name': 'after_event', 'py': 'after_event', 'in': 'query', 'explode': True}, {'name': 'limit', 'py': 'limit', 'in': 'query', 'explode': True}], 'body': None},
    'getPipelineRunCompletion': {'method': 'GET', 'path': '/organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/runs/{run_slug}/completion', 'params': [{'name': 'slug', 'py': 'slug', 'in': 'path', 'explode': True}, {'name': 'project_slug', 'py': 'project_slug', 'in': 'path', 'explode': True}, {'name': 'environment_slug', 'py': 'environment_slug', 'in': 'path', 'explode': True}, {'name': 'run_slug', 'py': 'run_slug', 'in': 'path', 'explode': True}], 'body': None},
}

mcp = MCPServer(SERVER_NAME, instructions='Read-only tools for a self-hosted Braidplane Core. Start with triage_failed_runs (why did runs fail) or find_stale_pipelines (which pipelines have not succeeded recently); the generated list*/get* tools give raw API responses for drill-down. Responses may be truncated; narrow requests with filters when you see a truncation notice.')
api = ApiClient(DEFAULT_BASE_URL, auth_mode='bearer', key_name='X-API-Key', key_in='header')

@mcp.tool(
    name='listOrganizations',
    description="List user's organizations\n\nLists all organizations the authenticated user is a member of.\n\nCalls GET /organizations.",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True),
    structured_output=False,
)
async def tool_001_listOrganizations(
    limit: Annotated[int | None, Field(description='Maximum number of items to return (default 20, max 100) (default: 20)')] = None,
    cursor: Annotated[str | None, Field(description="Cursor for pagination (from previous response's next_cursor). Format depends on sort_by parameter.")] = None,
    sort_by: Annotated[Literal['organization_created_at', 'organization_updated_at', 'organization_name'] | None, Field(description='Field to sort by (default: organization_created_at)')] = None,
    order: Annotated[Literal['asc', 'desc'] | None, Field(description='Sort order (asc for ascending, desc for descending) (default: desc)')] = None,
    direction: Annotated[Literal['next', 'prev'] | None, Field(description='Page direction (next for forward navigation, prev for backward navigation) (default: next)')] = None
) -> str:
    return await api.call(OPERATIONS['listOrganizations'], {"limit": limit, "cursor": cursor, "sort_by": sort_by, "order": order, "direction": direction})


@mcp.tool(
    name='listProjects',
    description='List projects\n\nLists projects in the organization.\n\nCalls GET /organizations/{slug}/projects.',
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True),
    structured_output=False,
)
async def tool_002_listProjects(
    slug: Annotated[str, Field(description='Organization slug')],
    limit: Annotated[int | None, Field(description='Maximum number of items to return (default 20, max 100) (default: 20)')] = None,
    cursor: Annotated[str | None, Field(description="Cursor for pagination (from previous response's next_cursor). Format depends on sort_by parameter.")] = None,
    sort_by: Annotated[Literal['project_created_at', 'project_updated_at', 'project_name'] | None, Field(description='Field to sort by (default: project_created_at)')] = None,
    order: Annotated[Literal['asc', 'desc'] | None, Field(description='Sort order (asc for ascending, desc for descending) (default: desc)')] = None,
    direction: Annotated[Literal['next', 'prev'] | None, Field(description='Page direction (next for forward navigation, prev for backward navigation) (default: next)')] = None
) -> str:
    return await api.call(OPERATIONS['listProjects'], {"slug": slug, "limit": limit, "cursor": cursor, "sort_by": sort_by, "order": order, "direction": direction})


@mcp.tool(
    name='listPipelines',
    description='List pipelines\n\nLists persisted pipelines for a project.\n\nCalls GET /organizations/{slug}/projects/{project_slug}/pipelines.',
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True),
    structured_output=False,
)
async def tool_003_listPipelines(
    slug: Annotated[str, Field(description='Organization slug')],
    project_slug: Annotated[str, Field(description='Slug of the project')],
    limit: Annotated[int | None, Field(description='Maximum number of items to return (default 20, max 100) (default: 20)')] = None,
    cursor: Annotated[str | None, Field(description="Cursor for pagination (from previous response's next_cursor). Format depends on sort_by parameter.")] = None,
    direction: Annotated[Literal['next', 'prev'] | None, Field(description='Page direction (next for forward navigation, prev for backward navigation) (default: next)')] = None
) -> str:
    return await api.call(OPERATIONS['listPipelines'], {"slug": slug, "project_slug": project_slug, "limit": limit, "cursor": cursor, "direction": direction})


@mcp.tool(
    name='listPipelineDeployments',
    description='List pipeline deployments\n\nLists deployed pipeline version pointers for one project environment.\n\nCalls GET /organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/pipeline-deployments.',
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True),
    structured_output=False,
)
async def tool_004_listPipelineDeployments(
    slug: Annotated[str, Field(description='Organization slug')],
    project_slug: Annotated[str, Field(description='Slug of the project')],
    environment_slug: Annotated[str, Field(description='Slug of the environment')]
) -> str:
    return await api.call(OPERATIONS['listPipelineDeployments'], {"slug": slug, "project_slug": project_slug, "environment_slug": environment_slug})


@mcp.tool(
    name='getSuccessfulRunRecency',
    description='Inspect successful-run recency\n\nRead current calculated state and the latest 20 immutable breach/recovery episodes, including disabled expectations. This is successful-run recency, not payload data freshness or an SLA.\n\nCalls GET /organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/pipelines/{pipeline_slug}/successful-run-recency.',
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True),
    structured_output=False,
)
async def tool_005_getSuccessfulRunRecency(
    slug: Annotated[str, Field(description='Organization slug')],
    project_slug: Annotated[str, Field(description='Slug of the project')],
    environment_slug: Annotated[str, Field(description='Slug of the environment')],
    pipeline_slug: Annotated[str, Field(description='Slug of the pipeline')]
) -> str:
    return await api.call(OPERATIONS['getSuccessfulRunRecency'], {"slug": slug, "project_slug": project_slug, "environment_slug": environment_slug, "pipeline_slug": pipeline_slug})


@mcp.tool(
    name='listPipelineRuns',
    description='List pipeline runs\n\nList persisted pipeline runs for one project environment with optional filters\n\nCalls GET /organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/runs.',
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True),
    structured_output=False,
)
async def tool_006_listPipelineRuns(
    slug: Annotated[str, Field(description='Organization slug')],
    project_slug: Annotated[str, Field(description='Slug of the project')],
    environment_slug: Annotated[str, Field(description='Slug of the environment')],
    limit: Annotated[int | None, Field(description='Maximum number of items to return (default 20, max 100) (default: 20)')] = None,
    cursor: Annotated[str | None, Field(description="Cursor for pagination (from previous response's next_cursor). Format depends on sort_by parameter.")] = None,
    sort_by: Annotated[Literal['run_created_at'] | None, Field(description='Field to sort by (default: run_created_at)')] = None,
    order: Annotated[Literal['asc', 'desc'] | None, Field(description='Sort order (asc for ascending, desc for descending) (default: desc)')] = None,
    direction: Annotated[Literal['next', 'prev'] | None, Field(description='Page direction (next for forward navigation, prev for backward navigation) (default: next)')] = None,
    status: Annotated[Literal['queued', 'running', 'succeeded', 'failed', 'canceled'] | None, Field(description='Filter by run status')] = None,
    pipeline_slug: Annotated[str | None, Field(description='Filter by pipeline slug')] = None,
    mode: Annotated[Literal['full', 'incremental', 'backfill'] | None, Field(description='Filter by run execution mode')] = None,
    backfill_series_slug: Annotated[str | None, Field(description='Filter by backfill series slug')] = None
) -> str:
    return await api.call(OPERATIONS['listPipelineRuns'], {"slug": slug, "project_slug": project_slug, "environment_slug": environment_slug, "limit": limit, "cursor": cursor, "sort_by": sort_by, "order": order, "direction": direction, "status": status, "pipeline_slug": pipeline_slug, "mode": mode, "backfill_series_slug": backfill_series_slug})


@mcp.tool(
    name='getPipelineRun',
    description='Get pipeline run details\n\nRetrieve run metadata and diagnostics timeline for a persisted environment-scoped pipeline run\n\nCalls GET /organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/runs/{run_slug}.',
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True),
    structured_output=False,
)
async def tool_007_getPipelineRun(
    slug: Annotated[str, Field(description='Organization slug')],
    project_slug: Annotated[str, Field(description='Slug of the project')],
    environment_slug: Annotated[str, Field(description='Slug of the environment')],
    run_slug: Annotated[str, Field(description='Run slug of the pipeline execution')]
) -> str:
    return await api.call(OPERATIONS['getPipelineRun'], {"slug": slug, "project_slug": project_slug, "environment_slug": environment_slug, "run_slug": run_slug})


@mcp.tool(
    name='getPipelineRunProgress',
    description='Read durable pipeline execution progress\n\nRead an append-only event page for one exact attempt. after_event is the number of events already consumed; start or resynchronize with zero. A cursor beyond retained history returns 400. Reconnect with the last next_after_event; duplicate observations are safe to discard by offset. Revision orders boundary snapshots; worker observations can advance independently. Poll no faster than once per second, normally every two seconds, and stop after a terminal attempt and all pages are consumed. Recent worker telemetry is not evidence of completed provider effects. Stale or unavailable observations do not prove worker death. Retention is the same as run history; a missing run returns 404.\n\nCalls GET /organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/runs/{run_slug}/progress.',
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True),
    structured_output=False,
)
async def tool_008_getPipelineRunProgress(
    slug: Annotated[str, Field(description='Organization slug')],
    project_slug: Annotated[str, Field(description='Slug of the project')],
    environment_slug: Annotated[str, Field(description='Slug of the environment')],
    run_slug: Annotated[str, Field(description='Run slug of the pipeline execution')],
    after_event: Annotated[int | None, Field(description='(default: 0)')] = None,
    limit: Annotated[int | None, Field(description='(default: 100)')] = None
) -> str:
    return await api.call(OPERATIONS['getPipelineRunProgress'], {"slug": slug, "project_slug": project_slug, "environment_slug": environment_slug, "run_slug": run_slug, "after_event": after_event, "limit": limit})


@mcp.tool(
    name='getPipelineRunCompletion',
    description='Inspect logical closure and completion dispatch decisions\n\nReturns persisted logical-root evidence and immutable completion dispatch selections. Each causal endpoint is independently authorized; inaccessible links are omitted without identifiers or counts. Current trigger configuration is separate from this historical evidence. Reading never dispatches or reselects a deployment.\n\nCalls GET /organizations/{slug}/projects/{project_slug}/environments/{environment_slug}/runs/{run_slug}/completion.',
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True),
    structured_output=False,
)
async def tool_009_getPipelineRunCompletion(
    slug: Annotated[str, Field(description='Organization slug')],
    project_slug: Annotated[str, Field(description='Slug of the project')],
    environment_slug: Annotated[str, Field(description='Slug of the environment')],
    run_slug: Annotated[str, Field(description='Run slug of the pipeline execution')]
) -> str:
    return await api.call(OPERATIONS['getPipelineRunCompletion'], {"slug": slug, "project_slug": project_slug, "environment_slug": environment_slug, "run_slug": run_slug})


triage_tools.register(mcp)


def main() -> None:
    setup_logging()
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run(
            transport,
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )


if __name__ == "__main__":
    main()
