"""
lab_settings.py » reads your settings from .env

Each line in .env is KEY=VALUE. Lines starting with # are comments.
A setting typed before a command wins over .env, for that one command:
    LAB_MODE=live python mcp_client_you_pick.py reboot_device device_id=dev-006 confirm=true
"""

import os
from pathlib import Path


def load(file_name: str = ".env") -> None:
    env_file = Path(__file__).with_name(file_name)
    if not env_file.exists():
        return

    for line in env_file.read_text().splitlines():
        line = line.strip()

        # skip blank lines, comments and anything that isn't KEY=VALUE
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        if value[:1] in ('"', "'"):
            # quoted value » keep everything between the quotes
            quote = value[0]
            value = value[1:].split(quote, 1)[0]
        else:
            # unquoted value » drop a trailing " # comment"
            value = value.split(" #", 1)[0].strip()

        # don't overwrite a setting that's already set (e.g. typed before the command)
        os.environ.setdefault(key, value)
