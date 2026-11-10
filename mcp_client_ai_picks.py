"""
mcp_client_ai_picks.py » Modules 4-5: an MCP client where the AI picks the tools

What every MCP client (Claude, Copilot...) does, in a small file:
    1. start the MCP server and ask it for its tool list
    2. send your question + the tool list to the AI model
    3. if the AI model asks for a tool, run it on the MCP server and send back the result
    4. repeat until the AI model answers in plain language

    python mcp_client_ai_picks.py                                        # interactive chat
    python mcp_client_ai_picks.py "Which rooms are down or degraded?"    # one question

Where the AI model comes from
    During the lab: through the lab gateway. It holds one shared AI account, so nobody needs
    their own. Your seat key only works with it, and both are switched off after the lab.
    The gateway is also a small version of what companies put in front of AI models:
    per-person keys, usage limits and a usage log.
        .env » LAB_LLM_BASE_URL (already set)   LAB_LLM_API_KEY (your seat key)

    After the lab: use your own API key. Any provider with an OpenAI-compatible chat API
    and tool calling works. Change three lines in .env, e.g. for Anthropic:
        LAB_LLM_BASE_URL=https://api.anthropic.com/v1
        LAB_LLM_API_KEY=<your API key>
        LAB_MODEL=claude-haiku-5-5
    See "Keep going on your own" in LAB_GUIDE.md.
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from typing import Any

import lab_settings

lab_settings.load()  # read settings from .env
console = Console(highlight=False)


# -- config constants

SERVER = os.getenv("LAB_SERVER", "mcp_server_lens_tools.py")
BASE_URL = os.getenv("LAB_LLM_BASE_URL", "")
API_KEY = os.getenv("LAB_LLM_API_KEY", "")
MODEL = os.getenv(
    "LAB_MODEL", "lab-default"
)  # the lab gateway always uses its own model
MAX_STEPS = int(os.getenv("LAB_MAX_STEPS", "8"))  # AI model requests per question
HOST = os.getenv("LAB_HOST_NAME", "the lab host")  # who to ask for help
SHOW_LEGEND = os.getenv("LAB_LEGEND", "1") != "0"
MAX_TOOL_RESULT = 20000  # characters of each tool result sent to the AI model

# the AI model's standing instructions, sent before your first question
SYSTEM_PROMPT = (
    "You help IT admins troubleshoot Poly voice and video devices using the Poly Lens tools. "
    "Prefer specific tools when they fit (e.g. rooms, find_devices, get_device, offline_devices). "
    "If none fits, use the schema tools to discover the API, then write your own read-only query. "
    "Never change a device without the user's explicit approval: call change tools with "
    "confirm=false first, show the plan, and wait. Keep answers short and practical."
)

LEGEND = """How to read the steps below (each one is a hop in the Module 4 diagram):
  [1] MCP CLIENT -> AI MODEL   this app sends your question + the list of tools the AI model may use
  [2] AI MODEL -> MCP CLIENT   the AI model asks this app to run one tool (it never runs anything itself)
  [3] TOOL RESULT              this app ran the tool on the MCP server, which called the Poly Lens API;
                               the result goes back to the AI model with the next [1]
  AI ANSWER                    the AI model's final answer, written from those results"""


# -- PRIVATE HELPERS » NO TOUCHY!


def _show_step(style: str, label: str, text: str):
    """One line per hop, e.g. '[2] AI MODEL -> MCP CLIENT   please run the tool ...'."""
    safe_text = text.replace(
        "[", "\\["
    )  # so rich doesn't read [brackets] as colour tags
    console.print(f"  [{style}]{label:<28}[/{style}] {safe_text}", soft_wrap=False)


def _show_error(message: str):
    console.print(f"[bold red]{message}[/bold red]")


def _show_answer(text: str):
    console.print()
    console.print(
        Panel(
            Markdown(text or ""),
            title="AI ANSWER",
            title_align="left",
            border_style="green",
        )
    )
    console.print()


def _preview(text: str) -> str:
    """The tool result squeezed onto one line that fits the terminal."""
    one_line = f"{len(text):,} characters: " + " ".join(text.split())
    room = max(40, console.width - 36)
    if len(one_line) <= room:
        return one_line
    return one_line[: room - 4] + " ..."


def _explain_model_error(error: Exception) -> str:
    """Turn an AI model / gateway error into a plain-English next step."""
    body = getattr(error, "body", None)
    gateway_message = body.get("message") if isinstance(body, dict) else None

    if isinstance(error, AuthenticationError):
        return "Your lab key was not accepted. Check LAB_LLM_API_KEY in .env (no spaces, no quotes)."
    if isinstance(error, (RateLimitError, PermissionDeniedError)):
        return f"Lab gateway: {gateway_message or error}"
    if isinstance(error, APIConnectionError):
        return f"Can't reach the lab gateway at {BASE_URL}. Check LAB_LLM_BASE_URL in .env."
    if isinstance(error, APIStatusError):
        if error.status_code == 404:
            return (
                f"Nothing answers at {BASE_URL.rstrip('/')}/chat/completions. Check LAB_LLM_BASE_URL in .env: "
                "it should be the gateway address ending in /v1."
            )
        if error.status_code in (502, 503, 504) and not gateway_message:
            return f"The lab gateway isn't answering (HTTP {error.status_code}). It may be stopped » Tell {HOST}."
        return f"The AI model request failed (HTTP {error.status_code}): {gateway_message or error.message}"
    return f"Unexpected error: {error}"


def _limit_result(result_text: str) -> str:
    """Cap a tool result's size, and tell the AI model when it was cut so it doesn't treat it as complete."""
    if len(result_text) <= MAX_TOOL_RESULT:
        return result_text
    return (
        result_text[:MAX_TOOL_RESULT]
        + f"\n[result cut at {MAX_TOOL_RESULT:,} of {len(result_text):,} characters: use filters or a narrower question to see the rest]"
    )


def _to_model_tool(mcp_tool) -> dict:
    """An MCP tool (name, description, inputs) in the format the AI model's chat API expects."""
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description or "",
            "parameters": mcp_tool.inputSchema,
        },
    }


# -- PUBLIC FUNCTIONS


async def ask(
    llm: OpenAI,
    session: ClientSession,
    tools: list[Any],
    messages: list[Any],
    question: str,
):
    """Answer one question: loop between the AI model and the MCP server until the model answers."""
    messages.append({"role": "user", "content": question})

    for step in range(1, MAX_STEPS + 1):
        # [1] send the conversation so far + the tool list to the AI model
        sending = "your question" if step == 1 else "the tool results"
        plural = "s" if len(tools) != 1 else ""
        _show_step(
            "dim",
            "[1] MCP CLIENT -> AI MODEL",
            f"sends {sending} + {len(tools)} tool description{plural} (request {step})",
        )
        try:
            response = llm.chat.completions.create(
                model=MODEL, messages=messages, tools=tools
            )
        except (APIStatusError, APIConnectionError) as error:
            _show_error(_explain_model_error(error))
            return

        if isinstance(
            response, str
        ):  # something answered that isn't a chat API (wrong URL)
            _show_error(
                f"{BASE_URL} did not answer like a chat API. Check LAB_LLM_BASE_URL in .env. It replied: {response[:200]}"
            )
            return

        reply = response.choices[0].message
        messages.append(reply.model_dump(exclude_none=True))

        # no tool requested » this is the final answer
        if not reply.tool_calls:
            # an empty answer usually means the AI model ran out of answer space (finish_reason: length)
            if not reply.content:
                finish_reason = response.choices[0].finish_reason
                _show_error(f"The AI model returned an empty answer (finish_reason: {finish_reason}). Try a narrower question.")
                return
            _show_answer(reply.content)
            return

        for tool_call in reply.tool_calls:
            if tool_call.type != "function":
                continue  # lab only offers function tools
            # [2] the AI model asks for a tool; it never runs anything itself
            tool_name = tool_call.function.name
            tool_inputs = json.loads(tool_call.function.arguments or "{}")
            shown_inputs = (
                ", ".join(f"{name}={value!r}" for name, value in tool_inputs.items())
                or "no arguments"
            )
            _show_step(
                "cyan",
                "[2] AI MODEL -> MCP CLIENT",
                f"please run the tool '{tool_name}' ({shown_inputs})",
            )

            # [3] this app runs the tool on the MCP server and keeps the result for the next request
            result = await session.call_tool(tool_name, tool_inputs)
            result_text = "\n".join(
                getattr(content, "text", str(content)) for content in result.content
            )
            _show_step("magenta", "[3] TOOL RESULT", _preview(result_text))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": _limit_result(result_text),
                }
            )

    _show_error(
        f"Stopped after {MAX_STEPS} steps (LAB_MAX_STEPS). Try a narrower question."
    )


async def main():
    if not BASE_URL or "<" in BASE_URL:
        raise SystemExit(
            f"Set LAB_LLM_BASE_URL in .env: copy that line from .env.example, or ask {HOST}."
        )
    if not API_KEY:
        raise SystemExit("Set LAB_LLM_API_KEY in .env to the key on your seat card.")
    llm = OpenAI(base_url=BASE_URL, api_key=API_KEY, max_retries=0)

    server = StdioServerParameters(
        command=sys.executable, args=[SERVER], env=dict(os.environ)
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. ask the MCP server for its tools, and convert them for the AI model
            tool_list = await session.list_tools()
            tools = [_to_model_tool(tool) for tool in tool_list.tools]
            tool_names = ", ".join(tool.name for tool in tool_list.tools)
            print(f"MCP server: {SERVER} offers {len(tools)} tools: {tool_names}")
            print(f"AI model:   reached through {BASE_URL}")
            if SHOW_LEGEND:
                print(f"\n{LEGEND}")
            print()
            console.rule(
                "[bold]Your question: step by step[/bold]", align="left", style="dim"
            )

            messages = [{"role": "system", "content": SYSTEM_PROMPT}]

            # one question on the command line » answer it and stop
            if len(sys.argv) > 1:
                await ask(llm, session, tools, messages, " ".join(sys.argv[1:]))
                return

            # otherwise » chat until 'exit'
            while True:
                try:
                    question = input("Your question (or 'exit'): ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if question.lower() in ("exit", "quit"):
                    break
                if question:
                    await ask(llm, session, tools, messages, question)


if __name__ == "__main__":
    asyncio.run(main())
