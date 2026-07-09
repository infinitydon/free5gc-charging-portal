from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.main import resolve_subscriber_from_request
from app.settings import Settings


def request_with(host="10.60.0.10", headers=None):
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers or {})


def test_resolve_subscriber_from_trusted_header():
    settings = Settings(
        TRUSTED_SUBSCRIBER_HEADER_ENABLED=True,
        TRUSTED_SUBSCRIBER_HEADER="x-subscriber-supi",
    )

    assert (
        resolve_subscriber_from_request(
            request_with(headers={"x-subscriber-supi": "imsi-001010000000001"}), settings
        )
        == "imsi-001010000000001"
    )


def test_resolve_subscriber_from_source_ip_binding():
    settings = Settings(SUBSCRIBER_BINDINGS_JSON='{"10.60.0.0/16":"imsi-001010000000001"}')

    assert resolve_subscriber_from_request(request_with(host="10.60.0.10"), settings) == "imsi-001010000000001"


def test_resolve_subscriber_rejects_unknown_source():
    settings = Settings()

    with pytest.raises(HTTPException) as exc:
        resolve_subscriber_from_request(request_with(host="10.99.0.10"), settings)

    assert exc.value.status_code == 403
