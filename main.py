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

_OS_UA = "OSC-DreamChatbox-Shocker/2.1"
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


def _max_intensity() -> int:
    if _api is None:
        return 100
    try:
        return max(1, min(100, int(_api.get("max_intensity") or 100)))
    except Exception:
        return 100


def _max_name_len() -> int:
    if _api is None:
        return 20
    try:
        return max(3, min(64, int(_api.get("max_name_len") or 20)))
    except Exception:
        return 20


def _clamp_name(name: str) -> str:
    """Limit length and strip line breaks / control characters from a live-log username."""
    if not name:
        return "someone"
    # drop newlines, tabs, and other control / non-printable characters
    cleaned = "".join(
        ch for ch in str(name)
        if ch.isprintable() and ch not in "\r\n\t\v\f\x00"
    )
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned:
        return "someone"
    limit = _max_name_len()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "…"
    return cleaned


def _fmt_duration_ms(ms: int) -> str:
    try:
        ms = int(ms)
    except Exception:
        return str(ms)
    if ms <= 0:
        return "0 s"
    secs = ms / 1000.0
    if abs(secs - round(secs)) < 1e-9:
        return f"{int(round(secs))} s"
    # one decimal place
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
    tmpl = str(_api.get("event_template") or "⚡ '{shocker_device}' {shocker_intensity}%:{shocker_duration} by {shocker_by}")
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





def _os_token() -> str:
    return str(_api.get("os_token") or "").strip() if _api else ""


def _os_base() -> str:
    if not _api:
        return "https://api.openshock.app"
    u = str(_api.get("os_base_url") or "https://api.openshock.app").rstrip("/")
    return u or "https://api.openshock.app"


def _os_ua() -> str:
    if not _api:
        return _OS_UA
    return str(_api.get("os_user_agent") or _OS_UA).strip() or _OS_UA


def _os_headers() -> dict:
    return {
        "Open-Shock-Token": _os_token(),
        "User-Agent": _os_ua(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _os_shocker_id() -> str:
    return str(_api.get("os_shocker_id") or "").strip() if _api else ""


def _os_ensure_shocker() -> str:
    """Return configured shocker id, or auto-select the first known one if empty."""
    sid = _os_shocker_id()
    if sid:
        return sid
    with _lock:
        items = list(_state.get("shockers") or [])
    if items and _api is not None and _api.supports("api.set"):
        try:
            first = items[0]["id"]
            _api.set("os_shocker_id", first)
            with _lock:
                _state["shocker_id"] = first
                _state["device"] = items[0].get("name") or first[:8]
            return first
        except Exception:
            pass
    return ""


def _os_fetch_shockers() -> None:
    if not _os_token():
        _set_status("not configured", "no OpenShock token")
        return
    try:
        code, data = _http_json("GET", f"{_os_base()}/1/shockers/own", _os_headers())
        items = []
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
        if not current and items and _api and _api.supports("api.set"):
            try:
                _api.set("os_shocker_id", items[0]["id"])
            except Exception:
                pass
            current = items[0]["id"]

        name = next((s["name"] for s in items if s["id"] == current), "")
        with _lock:
            _state["shocker_id"] = current
            _state["device"] = name or (current[:8] + "…" if current else "")

        _set_status(f"OpenShock · {len(items)} shocker(s)")
        _ensure_poll_thread()
    except Exception as e:
        _set_status("Error", str(e))
        _log(f"OpenShock refresh failed: {e}")


def _os_send(control_type: str, intensity: int, duration_ms: int) -> None:
    if not _os_token():
        _set_status("Error", "no OpenShock token")
        return
    sid = _os_ensure_shocker()
    if not sid and control_type != "Stop":
        _set_status("Error", "no shocker selected")
        return

    intensity = max(0, min(_max_intensity(), int(intensity)))
    duration_ms = max(300, min(30000, int(duration_ms)))
    body = {
        "shocks": [{
            "id": sid or "00000000-0000-0000-0000-000000000000",
            "type": control_type,
            "intensity": 0 if control_type == "Stop" else intensity,
            "duration": 300 if control_type == "Stop" else duration_ms,
            "exclusive": False,
        }],
        "customName": "ArcticTechy",
    }
    # Optimistic chatbox update so the overlay appears immediately
    dur = "" if control_type == "Stop" else _fmt_duration_ms(duration_ms)
    inten = None if control_type in ("Stop", "Sound") else intensity
    _record_action(control_type, inten, dur, by="ArcticTechy", fire_event=True)
    try:
        code, _ = _http_json(
            "POST", f"{_os_base()}/2/shockers/control", _os_headers(),
            body=body, timeout=5.0,
        )
        if _state["status"].startswith("Error") or _state["status"] == "not configured":
            with _lock:
                _state["status"] = "OpenShock · Connected"
        if _api and _api.supports("api.set"):
            try:
                _api.set("status_label", f"OK · {_state['last']}")
            except Exception:
                pass
        _log(f"OpenShock sent {_state['last']} (HTTP {code})")
    except Exception as e:
        _set_status("Error", str(e))
        _log(f"OpenShock control failed: {e}")
        # Clear the optimistic event on failure
        with _lock:
            _state["event_line"] = ""
            _state["event_until"] = 0.0
        _refresh()


def _os_type_name(t: Any) -> str:
    if isinstance(t, str):
        return t
    try:
        return _TYPE_MAP.get(int(t), str(t))
    except Exception:
        return str(t)


def _os_poll_logs_once() -> None:
    if not _os_token():
        return
    sid = _os_shocker_id()
    try:
        url = (
            f"{_os_base()}/1/shockers/{sid}/logs?offset=0&limit=5"
            if sid else
            f"{_os_base()}/1/shockers/logs?page=1&pageSize=5"
        )
        code, data = _http_json("GET", url, _os_headers(), timeout=6.0)
        entries = []
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
        who = str(custom).strip() if custom else (str(name).strip() or "someone")
        who = _clamp_name(who)

        with _lock:
            recent_self = (
                _state.get("by") in ("you", "ArcticTechy")
                and (time.time() - float(_state.get("last_ok_at") or 0)) < 2.5
            )
        if recent_self and who in ("you", "ArcticTechy", "OSC-DreamChatbox"):
            return

        try:
            inten = int(intensity) if intensity is not None and typ not in ("Stop", "Sound") else None
            if inten is not None:
                inten = max(0, min(_max_intensity(), inten))
        except Exception:
            inten = None
        try:
            dur = _fmt_duration_ms(int(duration)) if duration is not None else ""
        except Exception:
            dur = str(duration or "")

        if _state["status"].startswith("Error") or _state["status"] == "not configured":
            with _lock:
                _state["status"] = "OpenShock · Connected"

        _record_action(typ, inten, dur, by=who, fire_event=True)
        if _api and _api.supports("api.set"):
            try:
                _api.set("status_label", f"Live · {_state['last']}")
            except Exception:
                pass
        _log(f"OpenShock live: {_state['last']}")
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
        interval = 2
        if _api is not None:
            try:
                interval = max(1, min(60, int(_api.get("os_poll_secs") or 2)))
            except Exception:
                interval = 2
        with _lock:
            until = float(_state.get("event_until") or 0)
        if until and time.time() >= until:
            with _lock:
                if _state.get("event_line"):
                    _state["event_line"] = ""
                    _state["event_until"] = 0.0
                    need_refresh = True
                else:
                    need_refresh = False
            if need_refresh:
                _refresh()
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
    return str(_api.get("ps_username") or "").strip() if _api else ""


def _ps_apikey() -> str:
    return str(_api.get("ps_apikey") or "").strip() if _api else ""


def _ps_sharecode() -> str:
    return str(_api.get("ps_sharecode") or "").strip() if _api else ""


def _ps_log_name() -> str:
    if not _api:
        return "OSC-DreamChatbox"
    return str(_api.get("ps_log_name") or "OSC-DreamChatbox").strip() or "OSC-DreamChatbox"


def _ps_verify() -> None:
    user, key, code = _ps_username(), _ps_apikey(), _ps_sharecode()
    if not user or not key or not code:
        _set_status("not configured", "PiShock needs username, API key, share code")
        return
    body = {"Username": user, "Apikey": key, "Code": code, "Name": _ps_log_name()}
    try:
        status, data = _http_json(
            "POST", "https://do.pishock.com/api/GetShockerInfo",
            {"Content-Type": "application/json", "User-Agent": _OS_UA}, body=body,
        )
        name = ""
        if isinstance(data, dict):
            name = str(data.get("name") or data.get("Name") or "")
        with _lock:
            _state["device"] = name or code
            _state["shocker_id"] = code
            _state["last_ok_at"] = time.time()
        _set_status(f"PiShock · {name or 'ready'}")
    except Exception as e:
        with _lock:
            _state["device"] = code
            _state["shocker_id"] = code
        _set_status("PiShock · configured")
        _log(f"PiShock verify soft-fail: {e}")


def _ps_send(op: int, intensity: int, duration_sec: int) -> None:
    user, key, code = _ps_username(), _ps_apikey(), _ps_sharecode()
    if not user or not key or not code:
        _set_status("Error", "PiShock not configured")
        return
    intensity = max(1, min(_max_intensity(), int(intensity)))
    duration_sec = max(1, min(15, int(duration_sec)))
    body = {
        "Username": user, "Name": _ps_log_name(), "Code": code,
        "Intensity": str(intensity), "Duration": str(duration_sec),
        "Apikey": key, "Op": str(op),
    }
    names = {0: "Shock", 1: "Vibrate", 2: "Beep"}
    typ = names.get(op, str(op))
    inten = None if op == 2 else intensity
    dur = f"{duration_sec} s"
    # Optimistic update so chatbox reacts immediately
    _record_action(typ, inten, dur, by="ArcticTechy", fire_event=True)
    try:
        status, data = _http_json(
            "POST", "https://do.pishock.com/api/apioperate",
            {"Content-Type": "application/json", "User-Agent": _OS_UA}, body=body,
            timeout=5.0,
        )
        with _lock:
            _state["status"] = "PiShock · Connected"
        if _api and _api.supports("api.set"):
            try:
                _api.set("status_label", f"OK · {_state['last']}")
            except Exception:
                pass
        _log(f"PiShock sent {_state['last']} → {data!r}")
    except Exception as e:
        _set_status("Error", str(e))
        _log(f"PiShock control failed: {e}")
        with _lock:
            _state["event_line"] = ""
            _state["event_until"] = 0.0
        _refresh()



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
    _ensure_poll_thread()


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
    need_refresh = False
    with _lock:
        until = float(_state.get("event_until") or 0)
        if until and time.time() >= until and _state.get("event_line"):
            _state["event_line"] = ""
            _state["event_until"] = 0.0
            need_refresh = True
        # expire idle "last" message when last_ttl is set
        ttl = 0
        try:
            if _api is not None:
                ttl = max(0, min(600, int(_api.get("last_ttl") or 0)))
        except Exception:
            ttl = 0
        if ttl > 0 and _state.get("last"):
            age = time.time() - float(_state.get("last_ok_at") or 0)
            if age >= ttl:
                _state["last"] = ""
                _state["intensity"] = None
                _state["duration"] = ""
                _state["type"] = ""
                _state["by"] = ""
                need_refresh = True
    if need_refresh:
        _refresh()


def get_values():
    last = _last_visible()
    with _lock:
        event = ""
        if time.time() < float(_state.get("event_until") or 0):
            event = str(_state.get("event_line") or "")
        # when last has expired for display, also hide intensity/duration/type/by in placeholders
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
    if _api is not None:
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
    intensity = max(1, min(_max_intensity(), int(intensity)))
    duration_ms = max(300, min(30000, int(duration_ms)))

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
    if not _os_ensure_shocker() and control != "Stop":
        return "select a shocker first"
    _run_bg(_os_send, control, intensity, duration_ms)
    return f"sending {control}…"


def on_event(name, data=None):
    if name == "app.shutdown":
        _stop_poll()
