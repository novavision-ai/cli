from io import StringIO

from rich.console import Console

from novavision.logger import ConsoleLogger


def test_print_stream_during_loading_keeps_docker_lines_separate():
    buf = StringIO()
    logger = ConsoleLogger()
    logger.console = Console(
        file=buf,
        force_terminal=True,
        width=80,
        color_system=None,
        highlight=False,
        no_color=True,
    )
    docker_lines = [
        "#20 DONE 92.1s",
        "#17 [diginova-redis 6/7] RUN pip install --no-cache-dir -r requirements.prod",
    ]

    with logger.loading("Building server") as loading:
        assert loading.progress is not None
        assert loading.progress.console is logger.console
        for line in docker_lines:
            logger.print_stream(line)

    assert logger._active_loading is None
    output = buf.getvalue()
    for line in docker_lines:
        assert line in output
    for rendered in output.splitlines():
        if any(line in rendered for line in docker_lines):
            assert "Building server" not in rendered


def test_print_stream_without_loading_uses_console():
    buf = StringIO()
    logger = ConsoleLogger()
    logger.console = Console(
        file=buf,
        force_terminal=False,
        width=80,
        color_system=None,
        highlight=False,
        no_color=True,
    )
    logger.print_stream("#20 DONE 92.1s")
    assert "#20 DONE 92.1s" in buf.getvalue()
