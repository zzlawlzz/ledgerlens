"""MCP healthcheck (T-027): initialize a session against a streamable-HTTP server.

Usage: python scripts/mcp_ping.py http://127.0.0.1:8765/mcp
"""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main(url: str) -> int:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
