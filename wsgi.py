from app.web_server import app, bootstrap_runtime_services, _configured_gunicorn_host

bootstrap_runtime_services(host=_configured_gunicorn_host(), source="wsgi")

__all__ = ["app"]
