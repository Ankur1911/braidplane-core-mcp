"""Live check: launch server.py over stdio with the MCP client and call the tools against a running Core.

Required environment: API_BASE_URL, API_TOKEN, CORE_ORG, CORE_PROJECT (and CORE_ENVIRONMENT, default 'dev').
Run from the repository root:   uv run python scripts/live_check.py
With the synthetic fixtures from scripts/seed_local.sql it also asserts the expected findings.
"""
import asyncio
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("API_BASE_URL", "API_TOKEN", "CORE_ORG", "CORE_PROJECT")


def load_dotenv(path: Path) -> None:
    """Real environment variables win over .env."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def text(result) -> str:
    return "".join(c.text for c in result.content)


async def main() -> int:
    load_dotenv(ROOT / ".env")
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print("Set " + ", ".join(missing), file=sys.stderr)
        return 2
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], env=dict(os.environ), cwd=str(ROOT))
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        print(f"tools ({len(tools)}): " + ", ".join(t.name for t in tools) + "\n")
        triage = text(await client.call_tool("triage_failed_runs", {}))
        print(triage + "\n" + "-" * 72)
        stale = text(await client.call_tool("find_stale_pipelines", {}))
        print(stale + "\n" + "-" * 72)
        projects = text(await client.call_tool("listProjects", {"slug": os.environ["CORE_ORG"]}))
        print("listProjects ->", projects[:300])
    if os.environ.get("EXPECT_SEED") == "1":
        assert "FAILING  customer-enrichment" in triage, "expected customer-enrichment to be failing"
        assert "source_query_failed" in triage
        assert "STALE  inventory-export" in stale, "expected inventory-export to be stale"
        assert "HEALTHY  orders-daily-sync" in stale
        print("\nOK: findings match the seeded fixtures")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
