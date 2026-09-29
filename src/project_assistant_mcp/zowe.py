from __future__ import annotations

from mcp.server import MCPServer

from project_assistant.zowe_readonly import ZoweReadError, ZoweRunner


mcp = MCPServer(
    "Project Assistant Zowe Read-Only",
    instructions=(
        "Read-only z/OS context through the locally configured Zowe CLI. "
        "This server cannot submit jobs, upload, modify, rename, delete, or execute arbitrary Zowe commands."
    ),
)
_runner = ZoweRunner()


def _call(fn, *args, **kwargs) -> str:
    try:
        return fn(*args, **kwargs)
    except ZoweReadError as exc:
        # A normal exception becomes an MCP tool error without leaking a traceback
        # or any local credential material.
        raise RuntimeError(str(exc)) from exc


@mcp.tool()
def zowe_info() -> str:
    """Return the installed Zowe CLI version as a read-only connectivity sanity check."""
    return _call(_runner.info)


@mcp.tool()
def list_datasets(pattern: str, max_results: int = 50, attributes: bool = False) -> str:
    """List z/OS data sets matching a data-set pattern, using the existing Zowe profile/configuration."""
    return _call(_runner.list_datasets, pattern, max_results, attributes)


@mcp.tool()
def list_dataset_members(
    dataset: str,
    max_results: int = 100,
    pattern: str | None = None,
    attributes: bool = False,
) -> str:
    """List members of a partitioned z/OS data set (PDS/PDSE)."""
    return _call(_runner.list_dataset_members, dataset, max_results, pattern, attributes)


@mcp.tool()
def read_dataset(dataset: str) -> str:
    """Read the text content of a sequential data set or PDS/PDSE member such as HLQ.PDS(MEMBER)."""
    return _call(_runner.read_dataset, dataset)


@mcp.tool()
def get_job_status(job_id: str) -> str:
    """Read JES job status for a specific z/OS job ID."""
    return _call(_runner.get_job_status, job_id)


@mcp.tool()
def get_job_spool(job_id: str) -> str:
    """Read all available spool content for a specific z/OS job ID."""
    return _call(_runner.get_job_spool, job_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
