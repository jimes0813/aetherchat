from __future__ import annotations

import asyncio
import re

from ..context_assembler import BROWSER_OPEN_TOOL_NAME

MAX_MODEL_TURNS = 32
PSEUDO_TOOL_CALL_RE = re.compile(r"请求执行工具\s*[:：]\s*([A-Za-z0-9_]+)")
BROWSER_OPEN_TOOL = BROWSER_OPEN_TOOL_NAME
_COMPUTER_USE_LOCK = asyncio.Lock()
