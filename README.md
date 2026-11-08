# OpenShock plugin for OSC-DreamChatbox

Control [OpenShock](https://openshock.app) devices from inside OSC-DreamChatbox.

## Install

1. Zip the whole `openshock` folder (must contain `plugin.json` + `main.py` at the root of the zip).
2. In OSC-DreamChatbox → **Plugins** → **Installed** → install from zip  
   **or** drop the folder into  
   `~/.config/OSC-DreamChatbox/plugins/openshock/` (Linux) /  
   `%APPDATA%\\OSC-DreamChatbox\\plugins\\openshock\\` (Windows).
3. Enable the plugin and open its settings.

**Or install directly from this repo** (folder layout is already correct for the store):

```
https://github.com/ArcticTechy/OSC-DreamChatbox-OpenShock
```

## Setup

1. On [openshock.app](https://openshock.app) create an **API Token** (Settings → API Tokens) with at least the permission to use shockers.
2. Paste the token into **API Token** (stored as a secret).
3. Press **Refresh**. Your shockers appear; pick one or paste a UUID into **Shocker ID**.
4. Set default **Intensity** / **Duration**.
5. Use the **Vibrate / Shock / Sound / Stop** buttons, or put placeholders in any status / AIO string.

## Placeholders

| Placeholder            | Meaning                          |
|------------------------|----------------------------------|
| `{openshock}`          | Short status line (prefix + state + last action) |
| `{openshock_status}`   | Connected / Offline / Error / …  |
| `{openshock_last}`     | Last command that was sent       |
| `{openshock_intensity}`| Intensity of the last command    |
| `{openshock_shocker}`  | Selected shocker name or short ID|

## Notes

- All HTTP calls run on a background thread – the chatbox UI never blocks.
- Default endpoint: `https://api.openshock.app` (change only if you self-host).
- A non-empty **User-Agent** is required by OpenShock; the plugin already sends one.
- Control endpoint used: `POST /2/shockers/control`.
- Listing endpoint used: `GET /1/shockers/own`.

## Licence

GPL-3.0-or-later (same as OSC-DreamChatbox).
