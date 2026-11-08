"""Shocker plugin for OSC-DreamChatbox (OpenShock + PiShock)."""
import importlib.util
from pathlib import Path
_path = Path(__file__).with_name("shocker_core.py")
_spec = importlib.util.spec_from_file_location("_shocker_core", _path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
for _k in dir(_mod):
    if not _k.startswith("_"):
        globals()[_k] = getattr(_mod, _k)
