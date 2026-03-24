import importlib.util
from pathlib import Path

def _load_gunicorn_conf():
    conf_path = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", conf_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gunicorn_conf_defaults_to_stateless_multi_worker_web(monkeypatch):
    monkeypatch.delenv("GUNICORN_WORKERS", raising=False)
    monkeypatch.delenv("GUNICORN_BIND", raising=False)

    module = _load_gunicorn_conf()

    assert module.bind == "127.0.0.1:8080"
    assert module.workers == 2
    assert not hasattr(module, "post_worker_init")
    assert not hasattr(module, "worker_exit")
