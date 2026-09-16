from novavision.config import DEFAULT_HOST, load_config, resolve_install_defaults


def test_resolve_install_defaults_uses_constant(nv_home):
    host, workspace = resolve_install_defaults(None, None)
    assert host == DEFAULT_HOST
    assert workspace is None


def test_resolve_install_defaults_reads_config(nv_home):
    config_dir = nv_home / ".novavision"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.json").write_text(
        '{"host": "https://alfa.suite.novavision.ai", "workspace": "ci"}',
        encoding="utf-8",
    )
    host, workspace = resolve_install_defaults(None, None)
    assert host == "https://alfa.suite.novavision.ai"
    assert workspace == "ci"
    host, workspace = resolve_install_defaults("https://suite.novavision.ai", "other")
    assert host == "https://suite.novavision.ai"
    assert workspace == "other"
    assert load_config()["workspace"] == "ci"
