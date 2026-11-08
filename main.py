"""OpenShock plugin for OSC-DreamChatbox.

Talks to the official OpenShock HTTP API (v2 control endpoint).
All network work is done on a background thread so the GUI never freezes.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

_api = None
_lock = threading.Lock()

# Live state written by worker threads, read by get_values / get_text
_state: dict[str, Any] = {
    "status": "not configured",       # Connected / Offline / Error / …
    "last": "",                       # e.g. "Vibrate 30% 1000ms"
    "intensity": None,
    "shocker_name": "",
    "shocker_id": "",
    "error": "",
    "shockers": [],                   # list of {"id":…, "name":…}
    "last_ok_at": 0.0,
}

_USER_AGENT_DEFAULT = "OSC-DreamChatbox-OpenShock/1.0"


# ------------------------------------------------------------------ helpers

def _log(msg: str) -> None:
    if _api is not None:
        try:
            _api.log(f"[openshock] {msg}")
        except Exception:
            pass


def _set_status(text: str, error: str = "") -> None:
    with _lock:
        _state["status"] = text
        _state["error"] = error
    if _api is not None and _api.supports("api.set"):
        try:
            _api.set("status_label", text if not error else f"{text}: {error}")
        except Exception:
            pass


def _headers(token: str, user_agent: str) -> dict[str, str]:
    return {
        "Open-Shock-Token": token.strip(),
        "User-Agent": (user_agent or _USER_AGENT_DEFAULT).strip() or _USER_AGENT_DEFAULT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _request(
    method: str,
    url: str,
    token: str,
    user_agent: str,
    body: Optional[dict] = None,
    timeout: float = 12.0,
) -> tuple[int, Any]:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=_headers(token, user_agent),
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            code = resp.getcode()
            if not raw:
                return code, None
            try:
                return code, json.loads(raw.decode("utf-8"))
            except Exception:
                return code, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(e)
        raise RuntimeError(f"HTTP {e.code}: {detail[:300]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network: {e.reason}") from e


def _base_url() -> str:
    if _api is None:
        return "https://api.openshock.app"
    u = str(_api.get("base_url") or "https://api.openshock.app").rstrip("/")
    return u or "https://api.openshock.app"


def _token() -> str:
    if _api is None:
        return ""
    return str(_api.get("api_token") or "").strip()


def _ua() -> str:
    if _api is None:
        return _USER_AGENT_DEFAULT
    return str(_api.get("user_agent") or _USER_AGENT_DEFAULT).strip() or _USER_AGENT_DEFAULT


def _current_shocker_id() -> str:
    if _api is None:
        return ""
    # Prefer the free-text field; choice is a convenience
    sid = str(_api.get("shocker_id") or "").strip()
    if sid:
        return sid
    choice = str(_api.get("shocker_choice") or "").strip()
    return choice


# ------------------------------------------------------------------ API calls (background)

def _fetch_shockers() -> None:
    token = _token()
    if not token:
        _set_status("not configured", "no API token")
        return
    try:
        code, data = _request(
            "GET",
            f"{_base_url()}/1/shockers/own",
            token,
            _ua(),
        )
        # Response shapes vary a bit across API versions; be tolerant
        items: list[dict] = []
        if isinstance(data, dict):
            raw = data.get("data") or data.get("shockers") or data.get("ownShockers") or []
            if isinstance(raw, list):
                for entry in raw:
                    if not isinstance(entry, dict):
                        continue
                    # nested under device sometimes
                    if "shockers" in entry and isinstance(entry["shockers"], list):
                        for s in entry["shockers"]:
                            if isinstance(s, dict) and s.get("id"):
                                items.append({
                                    "id": str(s["id"]),
                                    "name": str(s.get("name") or s.get("id") or "?"),
                                })
                    elif entry.get("id"):
                        items.append({
                            "id": str(entry["id"]),
                            "name": str(entry.get("name") or entry.get("id") or "?"),
                        })
        elif isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict) and entry.get("id"):
                    items.append({
                        "id": str(entry["id"]),
                        "name": str(entry.get("name") or entry.get("id") or "?"),
                    })

        with _lock:
            _state["shockers"] = items
            _state["last_ok_at"] = time.time()

        if not items:
            _set_status("Connected (no shockers)")
            _log("token works but no shockers returned")
            return

        # Update the choice dropdown if the app supports live settings mutation
        choices = [{"value": "", "label": "(none)"}]
        for s in items:
            short = s["id"][:8]
            choices.append({"value": s["id"], "label": f"{s['name']} ({short}…)"})

        if _api is not None and _api.supports("api.set"):
            # The schema choice options are static in plugin.json;
            # we still write the first/selected id into the text field.
            current = _current_shocker_id()
            if not current and items:
                _api.set("shocker_id", items[0]["id"])
                with _lock:
                    _state["shocker_id"] = items[0]["id"]
                    _state["shocker_name"] = items[0]["name"]
            else:
                # keep name in sync
                for s in items:
                    if s["id"] == current:
                        with _lock:
                            _state["shocker_id"] = s["id"]
                            _state["shocker_name"] = s["name"]
                        break

        _set_status(f"Connected · {len(items)} shocker(s)")
        _log(f"fetched {len(items)} shockers")
    except Exception as e:
        _set_status("Error", str(e))
        _log(f"refresh failed: {e}")


def _send_control(control_type: str, intensity: int, duration_ms: int) -> None:
    token = _token()
    sid = _current_shocker_id()
    if not token:
        _set_status("Error", "no API token")
        return
    if not sid:
        _set_status("Error", "no shocker selected")
        return

    intensity = max(0, min(100, int(intensity)))
    duration_ms = max(300, min(65535, int(duration_ms)))

    body = {
        "shocks": [
            {
                "id": sid,
                "type": control_type,          # Shock | Vibrate | Sound | Stop
                "intensity": intensity if control_type != "Stop" else 0,
                "duration": duration_ms if control_type != "Stop" else 300,
                "exclusive": False,
            }
        ],
        "customName": "OSC-DreamChatbox",
    }

    try:
        code, _ = _request(
            "POST",
            f"{_base_url()}/2/shockers/control",
            token,
            _ua(),
            body=body,
        )
        label = f"{control_type} {intensity}% {duration_ms}ms"
        if control_type == "Stop":
            label = "Stop"
        with _lock:
            _state["last"] = label
            _state["intensity"] = intensity if control_type != "Stop" else None
            _state["last_ok_at"] = time.time()
            _state["error"] = ""
            if _state["status"].startswith("Error") or _state["status"] == "not configured":
                _state["status"] = "Connected"
        if _api is not None and _api.supports("api.set"):
            try:
                _api.set("status_label", f"OK · {label}")
            except Exception:
                pass
        _log(f"sent {label} → {sid[:8]}… (HTTP {code})")
    except Exception as e:
        _set_status("Error", str(e))
        _log(f"control failed: {e}")


def _run_bg(fn, *args) -> None:
    t = threading.Thread(target=fn, args=args, daemon=True, name="openshock-bg")
    t.start()


# ------------------------------------------------------------------ plugin hooks

def setup(api):
    global _api
    _api = api
    api.log("OpenShock plugin loaded")
    api.ensure_data_dir()
    if _token():
        _set_status("connecting…")
        _run_bg(_fetch_shockers)
    else:
        _set_status("not configured")


def teardown():
    global _api
    if _api is not None and _api.supports("api.set"):
        try:
            _api.set("status_label", "stopped")
        except Exception:
            pass
    _api = None


def on_settings(options):
    # When the user pastes a token or changes the base URL, refresh.
    if not _token():
        _set_status("not configured")
        return
    # Only auto-refresh when the token / base_url actually changed
    # (on_settings fires for every key). We keep it simple and let the
    # Refresh button be the explicit path; still sync the choice → id.
    choice = str(options.get("shocker_choice") or "").strip()
    if choice and choice != str(options.get("shocker_id") or "").strip():
        if _api is not None and _api.supports("api.set"):
            try:
                _api.set("shocker_id", choice)
            except Exception:
                pass
        with _lock:
            _state["shocker_id"] = choice
            for s in _state["shockers"]:
                if s["id"] == choice:
                    _state["shocker_name"] = s["name"]
                    break


def on_tick():
    # cheap; no network here
    pass


def get_values():
    with _lock:
        status = _state["status"]
        last = _state["last"] or None
        intensity = _state["intensity"]
        name = _state["shocker_name"] or (_state["shocker_id"][:8] + "…" if _state["shocker_id"] else None)
    return {
        "status": status,
        "last": last,
        "intensity": str(intensity) if intensity is not None else None,
        "shocker": name,
    }


def get_text():
    if _api is None:
        return ""
    with _lock:
        status = _state["status"]
        last = _state["last"]
        offline = status in ("not configured", "Offline", "Error") or status.startswith("Error")
    if offline and not _api.get("show_when_offline", False):
        return ""
    prefix = str(_api.get("prefix") or "⚡").strip()
    parts = [p for p in (prefix, status) if p]
    if last:
        parts.append(f"· {last}")
    return " ".join(parts)


def on_action(key: str) -> str:
    if key == "btn_refresh":
        if not _token():
            return "set an API token first"
        _set_status("refreshing…")
        _run_bg(_fetch_shockers)
        return "refreshing…"

    intensity = 30
    duration = 1000
    if _api is not None:
        try:
            intensity = int(_api.get("intensity") or 30)
        except Exception:
            intensity = 30
        try:
            duration = int(_api.get("duration_ms") or 1000)
        except Exception:
            duration = 1000

    mapping = {
        "btn_vibrate": "Vibrate",
        "btn_shock": "Shock",
        "btn_sound": "Sound",
        "btn_stop": "Stop",
    }
    if key not in mapping:
        return ""
    control = mapping[key]
    if not _token():
        return "no API token"
    if not _current_shocker_id() and control != "Stop":
        return "select a shocker first"
    _run_bg(_send_control, control, intensity, duration)
    return f"sending {control}…"


def on_event(name, data=None):
    if name == "app.shutdown":
        pass
