"""
host_gateway.py - a small OpenAI-compatible LLM gateway for the lab (the lab host runs it).

Participants' mcp_client_ai_picks.py talks to this gateway instead of a model provider. The gateway:
  - holds the ONE provider API key (it never reaches a participant's Codespace)
  - gives every participant their own lab key, so one person can be switched off
  - pins the model, whatever the client asks for, and caps the answer length
  - limits requests per key per minute and per day, and total tokens for the whole lab
  - logs who made each request and how many tokens it used (never the prompt text)

This is a miniature of the "AI gateway" an enterprise puts in front of its models.

Commands
    python host_gateway.py keys 50                # 50 seat keys (seat-01 ... seat-50); see below
    python host_gateway.py keys names.txt         # or one key per name (one name per line)
    python host_gateway.py                        # run the gateway (PORT, default 8080)

The keys command writes three files (all git-ignored, never commit them):
    gateway.env          the gateway's settings: lab keys, admin key, provider settings to fill in
    keys.csv             name,key (for your records or a mail merge)
    seat_cards.html      printable cards, one per seat (open in a browser, print)

Endpoints
    POST /v1/chat/completions    lab key required (Authorization: Bearer <lab key>); also /chat/completions
    GET  /v1/models              lab key required; shows the pinned model
    GET  /health                 no key; is the gateway up and open?
    GET  /admin/usage            LAB_ADMIN_KEY required; usage per participant + budget left

Settings (read from gateway.env, then .env, then the environment)
    UPSTREAM_BASE_URL      provider's OpenAI-compatible URL (default https://api.anthropic.com/v1)
    UPSTREAM_API_KEY       provider API key (secret)
    UPSTREAM_MODEL         model used for every request (default claude-haiku-5-5)
    UPSTREAM_HEADERS       optional JSON of extra headers for the provider, e.g. {"anthropic-workspace-id": "..."}
    LAB_KEYS               comma list of name:key pairs (printed by the `keys` command)
    LAB_KEYS_FILE          or: path to keys.csv (name,key) inside the container
    LAB_ADMIN_KEY          key for /admin/usage
    LAB_RPM                requests per minute per lab key (default 20; one question can use 2-8)
    LAB_DAILY_REQUESTS     requests per lab key per UTC day (default 300)
    LAB_MAX_TOKENS         longest answer allowed, in tokens (default 1500)
    LAB_TOKEN_BUDGET       total tokens (input + output) for everyone; 0 = no cap (default 20000000)
    LAB_GATEWAY_OPEN       0 = every lab key is refused (instant off switch); default 1
    LAB_HOST_NAME          name in "tell ..." messages (default "the lab host")
    PORT                   default 8080

Counters live in memory: restarting the gateway resets the per-day counts and the token budget.
"""

import csv
import json
import os
import secrets
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import lab_settings

lab_settings.load(
    "gateway.env"
)  # gateway settings (written by `python host_gateway.py keys ...`)
lab_settings.load()  # the lab's .env

UPSTREAM_BASE_URL = os.getenv(
    "UPSTREAM_BASE_URL", "https://api.anthropic.com/v1"
).rstrip("/")
UPSTREAM_API_KEY = os.getenv("UPSTREAM_API_KEY", "")
UPSTREAM_MODEL = os.getenv("UPSTREAM_MODEL", "claude-haiku-5-5")
UPSTREAM_HEADERS = json.loads(os.getenv("UPSTREAM_HEADERS") or "{}")
ADMIN_KEY = os.getenv("LAB_ADMIN_KEY", "")
RPM = int(os.getenv("LAB_RPM", "20"))
DAILY = int(os.getenv("LAB_DAILY_REQUESTS", "300"))
MAX_TOKENS = int(os.getenv("LAB_MAX_TOKENS", "1500"))
BUDGET = int(os.getenv("LAB_TOKEN_BUDGET", "20000000"))
OPEN = os.getenv("LAB_GATEWAY_OPEN", "1") != "0"
HOST = os.getenv(
    "LAB_HOST_NAME", "the lab host"
)  # the person who made this and is running the lab

# Only these request fields are passed to the provider; everything else is dropped.
PASS_THROUGH = {
    "messages",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "temperature",
    "top_p",
    "stop",
}


def load_keys() -> dict[str, str]:
    """Return {lab_key: participant_name} from LAB_KEYS and/or LAB_KEYS_FILE."""
    keys = {}
    for item in os.getenv("LAB_KEYS", "").split(","):
        name, _, key = item.strip().partition(":")
        if key:
            keys[key.strip()] = name.strip()
    path = os.getenv("LAB_KEYS_FILE")
    if path and os.path.exists(path):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                keys[row["key"].strip()] = row["name"].strip()
    return keys


KEYS = load_keys()


# --- usage tracking (in memory) ------------------------------------------------
class Usage:
    def __init__(self):
        self.recent = defaultdict(deque)  # name -> timestamps in the last minute
        self.day_count = defaultdict(int)  # (name, UTC date) -> requests that day
        self.tokens = defaultdict(int)  # name -> total tokens
        self.requests = defaultdict(int)  # name -> total requests
        self.last = {}  # name -> last request time
        self.total_tokens = 0

    def check(self, name: str) -> str | None:
        """Return a reason to refuse, or None if this request may go ahead."""
        now = time.time()
        q = self.recent[name]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RPM:
            return f"Rate limit: {RPM} requests per minute. Wait {int(61 - (now - q[0]))} seconds."
        today = datetime.now(timezone.utc).date().isoformat()
        day_key = (name, today)
        if self.day_count[day_key] >= DAILY:
            return f"Daily limit reached ({DAILY} requests). Pair up with a neighbour for the rest of the lab."
        if BUDGET and self.total_tokens >= BUDGET:
            return f"The lab's AI budget is used up. Tell {HOST}."
        q.append(now)
        self.day_count[day_key] += 1
        self.requests[name] += 1
        self.last[name] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return None

    def add_tokens(self, name: str, n: int):
        self.tokens[name] += n
        self.total_tokens += n


USAGE = Usage()


def _error(status: int, message: str, kind: str):
    """OpenAI-style error body, so the OpenAI SDK in mcp_client_ai_picks.py raises the right exception."""
    from starlette.responses import JSONResponse

    return JSONResponse(
        {"error": {"message": message, "type": kind, "code": kind}}, status_code=status
    )


def _bearer(request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def _log(**fields):
    print(
        json.dumps(
            {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **fields}
        ),
        flush=True,
    )


# --- endpoints -----------------------------------------------------------------
async def chat_completions(request):
    import httpx
    from starlette.responses import JSONResponse

    name = KEYS.get(_bearer(request))
    if not name:
        return _error(
            401,
            "Unknown lab key. Check LAB_LLM_API_KEY in your .env file.",
            "invalid_api_key",
        )
    if not OPEN:
        return _error(403, "The lab gateway is closed.", "gateway_closed")
    reason = USAGE.check(name)
    if reason:
        status = 429 if not reason.startswith("The lab's AI budget") else 403
        _log(who=name, status=status, refused=reason)
        return _error(
            status,
            reason,
            "rate_limit_exceeded" if status == 429 else "budget_exhausted",
        )

    try:
        body = await request.json()
    except ValueError:
        return _error(400, "Request body must be JSON.", "invalid_request")
    if body.get("stream"):
        return _error(
            400, "Streaming is not supported by the lab gateway.", "invalid_request"
        )
    upstream = {k: v for k, v in body.items() if k in PASS_THROUGH}
    upstream["model"] = UPSTREAM_MODEL
    upstream["max_tokens"] = min(int(body.get("max_tokens") or MAX_TOKENS), MAX_TOKENS)

    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{UPSTREAM_BASE_URL}/chat/completions",
                json=upstream,
                headers={
                    "Authorization": f"Bearer {UPSTREAM_API_KEY}",
                    **UPSTREAM_HEADERS,
                },
            )
    except httpx.HTTPError as e:
        _log(who=name, status=502, error=type(e).__name__)
        return _error(
            502,
            "The gateway could not reach the model provider. Try again in a minute.",
            "upstream_unreachable",
        )

    try:
        data = r.json()
    except ValueError:
        _log(
            who=name, status=502, upstream_status=r.status_code, error="non-JSON reply"
        )
        return _error(
            502,
            f"The model provider sent an unexpected reply. Tell {HOST}.",
            "upstream_bad_reply",
        )

    usage = data.get("usage") or {}
    used = int(
        usage.get("total_tokens")
        or (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
    )
    USAGE.add_tokens(name, used)
    choice = (data.get("choices") or [{}])[0].get("message") or {}
    _log(
        who=name,
        status=r.status_code,
        tokens=used,
        tool_calls=len(choice.get("tool_calls") or []),
        ms=int((time.time() - started) * 1000),
    )
    if r.status_code in (
        401,
        403,
    ):  # the provider rejected the GATEWAY's key, not the participant's
        return _error(
            502,
            f"The model provider rejected the lab gateway. Tell {HOST}.",
            "upstream_auth",
        )
    if r.status_code == 429:  # provider-side limit, not this participant's
        return _error(
            503,
            "The model provider is busy. Wait a minute and try again.",
            "upstream_busy",
        )
    if (
        r.status_code >= 400
    ):  # pass the provider's error message through, but never its headers
        return JSONResponse(
            data, status_code=r.status_code if r.status_code < 500 else 502
        )
    return JSONResponse(data)


async def models(request):
    from starlette.responses import JSONResponse

    if not KEYS.get(_bearer(request)):
        return _error(401, "Unknown lab key.", "invalid_api_key")
    return JSONResponse(
        {"object": "list", "data": [{"id": UPSTREAM_MODEL, "object": "model"}]}
    )


async def health(request):
    from starlette.responses import JSONResponse

    return JSONResponse(
        {
            "ok": bool(UPSTREAM_BASE_URL and UPSTREAM_API_KEY and UPSTREAM_MODEL),
            "open": OPEN,
            "lab_keys": len(KEYS),
        }
    )


async def admin_usage(request):
    from starlette.responses import JSONResponse

    if not ADMIN_KEY or _bearer(request) != ADMIN_KEY:
        return _error(401, "Admin key required.", "invalid_api_key")
    people = sorted(set(KEYS.values()))
    return JSONResponse(
        {
            "model": UPSTREAM_MODEL,
            "open": OPEN,
            "token_budget": BUDGET or None,
            "tokens_used": USAGE.total_tokens,
            "budget_left": (BUDGET - USAGE.total_tokens) if BUDGET else None,
            "people_active": sum(1 for p in people if USAGE.requests[p]),
            "per_person": {
                p: {
                    "requests": USAGE.requests[p],
                    "tokens": USAGE.tokens[p],
                    "last": USAGE.last.get(p),
                }
                for p in people
                if USAGE.requests[p]
            },
        }
    )


def build_app():
    from starlette.applications import Starlette
    from starlette.routing import Route

    return Starlette(
        routes=[
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
            Route(
                "/chat/completions", chat_completions, methods=["POST"]
            ),  # base URL entered without /v1
            Route("/v1/models", models, methods=["GET"]),
            Route("/models", models, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Route("/admin/usage", admin_usage, methods=["GET"]),
        ]
    )


# --- key generation --------------------------------------------------------------
_ALPHABET = (
    "23456789abcdefghjkmnpqrstuvwxyz"  # no 0/o, 1/l/i: easy to type from a printed card
)


def _new_key() -> str:
    groups = ["".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "lab-" + "-".join(groups)  # e.g. lab-7kq3-m9xp-4hfw


GATEWAY_ENV_TEMPLATE = """# Lab gateway settings. Never commit this file.
# Provider (Anthropic API, OpenAI-compatible endpoint). Paste your workspace API key below.
UPSTREAM_BASE_URL=https://api.anthropic.com/v1
UPSTREAM_API_KEY=
UPSTREAM_MODEL=claude-haiku-5-5
# Limits (defaults shown)
LAB_RPM=20
LAB_DAILY_REQUESTS=300
LAB_MAX_TOKENS=1500
LAB_TOKEN_BUDGET=20000000
LAB_GATEWAY_OPEN=1
# Name shown in messages that ask participants for help
LAB_HOST_NAME=Daniel
"""


def _cards_html(rows: list[tuple[str, str]]) -> str:
    cards = "\n".join(
        f'<div class="card"><div class="seat">{n}</div><div class="key">{k}</div>'
        f"<ol><li>Open the file <b>.env</b></li><li>Put the key after <b>LAB_LLM_API_KEY=</b></li>"
        f"<li>Save (Ctrl/Cmd+S)</li></ol></div>"
        for n, k in rows
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Lab seat cards</title>
<style>body{{font-family:Arial,sans-serif;margin:12mm}} .grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:6mm}}
.card{{border:1px dashed #888;padding:5mm;break-inside:avoid}} .seat{{font-size:11pt;color:#555}}
.key{{font-family:Consolas,Menlo,monospace;font-size:15pt;font-weight:bold;margin:2mm 0;white-space:nowrap}}
ol{{margin:0;padding-left:5mm;font-size:9pt}}</style></head>
<body><div class="grid">{cards}</div></body></html>"""


def make_keys(arg: str):
    if arg.isdigit():
        names = [f"seat-{i:02d}" for i in range(1, int(arg) + 1)]
    else:
        with open(arg) as f:
            names = [line.strip() for line in f if line.strip()]
    if any("," in n or ":" in n for n in names):
        raise SystemExit("Names cannot contain commas or colons.")
    rows = [(n, _new_key()) for n in names]
    admin = "admin-" + secrets.token_urlsafe(24)
    here = os.path.dirname(os.path.abspath(__file__))

    with open(os.path.join(here, "keys.csv"), "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["name", "key"])
        w.writerows(rows)
    with open(os.path.join(here, "seat_cards.html"), "w") as f:
        f.write(_cards_html(rows))

    env_path = os.path.join(here, "gateway.env")
    lines = (
        open(env_path).read().splitlines()
        if os.path.exists(env_path)
        else GATEWAY_ENV_TEMPLATE.splitlines()
    )
    lines = [ln for ln in lines if not ln.startswith(("LAB_KEYS=", "LAB_ADMIN_KEY="))]
    lines += [
        "LAB_KEYS=" + ",".join(f"{n}:{k}" for n, k in rows),
        f"LAB_ADMIN_KEY={admin}",
    ]
    with open(env_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Created {len(rows)} keys.")
    print(
        "  gateway.env      lab keys + admin key added (put your provider API key in UPSTREAM_API_KEY)"
    )
    print("  keys.csv         name,key")
    print(
        "  seat_cards.html  printable cards (right-click > Download, open in a browser, print)"
    )
    print(f"\nYour admin key (for /admin/usage): {admin}")
    print("Restart the gateway to load new keys. Never commit these files.")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "keys":
        make_keys(sys.argv[2])
    else:
        if not os.path.exists(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway.env")
        ):
            raise SystemExit(
                "No gateway.env yet. First run: python host_gateway.py keys 50\n"
                "That creates gateway.env; then put your provider API key in UPSTREAM_API_KEY and start again."
            )
        if not UPSTREAM_API_KEY:
            raise SystemExit(
                "Put your provider API key in UPSTREAM_API_KEY in gateway.env, then start again."
            )
        if not KEYS:
            raise SystemExit("No lab keys loaded. Run: python host_gateway.py keys 50")
        import uvicorn

        print(
            f"Lab gateway: {len(KEYS)} keys, model {UPSTREAM_MODEL}, {RPM}/min, {DAILY}/day, "
            f"budget {BUDGET or 'none'} tokens, open={OPEN}",
            flush=True,
        )
        uvicorn.run(
            build_app(),
            host="0.0.0.0",
            port=int(os.getenv("PORT", "8080")),
            log_level="warning",
        )
