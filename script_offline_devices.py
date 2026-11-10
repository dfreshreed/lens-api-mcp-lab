"""
script_offline_devices.py » Module 2: the developer way

A script with fixed steps, written by a person:
    1. ask Lens for all room gear (codecs, video bars, Trios, touch controllers, cameras, mics)
    2. keep the devices that aren't connected
    3. print a table

Same steps, every time. A new question (e.g. "which rooms are on old firmware?") means new code.

Run:  python script_offline_devices.py

Exercise: add a FIRMWARE column to the table (see the two comments in the print section below).
"""

from lens_api_helper import ROOM_GEAR, search_devices


def get_offline_devices() -> list[dict]:
    """Return room gear that is currently offline."""
    # fetch all room gear, then filter here: duplicate records are merged first,
    # so a USB device that's online in one record isn't counted as offline
    result = search_devices(extra=ROOM_GEAR)

    offline_devices = []
    for device in result["devices"]:
        if device.get("connected") is not False:
            continue
        offline_devices.append({
            "id": device.get("id"),
            "name": device.get("displayName"),
            "model": device.get("hardwareModel"),
            "room": (device.get("room") or {}).get("name"),
            "site": (device.get("site") or {}).get("name"),
            "firmware": device.get("softwareVersion"),
            "last_seen": device.get("lastDetected"),
        })
    return offline_devices


if __name__ == "__main__":
    offline_devices = get_offline_devices()
    print(f"{len(offline_devices)} offline room device(s)\n")

    # column headers: 'NAME':width  » exercise: add {'FIRMWARE':16} before {'LAST SEEN'}
    print(f"{'NAME':30} {'MODEL':24} {'ROOM':20} {'LAST SEEN'}")

    for device in offline_devices:
        name = (device["name"] or "")[:30]
        model = (device["model"] or "")[:24]
        room = (device["room"] or "")[:20]
        # exercise: add {device['firmware'] or '':16} before {device['last_seen']}
        print(f"{name:30} {model:24} {room:20} {device['last_seen']}")
