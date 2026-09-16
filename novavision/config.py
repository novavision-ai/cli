import json
from pathlib import Path

DEFAULT_HOST = "https://suite.novavision.ai"


def config_path():
    return Path.home() / ".novavision" / "config.json"


def load_config():
    path = config_path()
    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_install_defaults(host=None, workspace=None):
    config = load_config()
    resolved_host = host or config.get("host") or DEFAULT_HOST
    resolved_workspace = workspace if workspace is not None else config.get("workspace")
    return resolved_host, resolved_workspace
