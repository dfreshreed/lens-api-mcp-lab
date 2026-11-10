"""
lens_api_helper.py » shared Poly Lens API helper

One place for the Lens login, every GraphQL query, and the room rules.
Used by Module 1 (run directly), the Module 2 script and both MCP servers.

Run directly (Module 1):
    python lens_api_helper.py token             # log in and get an access token
    python lens_api_helper.py ops               # ask the API what it can do (introspection)
    python lens_api_helper.py count             # how many devices / room devices the tenant has
    python lens_api_helper.py rooms             # room status
    python lens_api_helper.py rooms all         # room status, including rooms with an inactive primary device
    python lens_api_helper.py rooms inactive    # only rooms with an inactive primary device
    python lens_api_helper.py '{ __typename }'  # run any GraphQL query

Settings (.env):
    LENS_MOCK=1          use built-in sample data (no Lens credentials needed)
    LENS_PAGE_SIZE       devices fetched per request (default 100)
    LAB_STALE_DAYS       days offline before a device counts as inactive (default 30, 0 = off)
"""

import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
from rich.console import Console

import lab_settings

lab_settings.load()  # read settings from .env
console = Console(highlight=False)


# -- config constants

# lens gql and auth endpoints
GRAPHQL_URL = os.getenv(
    "POLY_LENS_API_URL", "https://api.silica-prod01.io.lens.poly.com/graphql"
)
TOKEN_URL = os.getenv("POLY_LENS_TOKEN_URL", "https://login.lens.poly.com/oauth/token")

# lens api creds
CLIENT_ID = os.getenv("POLY_LENS_CLIENT_ID")
CLIENT_SECRET = os.getenv("POLY_LENS_CLIENT_SECRET")

MOCK = os.getenv("LENS_MOCK", "0") == "1"
PAGE_SIZE = int(os.getenv("LENS_PAGE_SIZE", "100"))
STALE_DAYS = int(os.getenv("LAB_STALE_DAYS", "30"))


# -- lens queries

# the device fields every search returns
DEVICE_FIELDS = """
            id
            displayName
            connected
            hardwareModel
            hardwareFamily
            softwareVersion
            serialNumber
            macAddress
            room { name }
            site { name }
            lastDetected
"""

# the devices linked to a device (touch controller, cameras, the codec it belongs to)
CONNECTION_FIELDS = """
            connections {
                id
                name
                hardwareModel
                hardwareFamily
                connected
                lastDetected
            }
"""

SEARCH_DEVICES = """
query SearchDevices($params: DeviceFindArgs) {
    deviceSearch(params: $params) {
        edges {
            node {
                __FIELDS__
            }
        }
        pageInfo {
            hasNextPage
            totalCount
            nextToken
        }
    }
}
""".replace("__FIELDS__", DEVICE_FIELDS)

# same search, plus each device's linked devices (used for room health)
SEARCH_ROOM_DEVICES = SEARCH_DEVICES.replace(
    DEVICE_FIELDS, DEVICE_FIELDS + CONNECTION_FIELDS
)

GET_DEVICE_DETAILS = """
query GetDeviceDetails($deviceId: String!) {
    device(id: $deviceId) {
        id
        name
        displayName
        connected
        lastConfigRequestDate
        hardwareModel
        hardwareFamily
        serialNumber
        macAddress
        internalIp
        softwareVersion
        hardwareRevision
        room { name }
        site { name }
        hasPeripherals
        allPeripheralsLinked
        activeApplicationName
        activeApplicationVersion
        connections {
            id
            name
            hardwareModel
            hardwareFamily
            connected
            lastDetected
        }
    }
}
"""

REBOOT_DEVICE = """
mutation RebootDevice($deviceId: String!, $includeLinkedDevices: Boolean) {
    rebootDevice(deviceId: $deviceId, includeLinkedDevices: $includeLinkedDevices) {
        error
        success
    }
}
"""

# introspection: the API describes every query (read) and mutation (change) it offers
LIST_OPERATIONS = """
query ListOperations {
    __schema {
        queryType { fields { name description args { name } } }
        mutationType { fields { name description args { name } } }
    }
}
"""


# -- room gear rules

# the device that runs a room, ranked: a higher rank wins when two are linked
# (e.g. an X32 linked to an MTR PC is that PC's peripheral)
PRIMARY_RANK = {
    "HP Conference PC": 3,
    "Studio X Family Large": 2,
    "Studio X Family Small": 2,
    "G7500 Family": 2,
    "Studio V Family": 1,
    "Poly Studio R Family": 1,
    "Poly Studio USB Family": 1,
    "Trio Family": 1,
}
PERIPHERAL_FAMILIES = ["Poly Touch Controller", "Studio E Family", "Poly Microphone"]
ROOM_AUDIO_MODELS = ["Sync 60"]
IGNORE_LINKED_MODELS = [
    "Desktop App"
]  # a user's PC running Lens Desktop, not room gear

# lens filters: {"field": <device field>, <operator>: "<text>"}, combined with AND / OR / NOT
ROOM_PRIMARIES = {
    "OR": [{"field": "hardwareFamily", "eq": family} for family in PRIMARY_RANK]
}
ROOM_GEAR = {
    "OR": [
        {"field": "hardwareFamily", "eq": family}
        for family in [*PRIMARY_RANK, *PERIPHERAL_FAMILIES]
    ]
    + [{"field": "hardwareModel", "contains": model} for model in ROOM_AUDIO_MODELS]
}
OFFLINE = {"field": "connected", "eq": "false"}  # booleans are sent as text


# -- PRIVATE HELPERS » NO TOUCHY!

_token_cache = {"value": None, "expires": 0.0}


def _check_errors(response: dict) -> dict:
    """GraphQL can return data AND errors together (e.g. one field this credential can't read).
    Stop only when there's no data at all; otherwise warn and keep going."""
    errors = response.get("errors")
    if not errors:
        return response
    if not response.get("data"):
        raise RuntimeError(json.dumps(errors, indent=2))
    messages = sorted({error.get("message") for error in errors})
    print(f"warning: Lens returned partial errors: {messages}", file=sys.stderr)
    return response


def _name_of(device: dict) -> str:
    return (
        device.get("displayName")
        or device.get("name")
        or device.get("hardwareModel")
        or device.get("id")
        or "unknown device"
    )


def _short(device: dict) -> dict:
    """The few fields worth showing for a device."""
    return {
        "id": device.get("id"),
        "name": _name_of(device),
        "model": device.get("hardwareModel"),
        "family": device.get("hardwareFamily"),
        "connected": device.get("connected"),
        "last_seen": device.get("lastDetected"),
    }


def _parse_time(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _offline_for(timestamp: str | None) -> str:
    """'5d', '<1d' or 'unknown': how long since Lens last saw the device."""
    last_seen = _parse_time(timestamp)
    if not last_seen:
        return "unknown"
    days = (datetime.now(timezone.utc) - last_seen).days
    return f"{days}d" if days >= 1 else "<1d"


def _is_stale(device: dict, cutoff: datetime | None) -> bool:
    """True when the device was last seen before the cutoff (LAB_STALE_DAYS ago)."""
    last_seen = _parse_time(device.get("lastDetected"))
    return bool(cutoff and last_seen and last_seen < cutoff)


def _merge_duplicates(devices: list[dict]) -> list[dict]:
    """A USB device can be in Lens twice (network record + Lens Desktop record) with the same
    serial number. Merge them into one: online if either is online, latest last seen, all ids kept.
    The record with a MAC address is the one to act on (e.g. the reboot target)."""
    records_by_serial = {}
    for device in devices:
        serial = (device.get("serialNumber") or device.get("id") or "").lower()
        records_by_serial.setdefault(serial, []).append(device)

    merged = []
    for records in records_by_serial.values():
        if len(records) == 1:
            merged.append(records[0])
            continue

        main = max(
            records,
            key=lambda record: (
                bool(record.get("macAddress")),
                record.get("lastDetected") or "",
            ),
        )

        if any(record.get("connected") is True for record in records):
            connected = True
        elif any(record.get("connected") is False for record in records):
            connected = False
        else:
            connected = None

        connections = []
        for record in records:
            connections.extend(record.get("connections") or [])

        merged.append(
            {
                **main,
                "ids": [record["id"] for record in records],
                "connected": connected,
                "lastDetected": max(
                    record.get("lastDetected") or "" for record in records
                )
                or None,
                "connections": connections or main.get("connections"),
            }
        )
    return merged


def _fill_missing(link: dict, devices_by_id: dict) -> dict:
    """Lens sometimes leaves a field empty on a linked device; copy it from the full record if we have it."""
    full_record = devices_by_id.get(link.get("id")) or {}
    filled = dict(link)
    for field in (
        "displayName",
        "hardwareModel",
        "hardwareFamily",
        "connected",
        "lastDetected",
    ):
        if filled.get(field) is None and full_record.get(field) is not None:
            filled[field] = full_record[field]
    return filled


def _peripheral_summary(link: dict) -> dict:
    return {
        "name": _name_of(link),
        "id": link.get("id"),
        "last_seen": link.get("lastDetected"),
        "offline_for": _offline_for(link.get("lastDetected")),
    }


def _build_filter(
    search: str = "", model: str = "", extra: dict | list | None = None
) -> dict | None:
    """Combine a text search, a model and any extra Lens filters with AND."""
    parts = []
    if search:
        parts.append({"searchTerm": search})
    if model:
        parts.append({"field": "hardwareModel", "contains": model})
    if isinstance(extra, list):
        parts.extend(extra)
    elif extra:
        parts.append(extra)

    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return {"AND": parts}


# -- PUBLIC FUNCTIONS


def get_token() -> str:
    """OAuth2 client-credentials token, cached until 60 seconds before it expires (Lens: 24 hours)."""
    if MOCK:
        return "mock-token"
    if _token_cache["value"] and time.time() < _token_cache["expires"] - 60:
        return _token_cache["value"]
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError(
            "POLY_LENS_CLIENT_ID / POLY_LENS_CLIENT_SECRET are not set in .env"
        )

    auth_payload = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    auth_response = httpx.post(TOKEN_URL, json=auth_payload, timeout=30)
    if auth_response.status_code >= 400:
        raise RuntimeError(
            f"Lens token request returned HTTP {auth_response.status_code}: {auth_response.text[:500]}"
        )

    # a wrong secret still returns HTTP 200, just without a token
    data = auth_response.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(
            f"Lens didn't return a token. Check the client ID and secret in .env. Lens said: {auth_response.text[:300]}"
        )

    _token_cache["value"] = token
    _token_cache["expires"] = time.time() + int(data.get("expires_in", 3600))
    return token


def execute_gql(query: str, variables: dict | None = None) -> dict:
    """Send one GraphQL request to Lens and return the JSON response."""
    if MOCK:
        from lens_sample_data import mock_gql

        return mock_gql(query, variables or {})

    payload = {"query": query, "variables": variables or {}}
    headers = {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json",
    }
    response = httpx.post(GRAPHQL_URL, json=payload, headers=headers, timeout=60)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Lens API returned HTTP {response.status_code}: {response.text[:1000]}"
        )
    return response.json()


def search_devices(
    search: str = "",
    model: str = "",
    extra: dict | list | None = None,
    limit: int | None = None,
    query: str = SEARCH_DEVICES,
) -> dict:
    """Search devices, following every page. Returns the total Lens found and the devices."""
    page_size = min(PAGE_SIZE, limit) if limit else PAGE_SIZE
    params: dict = {"pageSize": page_size}  # pageSize must stay the same on every page
    device_filter = _build_filter(search, model, extra)
    if device_filter:
        params["filter"] = device_filter

    devices = []
    total = None
    while True:
        data = _check_errors(execute_gql(query, {"params": params}))
        device_search = data["data"]["deviceSearch"]
        for edge in device_search["edges"]:
            devices.append(edge["node"])

        page_info = device_search.get("pageInfo") or {}
        total = page_info.get("totalCount", total)
        has_next = page_info.get("hasNextPage")
        next_token = page_info.get("nextToken")

        if limit and len(devices) >= limit:
            break
        if has_next and next_token:
            params["nextToken"] = next_token
            continue
        break

    devices = _merge_duplicates(devices)
    if limit:
        devices = devices[:limit]
    return {
        "total": total if total is not None else len(devices),
        "returned": len(devices),
        "devices": devices,
    }


def room_health(
    search: str = "",
    status: str = "",
    include_inactive: bool = False,
    inactive_only: bool = False,
) -> dict:
    """One entry per room: its primary device, its peripherals, and its status.
        down       primary device offline
        degraded   primary device online, a peripheral offline within LAB_STALE_DAYS
        unknown    Lens didn't report the primary device's state
        healthy    everything online
    A primary device offline longer than LAB_STALE_DAYS is marked inactive (likely unplugged
    or retired); those rooms are left out unless include_inactive=True. inactive_only=True
    returns just those. A peripheral offline that long goes to peripherals_to_review."""
    result = search_devices(
        search=search, extra=ROOM_PRIMARIES, query=SEARCH_ROOM_DEVICES
    )

    # look up a device by any of its ids (merged duplicates have several)
    devices_by_id = {}
    for device in result["devices"]:
        for device_id in device.get("ids") or [device["id"]]:
            devices_by_id[device_id] = device

    cutoff = None
    if STALE_DAYS > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)

    rooms = []
    seen = set()
    for device in devices_by_id.values():
        if device["id"] in seen:
            continue
        seen.add(device["id"])

        # this device's linked devices, minus Lens Desktop records and its own duplicate records
        own_ids = set(device.get("ids") or [device["id"]])
        links = []
        for link in device.get("connections") or []:
            if (
                link.get("hardwareModel") in IGNORE_LINKED_MODELS
                or link.get("id") in own_ids
            ):
                continue
            links.append(_fill_missing(link, devices_by_id))

        # linked to a higher-ranked device? then it's a peripheral of that room, not a room itself
        rank = PRIMARY_RANK.get(device.get("hardwareFamily"), 0)
        if any(
            PRIMARY_RANK.get(link.get("hardwareFamily"), 0) > rank for link in links
        ):
            continue

        offline_peripherals = []
        peripherals_to_review = []
        unknown_peripherals = []
        for link in links:
            if link.get("connected") is None:
                unknown_peripherals.append(_name_of(link))
            elif link.get("connected") is False and _is_stale(link, cutoff):
                peripherals_to_review.append(_peripheral_summary(link))
            elif link.get("connected") is False:
                offline_peripherals.append(_peripheral_summary(link))

        if device.get("connected") is False:
            room_status = "down"
        elif device.get("connected") is None:
            room_status = "unknown"
        elif offline_peripherals:
            room_status = "degraded"
        else:
            room_status = "healthy"

        primary_device = _short(device)
        primary_device["firmware"] = device.get("softwareVersion")
        primary_device["inactive"] = device.get("connected") is False and _is_stale(
            device, cutoff
        )
        if device.get("ids"):
            primary_device["all_ids"] = device["ids"]

        rooms.append(
            {
                "status": room_status,
                "room": (device.get("room") or {}).get("name"),
                "site": (device.get("site") or {}).get("name"),
                "primary_device": primary_device,
                "peripherals": [_short(link) for link in links],
                "offline_peripherals": offline_peripherals,
                "unknown_peripherals": unknown_peripherals,
                "peripherals_to_review": peripherals_to_review,
            }
        )

    # worst first: down, degraded, unknown, healthy; inactive rooms last
    status_order = {"down": 0, "degraded": 1, "unknown": 2, "healthy": 3}
    rooms.sort(
        key=lambda room: (
            room["primary_device"]["inactive"],
            status_order[room["status"]],
            room["room"] or "~",
            room["primary_device"]["name"] or "",
        )
    )

    inactive_count = sum(1 for room in rooms if room["primary_device"]["inactive"])
    if inactive_only:
        rooms = [room for room in rooms if room["primary_device"]["inactive"]]
    elif not include_inactive:
        rooms = [room for room in rooms if not room["primary_device"]["inactive"]]

    summary = dict(Counter(room["status"] for room in rooms))
    review_count = sum(1 for room in rooms if room["peripherals_to_review"])
    if status:
        rooms = [room for room in rooms if room["status"] == status.lower()]

    return {
        "summary": summary,
        "rooms_with_peripherals_to_review": review_count,
        "rooms_with_inactive_primary_device": inactive_count,
        "inactive_included": include_inactive or inactive_only,
        "stale_days": STALE_DAYS,
        "returned": len(rooms),
        "rooms": rooms,
    }


def get_device_details(device_id: str) -> dict:
    """Full detail for one device (raw response, including any errors)."""
    return execute_gql(GET_DEVICE_DETAILS, {"deviceId": device_id})


def reboot_variables(device_id: str, include_linked: bool = False) -> dict:
    return {"deviceId": device_id, "includeLinkedDevices": include_linked}


def send_reboot(device_id: str, include_linked: bool = False) -> dict:
    """Send the reboot mutation. The safety rules live in the MCP server, not here."""
    return execute_gql(REBOOT_DEVICE, reboot_variables(device_id, include_linked))


# -- RUN DIRECTLY (Module 1)


def show_token():
    token = get_token()
    print(f"Got an access token: {token[:12]}...  (mock mode: {MOCK})")


def show_operations():
    schema = execute_gql(LIST_OPERATIONS)["data"]["__schema"]
    counts = {}
    for kind, label in (
        ("queryType", "QUERY (read data)"),
        ("mutationType", "MUTATION (change data)"),
    ):
        fields = (schema.get(kind) or {}).get("fields") or []
        counts[kind] = len(fields)
        print(f"\n{label}:")
        for field in fields:
            description = (field.get("description") or "").strip().splitlines()
            first_line = description[0][:80] if description else ""
            print(f"  - {field['name']}: {first_line}")

    console.print(
        f"\n[green]{counts['queryType']} queries[/green] (read data) and "
        f"[magenta]{counts['mutationType']} mutations[/magenta] (change data)\n"
        "Nobody wrote this list: the Lens API produced it from its own schema. Scroll up to see them all."
    )


def show_counts():
    for label, extra in (
        ("all devices", None),
        ("room devices", ROOM_GEAR),
        ("offline room devices", [ROOM_GEAR, OFFLINE]),
    ):
        total = search_devices(extra=extra, limit=1)["total"]
        print(f"{label:22} {total}")


def show_rooms(option: str = ""):
    result = room_health(
        include_inactive=option == "all", inactive_only=option == "inactive"
    )
    print(result["summary"])

    if result["stale_days"] > 0:
        days = result["stale_days"]
        shown = "shown" if result["inactive_included"] else "hidden; add 'all' to show"
        print(
            f"{result['rooms_with_inactive_primary_device']} room(s) with a primary device offline more than {days} days ({shown})"
        )
        print(
            f"{result['rooms_with_peripherals_to_review']} room(s) with peripherals offline more than {days} days (REVIEW)"
        )

    print()
    print(
        f"{'STATUS':9} {'ROOM':26} {'PRIMARY DEVICE':34} {'OFFLINE PERIPHERALS':40} {'REVIEW'}"
    )
    for room in result["rooms"]:
        primary_device = room["primary_device"]
        room_name = room["room"] or "-"

        device_name = (primary_device["name"] or "")[:22]
        if primary_device["connected"] is False:
            device_name += f" ({_offline_for(primary_device['last_seen'])})"
        if primary_device["inactive"]:
            device_name += " inactive"

        offline = ", ".join(
            f"{peripheral['name']} ({peripheral['offline_for']})"
            for peripheral in room["offline_peripherals"]
        )
        review = ", ".join(
            f"{peripheral['name']} ({peripheral['offline_for']})"
            for peripheral in room["peripherals_to_review"]
        )
        print(
            f"{room['status']:9} {room_name[:26]:26} {device_name:34} {offline[:40]:40} {review}"
        )


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "token"
    if command == "token":
        show_token()
    elif command == "ops":
        show_operations()
    elif command == "count":
        show_counts()
    elif command == "rooms":
        show_rooms(sys.argv[2].lower() if len(sys.argv) > 2 else "")
    else:
        # anything else is sent to Lens as a GraphQL query
        print(json.dumps(execute_gql(command), indent=2))
