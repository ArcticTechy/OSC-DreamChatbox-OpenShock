"""Shocker plugin for OSC-DreamChatbox – OpenShock + PiShock.

Placeholders: {shocker}, {shocker_last}, {shocker_by}, …
Optional live event: temporarily replaces the whole chatbox via on_text().
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
    "duration": "",
    "type": "",
    "device": "",
    "shocker_id": "",
    "by": "",
    "error": "",
    "shockers": [],
    "last_ok_at": 0.0,
    "last_log_id": None,
    "event_until": 0.0,
    "event_line": "",
}

_poll_stop = threading.Event()
_poll_thread: Optional[threading.Thread] = None

_OS_UA = "OSC-DreamChatbox-Shocker/2.2"
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


def _http_json(method, url, headers, body=None, timeout=12.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
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
    threading.Thread(target=fn, args=args, daemon=True, name="shocker-bg").start()


def _refresh() -> None:
    if _api is not None and _api.supports("api.refresh"):
        try:
            _api.refresh()
        except Exception:
            pass


def _fmt_duration_ms(ms: int) -> str:
    """Format ms as e.g. 0.3 s, 1 s, 1.5 s (always space before s)."""
    try:
        ms = int(ms)
    except Exception:
        return str(ms)
    if ms <= 0:
        return "0 s"
    secs = ms / 1000.0
    if abs(secs - round(secs)) < 1e-9:
        return f"{int(round(secs))} s"
    return f"{secs:.1f} s"


def _record_action(typ: str, intensity, duration_text: str, by: str = "you", fire_event: bool = True) -> None:
    with _lock:
        _state["type"] = typ
        _state["last"] = " ".join(
            p for p in (
                typ,
                f"{intensity}%" if intensity is not None and typ not in ("Stop", "Sound", "Beep") else None,
                duration_text or None,
                f"by {by}" if by else None,
            ) if p
        )
        _state["intensity"] = intensity
        _state["duration"] = duration_text or ""
        _state["by"] = by or ""
        _state["last_ok_at"] = time.time()
        _state["error"] = ""

    if fire_event and _api is not None and bool(_api.get("event_enabled", True)):
        try:
            secs = max(1, min(30, int(_api.get("event_seconds") or 4)))
        except Exception:
            secs = 4
        line = _render_event_template()
        with _lock:
            _state["event_line"] = line
            _state["event_until"] = time.time() + secs
        _refresh()


def _render_event_template() -> str:
    if _api is None:
        return _state.get("last") or ""
    tmpl = str(_api.get("event_template") or "⚡ {shocker_type} {shocker_intensity}% by {shocker_by}")
    vals = get_values()
    repl = {
        "shocker": get_text_idle(),
        "shocker_status": vals.get("status") or "",
        "shocker_last": vals.get("last") or "",
        "shocker_intensity": vals.get("intensity") or "",
        "shocker_duration": vals.get("duration") or "",
        "shocker_type": vals.get("type") or "",
        "shocker_device": vals.get("device") or "",
        "shocker_by": vals.get("by") or "",
        "shocker_event": vals.get("event") or "",
    }
    out = tmpl
    for k, v in repl.items():
        out = out.replace("{" + k + "}", str(v if v is not None else ""))
    while "  " in out:
        out = out.replace("  ", " ")
    return out.strip()


def _event_active() -> bool:
    with _lock:
        return time.time() < float(_state.get("event_until") or 0)


def _event_line() -> str:
    with _lock:
        if time.time() < float(_state.get("event_until") or 0):
            return str(_state.get("event_line") or "")
        return ""


def _last_ttl() -> int:
    if _api is None:
        return 0
    try:
        return max(0, min(600, int(_api.get("last_ttl") or 0)))
    except Exception:
        return 0


def _last_visible() -> str:
    """Return last action text, or empty if TTL expired (0 = forever)."""
    with _lock:
        last = str(_state.get("last") or "")
        if not last:
            return ""
        ttl = _last_ttl()
        if ttl <= 0:
            return last
        age = time.time() - float(_state.get("last_ok_at") or 0)
        if age >= ttl:
            return ""
        return last


def get_values():
    last = _last_visible()
    with _lock:
        event = ""
        if time.time() < float(_state.get("event_until") or 0):
            event = str(_state.get("event_line") or "")
        show_details = bool(last)
        return {
            "status": _state["status"],
            "last": last or None,
            "intensity": (str(_state["intensity"]) if _state["intensity"] is not None else None) if show_details else None,
            "duration": (_state["duration"] or None) if show_details else None,
            "type": (_state["type"] or None) if show_details else None,
            "device": _state["device"] or (
                (_state["shocker_id"][:8] + "…") if _state["shocker_id"] else None
            ),
            "by": (_state["by"] or None) if show_details else None,
            "event": event or None,
        }


def get_text_idle() -> str:
    if _api is None:
        return ""
    with _lock:
        status = _state["status"]
        offline = status in ("not configured", "Offline") or status.startswith("Error")
    if offline and not _api.get("show_when_offline", False):
        return ""
    prefix = str(_api.get("prefix") or "⚡").strip()
    parts = [p for p in (prefix, status) if p]
    last = _last_visible()
    if last:
        parts.append(f"· {last}")
    return " ".join(parts)


def get_text():
    line = _event_line()
    if line:
        return line
    return get_text_idle()


def on_text(text):
    """Replace the entire chatbox while a live event is showing."""
    if _api is None or not bool(_api.get("event_enabled", True)):
        return text
    line = _event_line()
    if line:
        return line
    return text


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

    intensity, duration_ms, duration_sec = 30, 1000, 1
    try:
        intensity = int(_api.get("intensity") or 30)
    except Exception:
        pass
    try:
        duration_ms = int(_api.get("duration_ms") or 1000)
    except Exception:
        pass
    try:
        duration_sec = int(_api.get("duration_sec") or 1)
    except Exception:
        pass

    mapping = {
        "btn_vibrate": "Vibrate",
        "btn_shock": "Shock",
        "btn_sound": "Sound",
        "btn_stop": "Stop",
    }
    if key not in mapping:
        return ""
    control = mapping[key]

    if be == "pishock":
        if not (_ps_username() and _ps_apikey() and _ps_sharecode()):
            return "set PiShock credentials first"
        op_map = {"Vibrate": 1, "Shock": 0, "Sound": 2}
        if control not in op_map:
            return "Stop not supported on PiShock"
        _run_bg(_ps_send, op_map[control], intensity, duration_sec)
        return f"sending {control}…"

    if not _os_token():
        return "no OpenShock token"
    if not _os_shocker_id() and control != "Stop":
        return "select a shocker first"
    _run_bg(_os_send, control, intensity, duration_ms)
    return f"sending {control}…"


def on_event(name, data=None):
    if name == "app.shutdown":
        _stop_poll()
