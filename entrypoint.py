import logging
import os
import socket
import time

from app import initialize_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def wait_for_service(name, host, port):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                logger.info("%s is available on %s:%s.", name, host, port)
                return
        except OSError:
            time.sleep(2)
    raise RuntimeError(f"{name} did not become available on {host}:{port} within 90 seconds.")


wait_for_service(
    "PostgreSQL",
    os.environ.get("POSTGRES_HOST", "db"),
    int(os.environ.get("POSTGRES_PORT", "5432")),
)
wait_for_service(
    "Redis",
    os.environ.get("REDIS_HOST", "redis"),
    int(os.environ.get("REDIS_PORT", "6379")),
)
initialize_database()
os.execvp(
    "gunicorn",
    [
        "gunicorn",
        "--bind=0.0.0.0:8000",
        "--workers=1",
        "--threads=4",
        "--timeout=30",
        "app:app",
    ],
)
