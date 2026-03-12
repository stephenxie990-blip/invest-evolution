import config as config_module
import web_server


def test_root_route_supports_canary_and_legacy_rollback(monkeypatch, tmp_path):
    static_dir = tmp_path / 'static'
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / 'index.html').write_text('LEGACY SHELL', encoding='utf-8')

    dist_dir = tmp_path / 'dist'
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / 'index.html').write_text('APP SHELL', encoding='utf-8')

    monkeypatch.setattr(web_server.app, 'static_folder', str(static_dir))
    monkeypatch.setattr(web_server, '_FRONTEND_DIST_DIR', dist_dir)
    monkeypatch.setattr(config_module.config, 'web_ui_shell_mode', 'legacy')
    monkeypatch.setattr(config_module.config, 'frontend_canary_enabled', True)
    monkeypatch.setattr(config_module.config, 'frontend_canary_query_param', '__frontend')

    client = web_server.app.test_client()

    assert b'LEGACY SHELL' in client.get('/').data
    assert b'APP SHELL' in client.get('/?__frontend=app').data

    monkeypatch.setattr(config_module.config, 'web_ui_shell_mode', 'app')

    assert b'APP SHELL' in client.get('/').data
    assert b'LEGACY SHELL' in client.get('/legacy').data


def test_root_route_supports_header_canary(monkeypatch, tmp_path):
    static_dir = tmp_path / 'static'
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / 'index.html').write_text('LEGACY SHELL', encoding='utf-8')

    dist_dir = tmp_path / 'dist'
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / 'index.html').write_text('APP SHELL', encoding='utf-8')

    monkeypatch.setattr(web_server.app, 'static_folder', str(static_dir))
    monkeypatch.setattr(web_server, '_FRONTEND_DIST_DIR', dist_dir)
    monkeypatch.setattr(config_module.config, 'web_ui_shell_mode', 'legacy')
    monkeypatch.setattr(config_module.config, 'frontend_canary_enabled', True)
    monkeypatch.setattr(config_module.config, 'frontend_canary_query_param', 'rollout')

    client = web_server.app.test_client()

    res = client.get('/', headers={'X-Invest-Frontend-Canary': 'app'})
    assert b'APP SHELL' in res.data


def test_root_route_canary_falls_back_when_frontend_dist_missing(monkeypatch, tmp_path):
    static_dir = tmp_path / 'static'
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / 'index.html').write_text('LEGACY SHELL', encoding='utf-8')

    monkeypatch.setattr(web_server.app, 'static_folder', str(static_dir))
    monkeypatch.setattr(web_server, '_FRONTEND_DIST_DIR', tmp_path / 'missing-dist')
    monkeypatch.setattr(config_module.config, 'web_ui_shell_mode', 'legacy')
    monkeypatch.setattr(config_module.config, 'frontend_canary_enabled', True)
    monkeypatch.setattr(config_module.config, 'frontend_canary_query_param', '__frontend')

    client = web_server.app.test_client()

    res = client.get('/?__frontend=app')
    assert b'LEGACY SHELL' in res.data
