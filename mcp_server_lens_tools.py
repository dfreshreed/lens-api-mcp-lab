"""
mcp_server_lens_tools.py » the MCP server with tools built for Lens

Offers Lens capabilities as MCP tools: each one is a function + a description.
It does nothing on its own: an MCP client (mcp_client_ai_picks.py, mcp_client_you_pick.py,
Claude, Copilot...) starts it and calls its tools. It holds the Lens login and the safety rules.

Settings (.env):
    POLY_LENS_CLIENT_ID / POLY_LENS_CLIENT_SECRET   Lens API credential
    LENS_MOCK=1                                     use built-in sample data instead of Lens
    LAB_MODE=simulate|live                          simulate (default): changes are described, never sent
    LAB_ALLOWED_DEVICE_IDS                          comma list; in live mode only these devices can change
    LAB_ENABLE_RAW_MUTATIONS=1                      host only: allow any GraphQL mutation
    MCP_TRANSPORT=stdio|http                        stdio (default, the lab) or http (hosted)
    MCP_BEARER_TOKEN                                required in http mode; clients send "Authorization: Bearer <token>"
    PORT                                            http port (default 8000)
"""

import json
import os

from mcp.server.fastmcp import FastMCP

from lens_api_helper import (
    LIST_OPERATIONS,
    OFFLINE,
    REBOOT_DEVICE,
    ROOM_GEAR,
    execute_gql,
    get_device_details,
    reboot_variables,
    room_health,
    search_devices,
    send_reboot,
)


# -- config constants

LAB_MODE = os.getenv("LAB_MODE", "simulate").lower()
ALLOWED_DEVICE_IDS = [device_id.strip() for device_id in os.getenv("LAB_ALLOWED_DEVICE_IDS", "").split(",") if device_id.strip()]
RAW_MUTATIONS = os.getenv("LAB_ENABLE_RAW_MUTATIONS", "0") == "1"
TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio").lower()
PORT = int(os.getenv("PORT", "8000"))

DESCRIBE_TYPE = """
query DescribeType($typeName: String!) {
    __type(name: $typeName) {
        name
        kind
        description
        fields { name description type { name kind ofType { name kind } } }
        inputFields { name type { name kind ofType { name kind } } }
        enumValues { name description }
    }
}
"""

mcp = FastMCP("poly-lens", host="0.0.0.0", port=PORT, stateless_http=True, log_level="WARNING")


# -- PRIVATE HELPERS » NO TOUCHY!

_schema_cache = None


def _compact(device: dict) -> dict:
    """Just the fields worth sending to the AI model, so a long device list still fits."""
    return {
        "id": device.get("id"),
        "name": device.get("displayName"),
        "model": device.get("hardwareModel"),
        "firmware": device.get("softwareVersion"),
        "connected": device.get("connected"),
        "room": (device.get("room") or {}).get("name"),
        "site": (device.get("site") or {}).get("name"),
        "last_seen": device.get("lastDetected"),
    }


def _schema() -> dict:
    """The API's list of operations, fetched once and reused."""
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = execute_gql(LIST_OPERATIONS)["data"]["__schema"]
    return _schema_cache


def _require_bearer(app, token: str):
    """Http mode only: reject requests that don't carry the right bearer token."""
    expected = f"Bearer {token}".encode()

    async def wrapped(scope, receive, send):
        headers = dict(scope.get("headers") or [])
        if scope["type"] == "http" and headers.get(b"authorization") != expected:
            await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await app(scope, receive, send)

    return wrapped


# -- TOOLS: discovery (GraphQL describes itself)


@mcp.tool()
def list_operations(kind: str = "query", keyword: str = "") -> str:
    """List the Poly Lens API operations that exist. kind: 'query' (read) or 'mutation' (change).
    keyword filters by name or description, e.g. 'device', 'room', 'reboot', 'software'.
    Use this FIRST when no other tool answers the question."""
    schema_key = "queryType" if kind == "query" else "mutationType"
    keyword = keyword.lower()

    operations = []
    for field in (_schema().get(schema_key) or {}).get("fields") or []:
        description = field.get("description") or ""
        if keyword and keyword not in field["name"].lower() and keyword not in description.lower():
            continue
        operations.append({
            "name": field["name"],
            "description": description[:200],
            "args": [arg["name"] for arg in field.get("args") or []],
        })
    return json.dumps(operations, indent=2)


@mcp.tool()
def describe_type(type_name: str) -> str:
    """Show the fields of a Lens GraphQL type so you can write a correct query.
    Useful types: 'Device' (device fields), 'Filter' (deviceSearch filter operators),
    'DeviceFindArgs' (deviceSearch params)."""
    return json.dumps(execute_gql(DESCRIBE_TYPE, {"typeName": type_name}), indent=2)


@mcp.tool()
def lens_query(query: str, variables: dict | None = None) -> str:
    """Run a READ-ONLY GraphQL query against Poly Lens. Mutations are rejected.
    Use list_operations and describe_type first to get exact field names.
    deviceSearch tips:
      - query($params: DeviceFindArgs) { deviceSearch(params: $params) {
          edges { node { id displayName hardwareModel softwareVersion connected } }
          pageInfo { hasNextPage totalCount nextToken } } }
      - params: {"pageSize": 100, "filter": {...}, "nextToken": "<from previous page>"}
      - filter: {"field": "<Device field name>", "<operator>": "<string value>"}
        operators: eq, ne, contains, notContains, beginsWith, lt, le, gt, ge
        booleans are strings: {"field": "connected", "eq": "false"}
        combine: {"AND": [{...}, {...}]}, also OR / NOT
        broad text search: {"searchTerm": "Huddle"}
      - useful filter fields: hardwareModel, hardwareFamily, softwareVersion, connected
      - room gear families: Studio X Family Large/Small, G7500 Family, HP Conference PC,
        Studio V Family, Poly Studio R Family, Poly Studio USB Family, Trio Family,
        Poly Touch Controller, Studio E Family, Poly Microphone (+ hardwareModel "Sync 60")
      - linked devices: select connections { id name hardwareModel hardwareFamily connected }
      - softwareVersion compares as text, so "10.0" < "9.4"; compare versions yourself.
      - an empty result can mean the filter was wrong: check a broader query before saying "none".
    """
    if query.lstrip().lower().startswith("mutation"):
        return "Rejected: lens_query is read-only."
    return json.dumps(execute_gql(query, variables), indent=2)


# -- TOOLS: ready-made reads


@mcp.tool()
def find_devices(search: str = "", model: str = "", room_gear_only: bool = False, offline_only: bool = False, limit: int = 50) -> str:
    """Find devices. Returns total matches in Lens, how many were returned, and for each
    device: id, name, model, firmware, connected (online status), room, site,
    last_seen (accurate within ~6 hours). Use get_device for full detail on one device.
    search: broad text match on name, room, site or id (can return loose matches).
    model: filter on hardware model, e.g. 'Studio X50' or 'G7500'. Use it when the user
      names a product; use search + model together for 'the X30 in Huddle 2A'.
    room_gear_only: only room equipment (codecs, MTR PCs, video bars, Trios, touch
      controllers, cameras, mics, Sync 60); excludes desk phones, headsets and apps.
    offline_only: only devices that are not connected.
    If total > returned, there are more matches: narrow the filters instead of raising limit."""
    extra_filters = []
    if room_gear_only:
        extra_filters.append(ROOM_GEAR)
    if offline_only:
        extra_filters.append(OFFLINE)
    result = search_devices(search, model, extra_filters, limit=limit)

    devices = []
    for device in result["devices"]:
        devices.append(_compact(device))

    # one device per line: readable, and far shorter than indenting every field
    device_lines = ",\n".join(json.dumps(device) for device in devices)
    return f'{{"total": {result["total"]}, "returned": {result["returned"]}, "devices": [\n{device_lines}\n]}}'


@mcp.tool()
def rooms(search: str = "", status: str = "") -> str:
    """Room health. One entry per primary device (codec, MTR PC, standalone video bar or Trio)
    with its linked peripherals. The room's status comes from its devices:
      down = primary device offline; degraded = primary device online but a linked peripheral is offline;
      unknown = Lens did not report status; healthy = everything online.
    A primary device offline longer than LAB_STALE_DAYS is marked inactive (likely unplugged or
    retired); those rooms are left out, and the result says how many.
    A peripheral offline within LAB_STALE_DAYS makes the room degraded (users may not be able to
    start a meeting). One offline longer is listed in peripherals_to_review instead: tell the user it
    needs investigating, it could be a real problem or just cleanup.
    room is null when the primary device isn't assigned to a room in Lens; point these out as needing
    a room assignment (it affects room analytics and reporting).
    Each offline or to-review peripheral includes offline_for (days since Lens last saw it).
    search: narrow by room, site or device name.
    status: only return 'down', 'degraded', 'unknown' or 'healthy'.
    Use this for questions about rooms; use get_device for detail on one device."""
    return json.dumps(room_health(search, status), indent=2)


@mcp.tool()
def get_device(device_id: str) -> str:
    """Full detail for one device (network, serial, firmware, last seen) plus the devices linked to it
    (connections: touch controllers, cameras, the codec or PC it belongs to). Needs the id from find_devices."""
    return json.dumps(get_device_details(device_id), indent=2)


# -- TOOLS: changes, with safety rules


@mcp.tool()
def reboot_device(device_id: str, confirm: bool = False) -> str:
    """Reboot a Poly device through Lens. Only works for devices that are ONLINE in Lens:
    the reboot command travels over the device's cloud connection, so an offline device
    can't receive it (check power and network on site instead). Rebooting a room's primary
    device (codec, video bar) also restarts its link to peripherals like cameras and touch controllers.
    ALWAYS call first with confirm=false and show the user the plan; only call with
    confirm=true after the user explicitly says yes."""
    device = (get_device_details(device_id).get("data") or {}).get("device")
    if not device:
        return f"NOT FOUND: no device with id {device_id}. Use find_devices or rooms to get the id."

    name = device.get("displayName") or device.get("name") or device_id
    room_name = (device.get("room") or {}).get("name") or "no room assigned"

    # rule 1: an offline device can't receive a command
    if device.get("connected") is False:
        return (
            f"NOT POSSIBLE: {name} ({room_name}) is offline in Lens. Reboot commands are delivered over "
            "the device's cloud connection, so it can't receive one. Next step: check power and "
            "network at the device, or reboot it on site."
        )

    # rule 2: show a plan first, change nothing until the user approves
    if not confirm:
        return (
            f"PLAN (not executed): reboot {name} ({device.get('hardwareModel')}, {room_name}). "
            "It will drop any active call and be unavailable for ~2-3 minutes. Ask the user to "
            "approve, then call again with confirm=true."
        )

    # rule 3: simulate mode shows the mutation and sends nothing
    if LAB_MODE != "live":
        return (
            f"SIMULATED: would send this mutation to Poly Lens:\n{REBOOT_DEVICE.strip()}\n"
            f"variables: {json.dumps(reboot_variables(device_id))}\n"
            "(Server is in simulate mode, so nothing was sent.)"
        )

    # rule 4: live mode only changes devices on the allowlist
    if not ALLOWED_DEVICE_IDS:
        return "BLOCKED: live mode requires LAB_ALLOWED_DEVICE_IDS to be set."
    if device_id not in ALLOWED_DEVICE_IDS:
        return f"BLOCKED: {device_id} is not one of this lab seat's devices {sorted(ALLOWED_DEVICE_IDS)}."

    return json.dumps(send_reboot(device_id), indent=2)


if RAW_MUTATIONS:

    @mcp.tool()
    def lens_mutation(mutation: str, variables: dict | None = None, confirm: bool = False) -> str:
        """LAB HOST ONLY: run any GraphQL mutation. Show the user the exact mutation and
        get explicit approval before calling with confirm=true."""
        if not mutation.lstrip().lower().startswith("mutation"):
            return "Rejected: not a mutation."
        if not confirm:
            return f"Not executed. Show the user:\n{mutation}\n{json.dumps(variables or {})}"
        return json.dumps(execute_gql(mutation, variables), indent=2)


# -- MODULE 3 EXERCISE: turn the Module 2 script into an MCP tool
# Uncomment the 5 lines below (remove "# " from each). Same function, now with a decorator + description.
#
# from script_offline_devices import get_offline_devices
# @mcp.tool()
# def offline_devices() -> str:
#     """List room gear that is currently offline, with model, room, firmware and last seen."""
#     return json.dumps(get_offline_devices(), indent=2)


# -- PROMPT for richer clients (Claude)


@mcp.prompt()
def troubleshoot_device(device_name: str) -> str:
    """Guided remediation workflow for one Poly device."""
    return f"""Troubleshoot the Poly device "{device_name}" with the poly-lens tools.
1. find_devices, then get_device for full detail, including its connections (linked devices).
2. Check online status, last seen, firmware vs. other devices of the same model, network details,
   and whether the primary device or linked peripherals are offline too (use rooms for the room view).
3. Explain the likely root cause in plain language.
4. Propose fixes from least to most disruptive. Never change anything without my explicit OK.
5. After any change, re-check status and report the outcome."""


# -- START

if __name__ == "__main__":
    if TRANSPORT == "http":
        import uvicorn

        bearer_token = os.environ.get("MCP_BEARER_TOKEN")
        if not bearer_token:
            raise SystemExit("MCP_BEARER_TOKEN must be set in http mode.")
        uvicorn.run(_require_bearer(mcp.streamable_http_app(), bearer_token), host="0.0.0.0", port=PORT)
    else:
        mcp.run()  # stdio: the client starts this process and talks to it over stdin/stdout
