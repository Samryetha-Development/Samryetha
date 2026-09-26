from i18n_svc.config import Settings


def test_tasks_site_origin_is_in_catalog_cors_allowlist():
    settings = Settings(
        _env_file=None,
        app_origin="https://samryetha.com",
        i18n_site_origin="https://i18n.samryetha.com",
        tasks_site_origin="https://tasks.samryetha.com",
    )
    assert settings.allowed_origins == [
        "https://samryetha.com",
        "https://i18n.samryetha.com",
        "https://tasks.samryetha.com",
    ]
