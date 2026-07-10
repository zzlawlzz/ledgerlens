"""Shared test config.

Windows: psycopg async (langgraph checkpointer) refuses the default
ProactorEventLoop — tests run on the selector loop instead. The production
runtime is the Linux container where the default loop is already compatible.
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
