from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_deploy_assets_target_split_topology():
    web_unit = (PROJECT_ROOT / "deploy" / "systemd" / "invest-evolution.service").read_text(encoding="utf-8")
    runtime_unit = (PROJECT_ROOT / "deploy" / "systemd" / "invest-evolution-runtime.service").read_text(encoding="utf-8")
    env_example = (PROJECT_ROOT / "deploy" / "systemd" / "invest-evolution.env.example").read_text(encoding="utf-8")
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    runtime_state_design = (PROJECT_ROOT / "docs" / "RUNTIME_STATE_DESIGN.md").read_text(encoding="utf-8")
    nginx_conf = (PROJECT_ROOT / "deploy" / "nginx" / "invest-evolution.conf").read_text(encoding="utf-8")
    gunicorn_conf = (PROJECT_ROOT / "gunicorn.conf.py").read_text(encoding="utf-8")

    assert "Web/API 进程保持无状态" in web_unit
    assert "/api/status" in web_unit
    assert (
        "ExecStart=/opt/invest-evolution/.venv/bin/gunicorn -c "
        "/opt/invest-evolution/gunicorn.conf.py "
        "invest_evolution.interfaces.web.wsgi:app"
    ) in web_unit
    assert " wsgi:app" not in web_unit
    assert "invest-runtime" in runtime_unit
    assert "training.lock" in runtime_unit
    assert "WEB_EMBEDDED_RUNTIME_ENABLED=false" in env_example
    assert "GUNICORN_WORKERS=2" in env_example
    assert "post_worker_init" not in gunicorn_conf
    assert "worker_exit" not in gunicorn_conf
    assert "invest-evolution-runtime.service" in readme
    assert "compat/dev" in readme
    assert "clean boot" in readme.lower()
    assert "/api/status" in readme
    assert "stale lock" in readme.lower()
    assert "stale-lock" in runtime_state_design.lower()
    assert "training.lock" in runtime_state_design
    assert "/healthz 只表示 stateless Web/API upstream 健康" in nginx_conf
