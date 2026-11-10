"""
lens_sample_data.py - fake Lens API responses for LENS_MOCK=1.

allows running every module before Lens credentials exist,
and is the fallback if the Lens API is unreachable on lab day.
the devices are invented sample data; their shapes follow the Lens API schema
"""

import json
from datetime import datetime, timedelta, timezone

_NOW = datetime.now(timezone.utc)


def _ago(hours: float) -> str:
    """Timestamps relative to now, so 'inactive' (offline > LAB_STALE_DAYS) stays stable over time."""
    return (_NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dev(
    id, name, model, family, connected, fw, room, site, hours_ago, serial=None, mac=None
):
    return {
        "id": id,
        "displayName": name,
        "connected": connected,
        "hardwareModel": model,
        "hardwareFamily": family,
        "softwareVersion": fw,
        "serialNumber": serial or f"8L21{id[-3:]}XYZ",
        "macAddress": mac,
        "room": {"name": room} if room else None,
        "site": {"name": site} if site else None,
        "lastDetected": _ago(hours_ago),
    }


HQ, CLT = "Greenville HQ", "Charlotte Office"
DEVICES = [
    # Conf Rm 4B: video codec with a touch controller and a camera -> healthy
    _dev(
        "dev-001",
        "Conf Rm 4B Studio X50",
        "Studio X50",
        "Studio X Family Large",
        True,
        "4.7.0.466077",
        "Conf Rm 4B",
        HQ,
        0.2,
    ),
    _dev(
        "dev-002",
        "Conf Rm 4B TC10",
        "TC10",
        "Poly Touch Controller",
        True,
        "7.0.0.7002206",
        "Conf Rm 4B",
        HQ,
        0.2,
    ),
    _dev(
        "dev-003",
        "Conf Rm 4B Studio E60",
        "Studio E60",
        "Studio E Family",
        True,
        "1.1.4.6",
        "Conf Rm 4B",
        HQ,
        0.2,
    ),
    # ...and an old TC8 still linked but offline for months -> room stays healthy, TC8 goes to review
    _dev(
        "dev-020",
        "Conf Rm 4B TC8",
        "TC8",
        "Poly Touch Controller",
        False,
        "4.1.0-211230",
        "Conf Rm 4B",
        HQ,
        24 * 120,
    ),
    # Huddle 2A: codec offline, TC8 offline -> down
    _dev(
        "dev-004",
        "Huddle 2A Studio X30",
        "Studio X30",
        "Studio X Family Small",
        False,
        "4.6.1.444241",
        "Huddle 2A",
        HQ,
        15,
    ),
    _dev(
        "dev-005",
        "Huddle 2A TC8",
        "TC8",
        "Poly Touch Controller",
        False,
        "6.4.2-7000764",
        "Huddle 2A",
        HQ,
        15,
    ),
    # Boardroom: Studio G62 online, one of two cameras offline -> degraded
    _dev(
        "dev-006",
        "Boardroom Studio G62",
        "Studio G62",
        "G7500 Family",
        True,
        "4.6.0.436380",
        "Boardroom",
        CLT,
        0.2,
    ),
    _dev(
        "dev-007",
        "Boardroom TC10",
        "TC10",
        "Poly Touch Controller",
        True,
        "6.6.0.7001439",
        "Boardroom",
        CLT,
        0.2,
    ),
    _dev(
        "dev-008",
        "Boardroom Studio E60 Left",
        "Studio E60",
        "Studio E Family",
        False,
        "1.1.4.6",
        "Boardroom",
        CLT,
        130,
    ),
    _dev(
        "dev-009",
        "Boardroom Studio E60 Right",
        "Studio E60",
        "Studio E Family",
        True,
        "1.1.4.6",
        "Boardroom",
        CLT,
        0.2,
    ),
    # Training Room MTR: PC is the hub; video bar, touch controller, Sync 60 audio; Sync 60 offline -> degraded
    _dev(
        "dev-010",
        "Training Rm MTR PC",
        "HP Mini Conferencing PC",
        "HP Conference PC",
        True,
        "1.5.147",
        "Training Room",
        CLT,
        0.2,
    ),
    _dev(
        "dev-011",
        "Training Rm Studio R30",
        "Studio R30",
        "Poly Studio R Family",
        True,
        "2.1.2.001179",
        "Training Room",
        CLT,
        0.2,
    ),
    _dev(
        "dev-012",
        "Training Rm TC10",
        "TC10",
        "Poly Touch Controller",
        True,
        "6.9.0-7002597",
        "Training Room",
        CLT,
        0.2,
    ),
    _dev(
        "dev-013",
        "Training Rm Sync 60",
        "Sync 60",
        "Sync Family",
        False,
        "0.0.1803.3702",
        "Training Room",
        CLT,
        110,
    ),
    # Focus Room 3: audio-only huddle room, Trio is the hub -> down
    _dev(
        "dev-014",
        "Focus Rm 3 Trio C60",
        "Trio C60",
        "Trio Family",
        False,
        "9.4.1.0508",
        "Focus Room 3",
        HQ,
        50,
    ),
    # Old Lab: codec offline for months -> inactive (likely retired gear)
    _dev(
        "dev-019",
        "Old Lab Studio X70",
        "Studio X70",
        "Studio X Family Large",
        False,
        "4.2.0.396113",
        "Old Lab",
        CLT,
        24 * 200,
    ),
    # Huddle 3C: one Studio USB in Lens twice (network-provisioned + via Lens Desktop) -> merged
    _dev(
        "00e0db523daa",
        "Huddle 3C Studio USB",
        "Studio USB",
        "Poly Studio USB Family",
        True,
        "2.1.2.001179",
        "Huddle 3C",
        HQ,
        0.3,
        serial="8g2034523daaf9",
        mac="00:e0:db:52:3d:aa",
    ),
    _dev(
        "8G2034523DAAF9",
        "Huddle 3C Studio USB",
        "Studio USB",
        "Poly Studio USB Family",
        False,
        "2.1.2.001179",
        None,
        None,
        400,
        serial="8G2034523DAAF9",
    ),
    # Not room gear: desk phone, personal headset, personal speakerphone, a user's Lens Desktop
    _dev(
        "dev-015",
        "Reception CCX 400",
        "CCX 400",
        "CCX Family",
        True,
        "8.1.3.1300",
        "Reception",
        HQ,
        0.2,
    ),
    _dev(
        "dev-016",
        "Voyager 5200 Series",
        "Voyager 5200 Series",
        "Voyager Family",
        False,
        "1.4.0",
        None,
        None,
        12000,
    ),
    _dev(
        "dev-017",
        "Sync 20-M",
        "Sync 20-M",
        "Sync Family",
        False,
        "0.0.1905.4704",
        None,
        None,
        3600,
    ),
    _dev(
        "dev-018",
        "Lens Desktop",
        "Desktop App",
        "App Family",
        False,
        "2.2.0.3547",
        None,
        None,
        60,
    ),
]

LINKS = [  # Lens reports every link from both ends
    ("dev-001", "dev-002"),
    ("dev-001", "dev-003"),
    ("dev-001", "dev-020"),
    ("dev-004", "dev-005"),
    ("dev-006", "dev-007"),
    ("dev-006", "dev-008"),
    ("dev-006", "dev-009"),
    ("dev-010", "dev-011"),
    ("dev-010", "dev-012"),
    ("dev-010", "dev-013"),
    ("dev-011", "dev-010"),
    ("dev-016", "dev-018"),
    ("dev-017", "dev-018"),
]

_BY_ID = {d["id"]: d for d in DEVICES}
for _d in DEVICES:
    _d["connections"] = []
for _a, _b in {tuple(sorted(p)) for p in LINKS}:
    for _x, _y in ((_a, _b), (_b, _a)):
        t = _BY_ID[_y]
        _BY_ID[_x]["connections"].append(
            {
                "id": t["id"],
                "name": t["displayName"],
                "hardwareModel": t["hardwareModel"],
                "hardwareFamily": t["hardwareFamily"],
                "connected": t["connected"],
                "lastDetected": t["lastDetected"],
            }
        )

DETAIL_EXTRA = {
    "hardwareRevision": "B",
    "internalIp": "10.20.4.17",
    "lastConfigRequestDate": _ago(6),
    "allPeripheralsLinked": True,
    "activeApplicationName": "Poly Video",
    "activeApplicationVersion": "4.7.0",
}

SCHEMA = {
    "queryType": {
        "fields": [
            {
                "name": "deviceSearch",
                "description": "Searches for devices using query parameters. Supports pagination.",
                "args": [{"name": "region"}, {"name": "params"}],
            },
            {
                "name": "device",
                "description": "Get one device by id",
                "args": [{"name": "id"}],
            },
            {"name": "rooms", "description": "List rooms", "args": []},
            {"name": "sites", "description": "List sites", "args": []},
        ]
    },
    "mutationType": {
        "fields": [
            {
                "name": "rebootDevice",
                "description": "Reboot a device",
                "args": [{"name": "deviceId"}, {"name": "includeLinkedDevices"}],
            },
            {
                "name": "updateDeviceSoftware",
                "description": "Start a software update on a device",
                "args": [{"name": "deviceId"}],
            },
        ]
    },
}

_SCALAR = lambda n: {"name": n, "kind": "SCALAR", "ofType": None}  # noqa: E731
TYPES = {
    "Device": {
        "name": "Device",
        "kind": "OBJECT",
        "description": "A device (sample subset of fields)",
        "fields": [
            {"name": k, "description": None, "type": _SCALAR(t)}
            for k, t in [
                ("id", "ID"),
                ("displayName", "String"),
                ("connected", "Boolean"),
                ("hardwareModel", "String"),
                ("hardwareFamily", "String"),
                ("softwareVersion", "String"),
                ("lastDetected", "DateTimeISO"),
                ("internalIp", "String"),
            ]
        ]
        + [
            {
                "name": "connections",
                "description": "Devices linked to this device",
                "type": {
                    "name": None,
                    "kind": "LIST",
                    "ofType": {"name": None, "kind": "NON_NULL"},
                },
            }
        ],
        "inputFields": None,
        "enumValues": None,
    },
    "Filter": {
        "name": "Filter",
        "kind": "INPUT_OBJECT",
        "description": "Narrow down which devices are returned",
        "fields": None,
        "enumValues": None,
        "inputFields": [
            {"name": k, "type": _SCALAR("String")}
            for k in [
                "field",
                "eq",
                "ne",
                "contains",
                "notContains",
                "beginsWith",
                "lt",
                "le",
                "gt",
                "ge",
                "searchTerm",
            ]
        ],
    },
}


# --- tiny Filter evaluator, so mock mode honors the same filters as Lens -------
def _value(d: dict, field: str) -> str:
    v = d.get(field)
    return "" if v is None else str(v).lower()  # booleans compare as "true"/"false"


def _match(d: dict, f: dict | None) -> bool:
    if not f:
        return True
    if "AND" in f:
        return all(_match(d, x) for x in f["AND"])
    if "OR" in f:
        return any(_match(d, x) for x in f["OR"])
    if "NOT" in f:
        return not any(_match(d, x) for x in f["NOT"])
    if f.get("searchTerm"):
        return (
            f["searchTerm"].lower()
            in json.dumps({k: v for k, v in d.items() if k != "connections"}).lower()
        )
    v = _value(d, f.get("field", ""))
    ops = {
        "eq": lambda a: v == a,
        "ne": lambda a: v != a,
        "contains": lambda a: a in v,
        "notContains": lambda a: a not in v,
        "beginsWith": lambda a: v.startswith(a),
        "lt": lambda a: v < a,
        "le": lambda a: v <= a,
        "gt": lambda a: v > a,
        "ge": lambda a: v >= a,
    }
    for op, test in ops.items():
        if f.get(op) is not None:
            return test(str(f[op]).lower())
    return True


def mock_gql(query: str, variables: dict) -> dict:
    query = query.strip()
    if query.lower().startswith("mutation"):
        return {"data": {"rebootDevice": {"success": True, "error": None}}}
    if "__schema" in query:
        return {"data": {"__schema": SCHEMA}}
    if "__type" in query:
        type_name = variables.get("typeName") or ""
        return {"data": {"__type": TYPES.get(type_name)}}
    if "deviceSearch" in query:
        params = variables.get("params") or {}
        size = params.get("pageSize") or 10
        start = int(params.get("nextToken") or 0)
        matching_devices = []
        for device in DEVICES:
            if _match(device, params.get("filter")):
                matching_devices.append(device)
        page = matching_devices[start : start + size]
        has_next_page = start + size < len(matching_devices)
        return {
            "data": {
                "deviceSearch": {
                    "edges": [{"node": d} for d in page],
                    "pageInfo": {
                        "hasNextPage": has_next_page,
                        "totalCount": len(matching_devices),
                        "countOnPage": len(page),
                        "nextToken": str(start + size) if has_next_page else None,
                    },
                }
            }
        }
    if "device(" in query:
        device = _BY_ID.get(variables.get("deviceId"))
        if not device:
            return {"data": {"device": None}}
        return {
            "data": {
                "device": {
                    **device,
                    **DETAIL_EXTRA,
                    "name": device["displayName"],
                    "macAddress": device["macAddress"] or "00:E0:DB:12:34:56",
                    "hasPeripherals": bool(device["connections"]),
                }
            }
        }
    if "__typename" in query:
        return {"data": {"__typename": "Query"}}
    return {
        "errors": [{"message": "Mock mode only supports the lab's built-in queries."}]
    }
