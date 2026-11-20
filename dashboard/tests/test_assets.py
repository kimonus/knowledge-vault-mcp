import httpx
import pytest
from knowledge_vault_dashboard.app import PREFIX, SECURITY_HEADERS, create_app
from knowledge_vault_dashboard.assets import Assets
from knowledge_vault_dashboard.config import Settings

from conftest import CREDENTIAL, HEADERS, HOST


@pytest.fixture
def static(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(
        '<!doctype html><html><div data-auth-mode="__KV_AUTH_MODE__"></div></html>'
    )
    (tmp_path / "assets/index-hash123.js").write_text('document.title = "Knowledge Vault";')
    (tmp_path / "assets/index-hash123.css").write_text("body { color: black; }")
    (tmp_path / "assets/index-hash123.js.map").write_text("private source map")
    (tmp_path / "assets/secret.txt").write_text("must not serve")
    (tmp_path / "assets/link.js").symlink_to(tmp_path / "index.html")
    return tmp_path


async def test_private_shell_and_hashed_files_have_only_public_mode(
    settings, static, core_transport
):
    app = create_app(settings, transport=core_transport, static_directory=static)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://" + HOST) as client,
    ):
        for path in [
            "/dashboard/",
            "/dashboard/login",
            "/dashboard/explorer",
            "/dashboard/portal",
            "/dashboard/state",
            "/dashboard/assertions/" + "a" * 36,
            "/dashboard/portal/contexts/" + "b" * 36,
            "/dashboard/portal/topics/dG9waWM",
            "/dashboard/portal/unclassified/topics",
        ]:
            response = await client.get(path)
            assert response.status_code == 200
            assert 'data-auth-mode="privateSession"' in response.text
            for key, value in SECURITY_HEADERS.items():
                assert response.headers[key] == value
            for secret in [
                settings.service_token,
                settings.pepper,
                settings.signing_key,
                CREDENTIAL,
            ]:
                assert secret not in response.text
        for filename, media_type in [
            ("index-hash123.js", "text/javascript"),
            ("index-hash123.css", "text/css"),
        ]:
            response = await client.get("/dashboard/assets/" + filename)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(media_type)
        assert (await client.get(PREFIX + "session")).status_code == 401
        assert (
            await client.get("/dashboard/explorer", headers={"Host": "evil.test"})
        ).status_code == 403
        assert (await client.get("/dashboard/explorer?query=private")).status_code == 422
        assert (
            await client.post("/dashboard/explorer", headers=HEADERS, json={})
        ).status_code == 405


async def test_known_navigation_only_and_api_cannot_bypass_fetch_metadata(
    settings, static, core_transport
):
    app = create_app(settings, transport=core_transport, static_directory=static)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://" + HOST) as client,
    ):
        navigation = {
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
        assert (await client.get("/dashboard/explorer", headers=navigation)).status_code == 200
        assert (await client.get(PREFIX + "session", headers=navigation)).status_code == 403
        assert (
            await client.get("/dashboard/explorer", headers={"Sec-Fetch-Site": "same-site"})
        ).status_code == 403
        assert (
            await client.get(
                "/dashboard/explorer", headers={**navigation, "Origin": "https://evil.test"}
            )
        ).status_code == 403
        assert (
            await client.get("/dashboard/explorer", headers=[("Host", HOST), ("Host", HOST)])
        ).status_code == 400
        for path in [
            "/dashboard/assets/index-hash123.js.map",
            "/dashboard/assets/secret.txt",
            "/dashboard/assets/link.js",
            "/dashboard/not-found",
            "/dashboard/%65xplorer",
            "/dashboard//explorer",
            "/mcp",
            "/api/v1/read/capabilities",
            "/dashboard/portal/unclassified/evil",
        ]:
            assert (await client.get(path)).status_code == 404
        await client.post(
            PREFIX + "session/login", headers=HEADERS, json={"credential": CREDENTIAL}
        )
        assert (await client.get(PREFIX + "not-found")).status_code == 404
        assert (await client.get("/dashboard/assets/missing.js")).status_code == 404


async def test_access_does_not_make_static_pages_a_private_fallback(
    access_settings, static, core_transport
):
    app = create_app(access_settings, transport=core_transport, static_directory=static)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://" + HOST) as client,
    ):
        for path in ["/dashboard/", "/dashboard/login", "/dashboard/assets/index-hash123.js"]:
            assert (await client.get(path)).status_code == 401
        assert (
            await client.post(
                PREFIX + "session/login", headers=HEADERS, json={"credential": CREDENTIAL}
            )
        ).status_code == 404


async def test_disabled_stays_inaccessible_with_built_assets(static):
    app = create_app(Settings(), static_directory=static)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://" + HOST
    ) as client:
        assert (await client.get("/dashboard/")).status_code == 404
        assert (await client.get("/dashboard/assets/index-hash123.js")).status_code == 404


def test_unbuilt_assets_and_index_symlinks_are_not_served(tmp_path):
    assert not Assets(tmp_path).is_page("/dashboard/")
    target = tmp_path / "real.html"
    target.write_text("secret")
    (tmp_path / "index.html").symlink_to(target)
    assert not Assets(tmp_path).is_page("/dashboard/")
