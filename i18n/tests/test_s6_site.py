from pathlib import Path

from fastapi.testclient import TestClient

from i18n_svc.config import Settings
from i18n_svc.main import create_app


def _site_client(tmp_path: Path) -> tuple[TestClient, Path]:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("INDEX", encoding="utf-8")
    app = create_app(
        Settings(
            _env_file=None,
            database_url=str(tmp_path / "i18n.db"),
            auth_db_url=str(tmp_path / "auth.db"),
            site_dir=str(site),
        )
    )
    return TestClient(app), site


def test_site_serves_files_and_spa_fallback(tmp_path: Path):
    client, site = _site_client(tmp_path)
    (site / "app.js").write_text("APP", encoding="utf-8")

    with client:
        assert client.get("/app.js").text == "APP"
        assert client.get("/account/security").text == "INDEX"


def test_site_rejects_encoded_sibling_traversal(tmp_path: Path):
    client, _ = _site_client(tmp_path)
    sibling = tmp_path / "siteevil"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("SECRET", encoding="utf-8")

    with client:
        response = client.get("/%2e%2e/siteevil/secret.txt")

    assert response.status_code == 404
    assert "SECRET" not in response.text
