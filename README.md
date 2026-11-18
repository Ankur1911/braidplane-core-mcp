# braidplane-core-mcp

A read-only [MCP](https://modelcontextprotocol.io) server for a self-hosted
[Braidplane Core](https://github.com/Braidplane/braidplane-core). It answers two
operational questions without hand-written API calls:

- **Which pipelines are failing, and why?** `triage_failed_runs`
- **Which pipelines have not succeeded recently?** `find_stale_pipelines`

Both tools are composed from Core's public GET endpoints and return a short,
readable diagnosis instead of raw JSON. Nine generated tools expose the underlying
list/get operations for drill-down. Nothing in this server writes to Core.

## Tools

| Tool | Purpose | Core endpoints used |
|---|---|---|
| `triage_failed_runs` | Per pipeline: latest run, failing step, Core's failure reason code and suggested action, failure streak, last success, retry state; lists pipelines that recovered | `GET .../runs`, `.../runs/{run}`, `.../runs/{run}/completion` |
| `find_stale_pipelines` | Per deployed pipeline: successful-run recency verdict (stale / grace / healthy / no expectation), how overdue, latest run status | `GET .../pipeline-deployments`, `.../pipelines/{pipeline}/successful-run-recency`, `.../runs` |
| `listOrganizations`, `listProjects`, `listPipelines`, `listPipelineDeployments`, `getSuccessfulRunRecency`, `listPipelineRuns`, `getPipelineRun`, `getPipelineRunProgress`, `getPipelineRunCompletion` | Generated from `api-schema/schema.yml`, one tool per operation | the matching GET operation |

Design choices:

- Read-only: only `GET` requests are sent (a test asserts this). Secrets, runtime variables, API keys and members endpoints are not exposed.
- The token is never a tool argument. It is read from `API_TOKEN` and sent as `Authorization: Bearer ...`; use an organization API key or personal access token with read permissions only. Authorization stays entirely with Core.
- Organization, project and environment slugs are not secrets; pass them as arguments or default them with `CORE_ORG`, `CORE_PROJECT`, `CORE_ENVIRONMENT`.
- Errors are readable (401/403/404/429, connection refused, timeouts), and one failing sub-request degrades that pipeline's detail rather than the whole report.
- Triage inspects Core's most recent 100 runs (its maximum page size) and says when more exist.

## Targeted Core version

- Core `v0.2.0-rc.5` (REST API schema `api-schema/schema.yml`, `info.version 0.0.1`).
- Exercised against `main` at commit `8e40003` (`v0.2.0-rc.5` plus 11 commits; none of them change `api-schema/`).

## Quick start (local Core in one command)

Requires Docker, curl, [uv](https://docs.astral.sh/uv/) and a checkout of Core. The script compiles Core inside the Go image that Core's own Dockerfile pins (no local Go needed), starts PostgreSQL, applies migrations, runs the API and worker, seeds synthetic data with Core's own `cmd/fake --core-only` plus [`scripts/seed_local.sql`](scripts/seed_local.sql), and writes `.env` for this server.

```sh
git clone https://github.com/Braidplane/braidplane-core ../braidplane-core
scripts/local-core.sh up ../braidplane-core        # ports 3011 (API), 35432 (database); override with SERVER_PORT / DB_PORT
EXPECT_SEED=1 uv run python scripts/live_check.py  # launches the server over stdio and calls the tools
scripts/local-core.sh down ../braidplane-core      # stop and delete the disposable database
```

Core's documented full-stack path is `task dev` (see `COMMUNITY_BUILD.md`); `local-core.sh` is a lighter, API-only alternative with no frontend or mail server. The seed script inserts fixtures with SQL because Core only creates pipelines and runs through a Git backend and connector services; use it on a disposable database only.

## Point it at your own Core

```sh
cp .env.example .env     # set API_BASE_URL (Core's /api URL) and API_TOKEN
uv run server.py         # stdio MCP server
```

MCP client configuration (stdio):

```json
{
  "mcpServers": {
    "braidplane-core": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/braidplane-core-mcp", "server.py"],
      "env": { "API_BASE_URL": "http://127.0.0.1:3001/api", "API_TOKEN": "<read-only key>", "CORE_ORG": "<org-slug>", "CORE_PROJECT": "<project-slug>" }
    }
  }
}
```

Set `MCP_TRANSPORT=streamable-http` (with `MCP_HOST` / `MCP_PORT`) for HTTP instead of stdio. All settings are in [`.env.example`](.env.example).

## Example: the failed / stale run workflow

Output of `scripts/live_check.py` against the local Core seeded above (full transcript in [`docs/live-check-output.txt`](docs/live-check-output.txt)):

```text

```

Error paths against the same instance are in [`docs/live-error-paths.txt`](docs/live-error-paths.txt) (wrong token, unknown organization, Core not running).

## Tests

```sh
uv run pytest -q          # 13 tests, mocked HTTP (respx), no Core needed
```

They cover: the tool list and read-only annotations, no credential-like tool arguments, failing / recovered / healthy classification, retry state, graceful degradation when a detail call fails, 401 and connection errors, slug defaults from the environment, the stale / grace / no-expectation / unavailable states, and that only GET requests are sent.

`scripts/live_check.py` is the live check: it needs a running Core and `.env` (or environment variables) and is not part of CI.

## What was and was not tested live

Live, against a locally built Core: tool listing; both composed tools; `listProjects`; the three error paths above. Not live: the other eight generated tools (covered only by the generated code path and the shared HTTP layer), organization-wide `listOrganizations` (an organization API key is not allowed to list all organizations, which Core answers with 401; use a personal access token), and runs created by real pipeline execution (the fixtures are inserted by SQL).

## License

MIT, see [LICENSE](LICENSE).
