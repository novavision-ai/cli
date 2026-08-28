import os
import subprocess

import pytest
from pathlib import Path


def _current_docker_host():
    existing = os.environ.get("DOCKER_HOST")
    if existing:
        return existing
    try:
        result = subprocess.run(
            [
                "docker",
                "context",
                "inspect",
                "--format",
                "{{.Endpoints.docker.Host}}",
            ],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    host = (result.stdout or "").strip()
    if result.returncode == 0 and host:
        return host
    return None


class DummyLoading:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class FakeLogger:
    def __init__(self, answers=None):
        self.messages = []
        self.answers = list(answers or [])

    def _record(self, level, message):
        self.messages.append((level, message))

    def info(self, message):
        self._record("info", message)

    def success(self, message):
        self._record("success", message)

    def warning(self, message):
        self._record("warning", message)

    def error(self, message):
        self._record("error", message)

    def question(self, message):
        self._record("question", message)
        if self.answers:
            return self.answers.pop(0)
        return "y"

    def loading(self, message):
        self._record("process", message)
        return DummyLoading()

    def messages_of(self, level):
        return [text for recorded_level, text in self.messages if recorded_level == level]


@pytest.fixture
def fake_logger():
    return FakeLogger()


@pytest.fixture
def nv_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    docker_host = _current_docker_host()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", lambda *args, **kwargs: home)
    if docker_host:
        monkeypatch.setenv("DOCKER_HOST", docker_host)
    return home
