"""Shocker plugin for OSC-DreamChatbox – OpenShock + PiShock.

OpenShock: REST control + optional live log polling (shows who triggered actions).
PiShock: classic HTTP operate API.

All network work runs on background threads.
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

_state: dict[str, Any] = {
    "status": "not configured",
    "last": "",
    "intensity": None,
    "shocker_name": "",
    "shocker_id": "",
    "by": "",
    "error": "",
    "shockers": [],
    "last_ok_at": 0.0,
    "last_log_id": None,
}

_poll_stop = threading.Event()
_poll_thread: Optional[threading.Thread] = None

_OS_UA = "OSC-DreamChatbox-Shocker/2.0"
_TYPE_MAP = {0: "Stop", 1: "Shock", 2: "Vibrate", 3: "Sound"}


def _log(msg: str) -> None:
    if _api is not None:
        try:
            _api.log(f"[shocker] {msg}")
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


def _backend() -> str:
    if _api is None:
        return "openshock"
    return str(_api.get("backend") or "openshock").strip().lower()


def _http_json(
    method: str,
    url: str,
    headers: dict[str, str],
    body: Optional[dict] = None,
    timeout: float = 12.0,
) -> tuple[int, Any]:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
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


def _run_bg(fn, *args) -> None:
    t = threading.Thread(target=fn, args=args, daemon=True, name="shocker-bg")
    t.start()


def _os_token() -> str:
    if _api is None:
        return ""
    return str(_api.get("os_token") or "").strip()


def _os_base() -> str:
    if _api is None:
        return "https://api.openshock.app"
    u = str(_api.get("os_base_url") or "https://api.openshock.app").rstrip("/")
    return u or "https://api.openshock.app"


def _os_ua() -> str:
    if _api is None:
        return _OS_UA
    return str(_api.get("os_user_agent") or _OS_UA).strip() or _OS_UA


def _os_headers() -> dict[str, str]:
    return {
        "Open-Shock-Token": _os_token(),
        "User-Agent": _os_ua(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _os_shocker_id() -> str:
    if _api is None:
        return ""
    return str(_api.get("os_shocker_id") or "").strip()


def _os_fetch_shockers() -> None:
    token = _os_token()
    if not token:
        _set_status("not configured", "no OpenShock token")
        return
    try:
        code, data = _http_json("GET", f"{_os_base()}/1/shockers/own", _os_headers())
        items: list[dict] = []
        if isinstance(data, dict):
            raw = data.get("data") or data.get("shockers") or data.get("ownShockers") or []
            if isinstance(raw, list):
                for entry in raw:
                    if not isinstance(entry, dict):
                        continue
                    if "shockers" in entry and isinstance(entry["shockers"], list):
                        for s in entry["shockers"]:
                            if isinstance(s, dict) and s.get("id"):
                                items.append({"id": str(s["id"]), "name": str(s.get("name") or s["id"])})
                    elif entry.get("id"):
                        items.append({"id": str(entry["id"]), "name": str(entry.get("name") or entry["id"])})
        elif isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict) and entry.get("id"):
                    items.append({"id": str(entry["id"]), "name": str(entry.get("name") or entry["id"])})

        with _lock:
            _state["shockers"] = items
            _state["last_ok_at"] = time.time()

        if not items:
            _set_status("Connected (no shockers)")
            return

        current = _os_shocker_id()
        if not current and items and _api is not None and _api.supports("api.set"):
            try:
                _api.set("os_shocker_id", items[0]["id"])
            except Exception:
                pass
            current = items[0]["id"]

        name = ""
        for s in items:
            if s["id"] == current:
                name = s["name"]
                break
        with _lock:
            _state["shocker_id"] = current
            _state["shocker_name"] = name or (current[:8] + "…" if current else "")

        _set_status(f"OpenShock · {len(items)} shocker(s)")
        _log(f"OpenShock: {len(items)} shockers")
        _ensure_poll_thread()
    except Exception as e:
        _set_status("Error", str(e))
        _log(f"OpenShock refresh failed: {e}")


def _os_send(control_type: str, intensity: int, duration_ms: int) -> None:
    token = _os_token()
    sid = _os_shocker_id()
    if not token:
        _set_status("Error", "no OpenShock token")
        return
    if not sid and control_type != "Stop":
        _set_status("Error", "no shocker selected")
        return

    intensity = max(0, min(100, int(intensity)))
    duration_ms = max(300, min(65535, int(duration_ms)))

    body = {
        "shocks": [{
            "id": sid or "00000000-0000-0000-0000-000000000000",
            "type": control_type,
            "intensity": 0 if control_type == "Stop" else intensity,
            "duration": 300 if control_type == "Stop" else duration_ms,
            "exclusive": False,
        }],
        "customName": "OSC-DreamChatbox",
    }
    try:
        code, _ = _http_json("POST", f"{_os_base()}/2/shockers/control", _os_headers(), body=body)
        label = "Stop" if control_type == "Stop" else f"{control_type} {intensity}% {duration_ms}ms"
        with _lock:
            _state["last"] = label
            _state["intensity"] = None if control_type == "Stop" else intensity
            _state["by"] = "you"
            _state["last_ok_at"] = time.time()
            _state["error"] = ""
            if _state["status"].startswith("Error") or _state["status"] == "not configured":
                _state["status"] = "OpenShock · Connected"
        if _api is not None and _api.supports("api.set"):
            try:
                _api.set("status_label", f"OK · {label}")
            except Exception:
                pass
        _log(f"OpenShock sent {label} (HTTP {code})")
    except Exception as e:
        _set_status("Error", str(e))
        _log(f"OpenShock control failed: {e}")


def _os_type_name(t: Any) -> str:
    if isinstance(t, str):
        return t
    try:
        return _TYPE_MAP.get(int(t), str(t))
    except Exception:
        return str(t)


def _os_poll_logs_once() -> None:
    token = _os_token()
    if not token:
        return
    sid = _os_shocker_id()
    try:
        if sid:
            url = f"{_os_base()}/1/shockers/{sid}/logs?offset=0&limit=5"
        else:
            url = f"{_os_base()}/1/shockers/logs?page=1&pageSize=5"
        code, data = _http_json("GET", url, _os_headers(), timeout=10.0)

        entries: list[dict] = []
        if isinstance(data, dict):
            raw = data.get("data")
            if isinstance(raw, dict):
                raw = raw.get("data") or raw.get("items") or raw.get("results") or []
            if raw is None:
                raw = data.get("items") or data.get("results") or []
            if isinstance(raw, list):
                entries = [e for e in raw if isinstance(e, dict)]
        elif isinstance(data, list):
            entries = [e for e in data if isinstance(e, dict)]

        if not entries:
            return

        entry = entries[0]
        log_id = entry.get("id") or entry.get("Id")
        with _lock:
            if log_id is not None and log_id == _state.get("last_log_id"):
                return
            _state["last_log_id"] = log_id

        typ = _os_type_name(entry.get("type") or entry.get("Type"))
        intensity = entry.get("intensity") if entry.get("intensity") is not None else entry.get("Intensity")
        duration = entry.get("duration") if entry.get("duration") is not None else entry.get("Duration")
        controlled = entry.get("controlledBy") or entry.get("ControlledBy") or {}
        if not isinstance(controlled, dict):
            controlled = {}
        name = controlled.get("name") or controlled.get("Name") or "Guest"
        custom = controlled.get("customName") or controlled.get("CustomName")
        who = str(custom).strip() if custom else str(name).strip() or "someone"

        parts = [typ]
        if intensity is not None and typ not in ("Stop", "Sound", "Beep"):
            parts.append(f"{intensity}%")
        if duration is not None:
            try:
                d = int(duration)
                if d >= 1000:
                    parts.append(f"{d}ms" if d % 1000 else f"{d // 1000}s")
                else:
                    parts.append(f"{d}ms")
            except Exception:
                parts.append(str(duration))
        parts.append(f"by {who}")
        label = " ".join(parts)

        with _lock:
            _state["last"] = label
            _state["by"] = who
            try:
                _state["intensity"] = int(intensity) if intensity is not None else None
            except Exception:
                _state["intensity"] = None
            _state["last_ok_at"] = time.time()
            if _state["status"].startswith("Error") or _state["status"] == "not configured":
                _state["status"] = "OpenShock · Connected"

        if _api is not None and _api.supports("api.set"):
            try:
                _api.set("status_label", f"Live · {label}")
            except Exception:
                pass
        _log(f"OpenShock live log: {label}")
    except Exception as e:
        _log(f"OpenShock log poll: {e}")


def _poll_loop() -> None:
    while not _poll_stop.is_set():
        try:
            if (
                _backend() == "openshock"
                and _api is not None
                and bool(_api.get("os_watch_logs", True))
                and _os_token()
            ):
                _os_poll_logs_once()
        except Exception as e:
            _log(f"poll loop: {e}")
        interval = 6
        if _api is not None:
            try:
                interval = max(3, min(60, int(_api.get("os_poll_secs") or 6)))
            except Exception:
                interval = 6
        _poll_stop.wait(interval)


def _ensure_poll_thread() -> None:
    global _poll_thread
    if _poll_thread is not None and _poll_thread.is_alive():
        return
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="shocker-poll")
    _poll_thread.start()


def _stop_poll() -> None:
    _poll_stop.set()


def _ps_username() -> str:
    if _api is None:
        return ""
    return str(_api.get("ps_username") or "").strip()


def _ps_apikey() -> str:
    if _api is None:
        return ""
    return str(_api.get("ps_apikey") or "").strip()


def _ps_sharecode() -> str:
    if _api is None:
        return ""
    return str(_api.get("ps_sharecode") or "").strip()


def _ps_log_name() -> str:
    if _api is None:
        return "OSC-DreamChatbox"
    return str(_api.get("ps_log_name") or "OSC-DreamChatbox").strip() or "OSC-DreamChatbox"


def _ps_verify() -> None:
    user = _ps_username()
    key = _ps_apikey()
    code = _ps_sharecode()
    if not user or not key or not code:
        _set_status("not configured", "PiShock needs username, API key, share code")
        return
    body = {
        "Username": user,
        "Apikey": key,
        "Code": code,
        "Name": _ps_log_name(),
    }
    try:
        status, data = _http_json(
            "POST",
            "https://do.pishock.com/api/GetShockerInfo",
            {"Content-Type": "application/json", "User-Agent": _OS_UA},
            body=body,
        )
        name = ""
        if isinstance(data, dict):
            name = str(data.get("name") or data.get("Name") or "")
        with _lock:
            _state["shocker_name"] = name or code
            _state["shocker_id"] = code
            _state["last_ok_at"] = time.time()
        _set_status(f"PiShock · {name or 'ready'}")
        _log(f"PiShock verify OK ({name or code})")
    except Exception as e:
        with _lock:
            _state["shocker_name"] = code
            _state["shocker_id"] = code
        _set_status("PiShock · configured")
        _log(f"PiShock verify soft-fail: {e}")


def _ps_send(op: int, intensity: int, duration_sec: int) -> None:
    user = _ps_username()
    key = _ps_apikey()
    code = _ps_sharecode()
    if not user or not key or not code:
        _set_status("Error", "PiShock not configured")
        return

    intensity = max(1, min(100, int(intensity)))
    duration_sec = max(1, min(15, int(duration_sec)))

    body = {
        "Username": user,
        "Name": _ps_log_name(),
        "Code": code,
        "Intensity": str(intensity),
        "Duration": str(duration_sec),
        "Apikey": key,
        "Op": str(op),
    }
    try:
        status, data = _http_json(
            "POST",
            "https://do.pishock.com/api/apioperate",
            {"Content-Type": "application/json", "User-Agent": _OS_UA},
            body=body,
        )
        names = {0: "Shock", 1: "Vibrate", 2: "Beep"}
        label = f"{names.get(op, op)} {intensity}% {duration_sec}s"
        if op == 2:
            label = f"Beep {duration_sec}s"
        with _lock:
            _state["last"] = label
            _state["intensity"] = intensity if op != 2 else None
            _state["by"] = "you"
            _state["last_ok_at"] = time.time()
            _state["error"] = ""
            _state["status"] = "PiShock · Connected"
        if _api is not None and _api.supports("api.set"):
            try:
                _api.set("status_label", f"OK · {label}")
            except Exception:
                pass
        _log(f"PiShock sent {label} → {data!r}")
    except Exception as e:
        _set_status("Error", str(e))
        _log(f"PiShock control failed: {e}")


def setup(api):
    global _api
    _api = api
    api.log("Shocker plugin loaded (OpenShock + PiShock)")
    api.ensure_data_dir()
    be = _backend()
    if be == "pishock":
        if _ps_username() and _ps_apikey() and _ps_sharecode():
            _set_status("connecting…")
            _run_bg(_ps_verify)
        else:
            _set_status("not configured")
    else:
        if _os_token():
            _set_status("connecting…")
            _run_bg(_os_fetch_shockers)
        else:
            _set_status("not configured")


def teardown():
    global _api
    _stop_poll()
    if _api is not None and _api.supports("api.set"):
        try:
            _api.set("status_label", "stopped")
        except Exception:
            pass
    _api = None


def on_settings(options):
    pass


def on_tick():
    pass


def get_values():
    with _lock:
        return {
            "status": _state["status"],
            "last": _state["last"] or None,
            "intensity": str(_state["intensity"]) if _state["intensity"] is not None else None,
            "shocker": _state["shocker_name"] or (
                (_state["shocker_id"][:8] + "…") if _state["shocker_id"] else None
            ),
            "by": _state["by"] or None,
        }


def get_text():
    if _api is None:
        return ""
    with _lock:
        status = _state["status"]
        last = _state["last"]
        offline = status in ("not configured", "Offline") or status.startswith("Error")
    if offline and not _api.get("show_when_offline", False):
        return ""
    prefix = str(_api.get("prefix") or "⚡").strip()
    parts = [p for p in (prefix, status) if p]
    if last:
        parts.append(f"· {last}")
    return " ".join(parts)


def on_action(key: str) -> str:
    be = _backend()

    if key == "btn_refresh":
        if be == "pishock":
            if not (_ps_username() and _ps_apikey() and _ps_sharecode()):
                return "set PiShock username, API key, share code"
            _set_status("verifying…")
            _run_bg(_ps_verify)
            return "verifying…"
        if not _os_token():
            return "set OpenShock API token first"
        _set_status("refreshing…")
        _run_bg(_os_fetch_shockers)
        return "refreshing…"

    intensity = 30
    duration_ms = 1000
    duration_sec = 1
    if _api is not None:
        try:
            intensity = int(_api.get("intensity") or 30)
        except Exception:
            intensity = 30
        try:
            duration_ms = int(_api.get("duration_ms") or 1000)
        except Exception:
            duration_ms = 1000
        try:
            duration_sec = int(_api.get("duration_sec") or 1)
        except Exception:
            duration_sec = 1

    if be == "pishock":
        mapping = {"btn_vibrate": 1, "btn_shock": 0, "btn_sound": 2}
        if key == "btn_stop":
            return "Stop is OpenShock-only"
        if key not in mapping:
            return ""
        if not (_ps_username() and _ps_apikey() and _ps_sharecode()):
            return "PiShock not configured"
        _run_bg(_ps_send, mapping[key], intensity, duration_sec)
        return "sending…"

    mapping = {
        "btn_vibrate": "Vibrate",
        "btn_shock": "Shock",
        "btn_sound": "Sound",
        "btn_stop": "Stop",
    }
    if key not in mapping:
        return ""
    control = mapping[key]
    if not _os_token():
        return "no OpenShock token"
    if not _os_shocker_id() and control != "Stop":
        return "select a shocker first"
    _run_bg(_os_send, control, intensity, duration_ms)
    return f"sending {control}…"


def on_event(name, data=None):
    if name == "app.shutdown":
        _stop_poll()
