import json
import os
import subprocess
from pathlib import Path

import requests

CREDENTIALS_NAME = "credentials"
PROFILE_PATH = "api/auth/default/profile"
TOKEN_ENV = "NOVAVISION_TOKEN"


class CredentialsError(Exception):
    pass


class ResolvedAuth:
    def __init__(self, token=None, source=None, host=None, username=None):
        self.token = token
        self.source = source
        self.host = host
        self.username = username


def credentials_path():
    return Path.home() / ".novavision" / CREDENTIALS_NAME


def canonical_host(host):
    value = str(host or "").strip()
    if value.startswith("http://"):
        value = value[len("http://") :]
    if not value.startswith("https://"):
        value = "https://" + value
    return value.rstrip("/")


def hosts_match(left, right):
    if not left or not right:
        return False
    return canonical_host(left) == canonical_host(right)


def load_credentials():
    path = credentials_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CredentialsError(
            "Saved login could not be read. Run `novavision login` again."
        ) from exc
    if not isinstance(data, dict):
        raise CredentialsError(
            "Saved login could not be read. Run `novavision login` again."
        )
    token = str(data.get("token") or "").strip()
    host = str(data.get("host") or "").strip()
    if not token or not host:
        raise CredentialsError(
            "Saved login could not be read. Run `novavision login` again."
        )
    username = str(data.get("username") or "").strip() or None
    return {
        "host": canonical_host(host),
        "token": token,
        "username": username,
    }


def save_credentials(host, token, username):
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "host": canonical_host(host),
            "token": token,
            "username": username,
        },
        indent=2,
    )
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (payload + "\n").encode("utf-8"))
    except Exception:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    os.close(fd)
    _restrict_to_owner(tmp)
    try:
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    _restrict_to_owner(path)
    return path


def delete_credentials():
    path = credentials_path()
    if not path.is_file():
        return False
    path.unlink()
    return True


def active_login_label():
    """Short session line, or None when there is no readable saved login."""
    try:
        saved = load_credentials()
    except CredentialsError:
        return None
    if not saved:
        return None
    username = saved.get("username") or "unknown"
    host = canonical_host(saved["host"]).split("://", 1)[-1]
    return f"{username} @ {host}"


def resolve_auth(flag_token=None, positional_token=None, env=None):
    """--token, then the legacy positional token, then NOVAVISION_TOKEN, then the saved login."""
    flag = str(flag_token or "").strip()
    if flag:
        return ResolvedAuth(token=flag, source="flag")

    positional = str(positional_token or "").strip()
    if positional:
        return ResolvedAuth(token=positional, source="positional")

    if env is None:
        env = os.environ
    env_token = str(env.get(TOKEN_ENV) or "").strip()
    if env_token:
        return ResolvedAuth(token=env_token, source="env")

    saved = load_credentials()
    if not saved:
        return ResolvedAuth()
    return ResolvedAuth(
        token=saved["token"],
        source="credentials",
        host=saved["host"],
        username=saved.get("username"),
    )


def verify_api_key(host, token, timeout=30):
    url = canonical_host(host) + "/" + PROFILE_PATH
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException:
        return None, f"Could not reach {canonical_host(host)}."

    username = _username_from_profile(response)
    if username:
        return username, None
    return None, f"API key was rejected. {_response_detail(response)}"


def _username_from_profile(response):
    if getattr(response, "status_code", None) != 200:
        return None
    try:
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("success") is not True:
        return None
    user = data.get("user")
    if not isinstance(user, dict):
        return None
    username = str(user.get("username") or "").strip()
    return username or None


def _response_detail(response):
    status = getattr(response, "status_code", None)
    message = None
    try:
        data = response.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        message = data.get("message") or data.get("error") or data.get("detail")
        if isinstance(message, (dict, list)):
            message = str(message)
        elif message is not None:
            message = str(message).strip() or None
    if message and status is not None:
        return f"{message} (HTTP {status})"
    if status is not None:
        return f"HTTP {status}"
    return "Request failed"


def _restrict_to_owner(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if os.name != "nt":
        return
    username = os.environ.get("USERNAME")
    if not username:
        return
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{username}:(R,W)"],
        check=False,
        capture_output=True,
        text=True,
    )
