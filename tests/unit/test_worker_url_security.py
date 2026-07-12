"""Worker URL security validation (T-031, gate G3)."""

from __future__ import annotations

import pytest

from common.errors import ConfigError
from orchestrator.worker_client import assert_worker_url_secure


@pytest.mark.parametrize(
    "url",
    [
        "local",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "http://10.8.0.2:8081",  # WireGuard mesh (RFC-1918)
        "http://192.168.1.50:8081",
        "http://172.16.3.4:8081",
        "https://worker.example.com",  # public but TLS
        "http://worker:8081",  # Docker Compose service name (internal network)
        "http://worker-local:8081",  # single-label host, internal only
    ],
)
def test_secure_urls_pass(url: str) -> None:
    assert_worker_url_secure("worker", url)  # must not raise


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8:8081",  # public IP, plain http
        "http://worker.example.com:8081",  # public host, plain http
        "http://104.238.24.196:8081",  # the VPS public IP without WG/TLS
    ],
)
def test_insecure_public_http_is_rejected(url: str) -> None:
    with pytest.raises(ConfigError, match="not secure"):
        assert_worker_url_secure("worker-vps", url)
