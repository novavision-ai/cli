import json
from unittest.mock import Mock

from novavision.update_listener import (
    UpdateListener,
    cli_topic,
    dispatch_update_message,
    load_listen_targets,
    post_update_status,
    split_listen_targets,
)


def _target(**overrides):
    target = {
        "folder_name": "abcdef",
        "id_device": 42,
        "host": "https://suite.novavision.ai",
        "mqtt_host": "redis.example",
        "mqtt_port": "6379",
        "mqtt_token": "secret",
        "mqtt_channel": "/mqtt/abc",
        "web_api": "https://suite.novavision.ai/api",
        "device_access_token": "device-token",
    }
    target.update(overrides)
    return target


def test_cli_topic_is_separate_from_the_server_channel():
    assert cli_topic("/mqtt/abc") == "/mqtt/abc/cli"
    assert cli_topic("/mqtt/abc/") == "/mqtt/abc/cli"


def test_split_waits_when_mqtt_settings_are_missing():
    ready, waiting = split_listen_targets([_target(mqtt_host="")])
    assert ready == []
    assert waiting[0]["folder_name"] == "abcdef"


def test_dispatch_ignores_unknown_mode_and_other_devices():
    reports = []
    runs = []

    def report(target, request_uuid, payload):
        reports.append(payload)

    def run_update(target, package_id):
        runs.append(package_id)
        return {"report": {"status": "success"}}

    assert (
        dispatch_update_message(
            {"mode": "other", "id_device": 42},
            [_target()],
            lambda: True,
            lambda: None,
            run_update,
            report,
        )
        == "ignored"
    )
    assert (
        dispatch_update_message(
            {"mode": "server-update", "id_device": 7, "server_package": 1, "requestUUID": "u"},
            [_target()],
            lambda: True,
            lambda: None,
            run_update,
            report,
        )
        == "ignored"
    )
    assert reports == []
    assert runs == []


def test_dispatch_reports_conflict_without_updating():
    reports = []

    def report(target, request_uuid, payload):
        reports.append((request_uuid, payload))

    result = dispatch_update_message(
        {
            "mode": "server-update",
            "id_device": 42,
            "server_package": 89074,
            "requestUUID": "uuid-1",
        },
        [_target()],
        lambda: False,
        lambda: None,
        lambda target, package_id: {"report": {}},
        report,
    )
    assert result == "conflict"
    assert reports[0][0] == "uuid-1"
    assert reports[0][1]["status_code"] == 409
    assert reports[0][1]["status"] == "error"


def test_dispatch_reports_the_update_result(nv_home):
    reports = []
    released = []

    def report(target, request_uuid, payload):
        reports.append(payload)

    result = dispatch_update_message(
        {
            "mode": "server-update",
            "id_device": "42",
            "server_package": 89074,
            "requestUUID": "uuid-1",
        },
        [_target()],
        lambda: True,
        lambda: released.append(True),
        lambda target, package_id: {
            "report": {
                "status_code": 200,
                "status": "success",
                "changed": True,
                "server_package": package_id,
                "message": "Server updated",
            }
        },
        report,
    )
    assert result == "updated"
    assert reports[0]["changed"] is True
    assert reports[0]["server_package"] == 89074
    assert released == [True]


def test_post_update_status_uses_form_body():
    posted = {}

    def post(url, params=None, data=None, timeout=None, headers=None):
        posted["url"] = url
        posted["params"] = params
        posted["headers"] = headers
        posted["data"] = data
        return Mock(status_code=200)

    assert post_update_status(
        _target(),
        "uuid-1",
        {"status_code": 200, "status": "success", "changed": False},
        post=post,
    )
    assert posted["url"].endswith("/ide/request/update-status-by-uuid")
    assert "access-token" not in posted["url"]
    assert posted["params"] is None
    assert posted["headers"]["Authorization"] == "Bearer device-token"
    assert posted["data"]["uuid"] == "uuid-1"
    assert json.loads(posted["data"]["data"])["changed"] is False


def test_listener_subscribes_only_to_cli_topic(fake_logger, nv_home):
    metadata = {
        "abcdef": {"id_device": 42, "host": "https://suite.novavision.ai"}
    }
    novavision = nv_home / ".novavision"
    novavision.mkdir(parents=True)
    (novavision / "servers.json").write_text(json.dumps(metadata), encoding="utf-8")
    server = novavision / "Server" / "abcdef"
    server.mkdir(parents=True)
    (server / ".env").write_text(
        "\n".join(
            [
                "MQTT_SERVICE_HOST=redis.example",
                "MQTT_SERVICE_PORT=6379",
                "MQTT_TOKEN=secret",
                "MQTT_CHANNEL=/mqtt/abc",
                "WEB_API=https://suite.novavision.ai/api",
                "DEVICE_ACCESS_TOKEN=device-token",
            ]
        ),
        encoding="utf-8",
    )
    subscribed = []

    class PubSub:
        def subscribe(self, *channels):
            subscribed.extend(channels)

        def get_message(self, timeout=None):
            listener.stop()
            return {
                "type": "message",
                "data": json.dumps(
                    {
                        "mode": "server-update",
                        "id_device": 42,
                        "server_package": 5,
                        "requestUUID": "uuid-1",
                    }
                ).encode("utf-8"),
            }

        def ping(self):
            return True

        def close(self):
            return None

    class Client:
        def pubsub(self, ignore_subscribe_messages=True):
            return PubSub()

        def close(self):
            return None

    listener = UpdateListener(
        fake_logger,
        redis_factory=lambda host, port, password: Client(),
        poll_timeout=0.01,
    )
    listener._run_update = lambda target, package_id: {
        "report": {"status_code": 200, "status": "success", "changed": True}
    }
    reported = []
    listener._report = lambda target, request_uuid, report: reported.append(report)
    listener.serve()

    assert subscribed == ["/mqtt/abc/cli"]
    assert "/mqtt/abc" not in subscribed
    assert reported[0]["changed"] is True
    assert load_listen_targets(home=nv_home)[0]["mqtt_channel"] == "/mqtt/abc"
