"""
mcp_server_setup_check.py » a one-tool MCP server that checks your setup works

No Lens credentials needed. If the AI answers with your GitHub username, every piece works:
the MCP client, the lab gateway, the AI model and this MCP server.

    LAB_SERVER=mcp_server_setup_check.py python mcp_client_ai_picks.py "Run the lab check"
"""

import os
import platform

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("setup-check", log_level="WARNING")


@mcp.tool()
def lab_check() -> str:
    """Confirms the lab environment works. Call this when asked to run the lab check."""
    github_user = os.getenv("GITHUB_USER", "unknown user")  # set automatically in a Codespace
    return f"MCP is working for {github_user} on Python {platform.python_version()}."


if __name__ == "__main__":
    mcp.run()
