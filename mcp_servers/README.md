# AetherChat local audit MCP

`aetherchat_local_audit.py` is a read-only stdio MCP server for checking whether
an AetherChat Agent run has system-recorded execution evidence.

It exposes three tools:

- `get_runtime_health`
- `list_recent_runs`
- `get_run_evidence`

The server never writes to the AetherChat database and intentionally omits user
message bodies and sensitive tool arguments. A browser `launcher_accepted`
receipt proves only that the launcher accepted the URL; it does not prove that
the page loaded or that any page interaction occurred.

Example connection values:

```text
transport: stdio
command: /opt/AetherChat/resources/backend/aetherchat-backend
args:
  local-audit-mcp
  --data-dir
  /home/jimes0813/.config/aetherchat-frontend/backend-data
```

For source-tree development, run the server with the repository virtual
environment and `mcp_servers/aetherchat_local_audit.py` directly.
