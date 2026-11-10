"""
mcp_client_you_pick.py » Module 3: an MCP client where YOU pick the tool

Starts the MCP server, then either lists its tools or runs the one you name.
No AI involved: this is what an AI client does, except you choose the tool and its inputs.

    python mcp_client_you_pick.py                                           # list the server's tools
    python mcp_client_you_pick.py rooms status=degraded                     # run a tool with inputs
    python mcp_client_you_pick.py find_devices search=Huddle
    python mcp_client_you_pick.py reboot_device device_id=dev-006           # shows a plan, changes nothing
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import lab_settings

lab_settings.load()  # read settings from .env


# -- config constants

SERVER = os.getenv("LAB_SERVER", "mcp_server_lens_tools.py")


# -- PRIVATE HELPERS » NO TOUCHY!


def _parse_inputs(pairs: list[str]) -> dict:
    """Turn ['status=degraded', 'confirm=true'] into {'status': 'degraded', 'confirm': True}."""
    tool_inputs = {}
    for pair in pairs:
        name, _, value = pair.partition("=")
        try:
            tool_inputs[name] = json.loads(value)  # numbers, true/false
        except json.JSONDecodeError:
            tool_inputs[name] = value  # plain text
    return tool_inputs


# -- PUBLIC FUNCTIONS


async def main():
    server = StdioServerParameters(command=sys.executable, args=[SERVER], env=dict(os.environ))
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # no tool named » list what the server offers
            if len(sys.argv) == 1:
                tool_list = await session.list_tools()
                for tool in tool_list.tools:
                    first_line = (tool.description or "").strip().splitlines()[0]
                    print(f"- {tool.name}: {first_line}")
                return

            # tool named » run it with the inputs that follow
            tool_name = sys.argv[1]
            tool_inputs = _parse_inputs(sys.argv[2:])
            result = await session.call_tool(tool_name, tool_inputs)
            for content in result.content:
                print(getattr(content, "text", content))


if __name__ == "__main__":
    asyncio.run(main())
