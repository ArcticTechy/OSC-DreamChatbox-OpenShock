# Shocker plugin for OSC-DreamChatbox

OpenShock **and** PiShock control, with optional **full chatbox replace** when a shock happens.

**Plugin id:** `shocker` → placeholders are `{shocker}`, `{shocker_last}`, …

Repo: https://github.com/ArcticTechy/OSC-DreamChatbox-OpenShock

## Install

1. Install from this GitHub repo **or** zip the folder so it contains `plugin.json` + `main.py`.
2. Folder name must match the id: `…/plugins/shocker/`
3. Enable the plugin and open settings.

If you had v1/v2 under the old `openshock` id, remove that folder and install this one as `shocker`.

## Placeholders

| Placeholder | Meaning |
|-------------|---------|
| `{shocker}` | Idle status line, **or** the event line while an event is active |
| `{shocker_status}` | Connected / Offline / Error |
| `{shocker_last}` | Last command (includes `by Name` when live) |
| `{shocker_type}` | Shock / Vibrate / Sound / Stop / Beep |
| `{shocker_intensity}` | Intensity |
| `{shocker_duration}` | Duration text (`1s`, `1500ms`, …) |
| `{shocker_device}` | Selected shocker name |
| `{shocker_by}` | Who triggered it (`you` or a username) |
| `{shocker_event}` | Event line only while showing, else empty |

## Live event (replace chatbox)

Settings → **Live event (replace chatbox)**:

1. Turn on **Replace chatbox on action**
2. Edit **Event template**, e.g.  
   `⚡ {shocker_type} {shocker_intensity}% {shocker_duration} by {shocker_by}`
3. Set **Show for** (seconds)

When you press a button (or OpenShock live log sees someone else), the plugin:

- builds the event line from the template  
- calls `api.refresh()`  
- uses **`on_text()`** so the **entire** VRChat chatbox becomes that line for a few seconds  
- then returns to your normal AIO / status text

## Backends

### OpenShock
API token + shocker UUID. Optional **Watch live logs** polls who triggered actions on the website.

### PiShock
Username + API key + share code. Duration in whole seconds (1–15).

## Licence

GPL-3.0-or-later
