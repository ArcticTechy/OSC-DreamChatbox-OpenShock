# Shocker plugin for OSC-DreamChatbox (OpenShock + PiShock)

Control **OpenShock** and **PiShock** devices from inside OSC-DreamChatbox.

**v2.0** – dual backend + OpenShock live “who” via log polling.

## Install

Repo: https://github.com/ArcticTechy/OSC-DreamChatbox-OpenShock

1. Zip the folder (must contain `plugin.json` + `main.py` at the root of the zip), **or** clone / download this repo.
2. OSC-DreamChatbox → **Plugins** → install from zip / folder  
   or drop into  
   `~/.config/OSC-DreamChatbox/plugins/openshock/` (Linux) /  
   `%APPDATA%\\OSC-DreamChatbox\\plugins\\openshock\\` (Windows).
3. Enable the plugin and open its settings.

## Setup

### Backend switch

Pick **OpenShock** or **PiShock** at the top of the settings.

### OpenShock

1. Create an API token on [openshock.app](https://openshock.app) → Settings → API Tokens.
2. Paste into **API Token**.
3. Press **Refresh**. Shockers are listed; paste a UUID into **Shocker ID** if needed.
4. Leave **Watch live logs (show who)** enabled if you want website / other-app actions to appear in the chatbox.

### PiShock

1. Username + API key from [pishock.com/#/account](https://pishock.com/#/account).
2. Share code from the control page (Share button).
3. Press **Refresh** to verify, then use the action buttons.

## Actions

| Button   | OpenShock | PiShock        |
|----------|-----------|----------------|
| Vibrate  | ✓         | ✓              |
| Shock    | ✓         | ✓              |
| Sound    | ✓         | Beep           |
| Stop     | ✓         | (not available)|

Duration:
- OpenShock → milliseconds (`Duration (OpenShock)`)
- PiShock → whole seconds 1–15 (`Duration (PiShock)`)

## Placeholders

| Placeholder             | Meaning |
|-------------------------|---------|
| `{openshock}`           | Short status line (prefix + state + last action) |
| `{openshock_status}`    | Connected / Offline / Error / … |
| `{openshock_last}`      | Last command (includes `by Name` when live) |
| `{openshock_intensity}` | Intensity of the last command |
| `{openshock_shocker}`   | Selected shocker name / short ID |
| `{openshock_by}`        | Who triggered the last seen action (OpenShock live) |

### Live “who” (OpenShock only)

When **Watch live logs** is on, the plugin polls  
`GET /1/shockers/{id}/logs` every few seconds.

If someone (or the website / another app / share link) sends a command, the chatbox updates to something like:

```text
⚡ OpenShock · 1 shocker(s) · Shock 40% 1500ms by Alice
```

Your own button presses are labelled `by you`.

PiShock does not get this feed (classic HTTP API has no push/poll of third-party actions without a persistent WebSocket).

## Notes

- All HTTP calls run on background threads – the chatbox UI never blocks.
- OpenShock default API: `https://api.openshock.app`
- PiShock operate: `https://do.pishock.com/api/apioperate`
- OpenShock requires a non-empty User-Agent (plugin sends one by default).

## Licence

GPL-3.0-or-later (same as OSC-DreamChatbox).
