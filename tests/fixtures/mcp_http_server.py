import os

from mcp.server.fastmcp import FastMCP

server = FastMCP(
    "AetherChat HTTP Test MCP",
    host="127.0.0.1",
    port=int(os.environ["AETHERCHAT_TEST_MCP_PORT"]),
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


@server.tool()
def add(left: int, right: int) -> int:
    """Add two integers."""
    return left + right


if __name__ == "__main__":
    server.run(transport="streamable-http")
