import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8080")
# Web API 当前以内嵌 Commander runtime 方式运行，必须保持单 worker。
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "180"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "15"))
accesslog = os.environ.get("GUNICORN_ACCESSLOG", "-")
errorlog = os.environ.get("GUNICORN_ERRORLOG", "-")
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
worker_tmp_dir = os.environ.get("GUNICORN_WORKER_TMP_DIR", "/dev/shm")
