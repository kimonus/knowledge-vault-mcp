import urllib.request

import pytest
from scripts.feasibility_fetch_image import SafeRedirect


def test_redirect_strips_authorization_on_new_host() -> None:
    request = urllib.request.Request(
        "https://registry-1.docker.io/v2/example", headers={"Authorization": "synthetic"}
    )
    result = SafeRedirect().redirect_request(
        request, None, 302, "redirect", {}, "https://cdn.example.invalid/blob"
    )
    assert result is not None
    assert not result.has_header("Authorization")


def test_redirect_rejects_plaintext() -> None:
    request = urllib.request.Request("https://registry-1.docker.io/v2/example")
    with pytest.raises(RuntimeError, match="non-TLS"):
        SafeRedirect().redirect_request(
            request, None, 302, "redirect", {}, "http://example.invalid"
        )
