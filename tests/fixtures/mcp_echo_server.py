from mcp.server.fastmcp import FastMCP


server = FastMCP("AetherChat Test MCP", json_response=True)


@server.tool()
def echo(message: str) -> str:
    """Return the supplied message."""
    return f"echo:{message}"


if __name__ == "__main__":
    server.run(transport="stdio")
