"""
Unit tests for src/config.py.

All tests monkeypatch os.environ so they are hermetic — no .env file
required, no side-effects on the real environment.
"""

import os
import sys
import pytest

# Ensure the project root is on sys.path when running from any directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(env_overrides: dict):
    """
    Import Settings fresh with the given env vars injected.
    Re-imports the module each time so cached settings don't bleed between tests.
    """
    import importlib
    import src.config as cfg_module

    for key, value in env_overrides.items():
        os.environ[key] = str(value)

    # Force a fresh read (pydantic-settings reads env at construction time)
    importlib.reload(cfg_module)
    return cfg_module.Settings()


def _clear_env(*keys):
    for key in keys:
        os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_env():
    """Remove all pipeline-related env vars before and after each test."""
    keys = [
        "DATABASE_URL", "MOCK_LLM_ENABLED", "OPENAI_API_KEY", "OPENAI_MODEL",
        "LLM_TIMEOUT_SECONDS", "IDEMPOTENCY_MODE", "KB_ENABLED",
        "KB_CONNECTION_STRING", "RETRY_MAX_ATTEMPTS", "RETRY_DELAY_SECONDS",
        "LOG_LEVEL", "HYPOTHESIS_PROFILE",
    ]
    _clear_env(*keys)
    yield
    _clear_env(*keys)


# ---------------------------------------------------------------------------
# Tests: required field
# ---------------------------------------------------------------------------

class TestDatabaseUrlRequired:
    def test_settings_succeed_when_database_url_is_set(self):
        settings = _make_settings({"DATABASE_URL": "postgresql://u:p@localhost:5432/db"})
        assert settings.DATABASE_URL == "postgresql://u:p@localhost:5432/db"

    def test_missing_database_url_raises(self):
        """Settings() without DATABASE_URL must raise (pydantic ValidationError)."""
        import importlib
        import src.config as cfg_module
        importlib.reload(cfg_module)

        from pydantic import ValidationError
        with pytest.raises((ValidationError, SystemExit)):
            cfg_module.Settings()


# ---------------------------------------------------------------------------
# Tests: defaults
# ---------------------------------------------------------------------------

class TestDefaults:
    @pytest.fixture(autouse=True)
    def with_db_url(self):
        os.environ["DATABASE_URL"] = "postgresql://u:p@localhost:5432/db"

    def test_mock_llm_enabled_defaults_to_true(self):
        settings = _make_settings({"DATABASE_URL": "postgresql://u:p@localhost:5432/db"})
        assert settings.MOCK_LLM_ENABLED is True

    def test_openai_api_key_defaults_to_empty_string(self):
        settings = _make_settings({"DATABASE_URL": "postgresql://u:p@localhost:5432/db"})
        assert settings.OPENAI_API_KEY == ""

    def test_openai_model_default(self):
        settings = _make_settings({"DATABASE_URL": "postgresql://u:p@localhost:5432/db"})
        assert settings.OPENAI_MODEL == "gpt-4o-mini"

    def test_idempotency_mode_default_is_skip(self):
        settings = _make_settings({"DATABASE_URL": "postgresql://u:p@localhost:5432/db"})
        assert settings.IDEMPOTENCY_MODE == "skip"

    def test_kb_enabled_defaults_to_false(self):
        settings = _make_settings({"DATABASE_URL": "postgresql://u:p@localhost:5432/db"})
        assert settings.KB_ENABLED is False

    def test_retry_max_attempts_default(self):
        settings = _make_settings({"DATABASE_URL": "postgresql://u:p@localhost:5432/db"})
        assert settings.RETRY_MAX_ATTEMPTS == 3

    def test_retry_delay_seconds_default(self):
        settings = _make_settings({"DATABASE_URL": "postgresql://u:p@localhost:5432/db"})
        assert settings.RETRY_DELAY_SECONDS == 2.0

    def test_log_level_default(self):
        settings = _make_settings({"DATABASE_URL": "postgresql://u:p@localhost:5432/db"})
        assert settings.LOG_LEVEL == "INFO"

    def test_hypothesis_profile_default(self):
        settings = _make_settings({"DATABASE_URL": "postgresql://u:p@localhost:5432/db"})
        assert settings.HYPOTHESIS_PROFILE == "dev"

    def test_llm_timeout_seconds_default(self):
        settings = _make_settings({"DATABASE_URL": "postgresql://u:p@localhost:5432/db"})
        assert settings.LLM_TIMEOUT_SECONDS == 30


# ---------------------------------------------------------------------------
# Tests: env var overrides
# ---------------------------------------------------------------------------

class TestEnvVarOverrides:
    def test_mock_llm_enabled_can_be_set_to_false(self):
        settings = _make_settings({
            "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
            "MOCK_LLM_ENABLED": "false",
        })
        assert settings.MOCK_LLM_ENABLED is False

    def test_idempotency_mode_accepts_update(self):
        settings = _make_settings({
            "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
            "IDEMPOTENCY_MODE": "update",
        })
        assert settings.IDEMPOTENCY_MODE == "update"

    def test_idempotency_mode_accepts_reprocess(self):
        settings = _make_settings({
            "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
            "IDEMPOTENCY_MODE": "reprocess",
        })
        assert settings.IDEMPOTENCY_MODE == "reprocess"

    def test_idempotency_mode_rejects_invalid_value(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            _make_settings({
                "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
                "IDEMPOTENCY_MODE": "invalid_mode",
            })

    def test_retry_max_attempts_override(self):
        settings = _make_settings({
            "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
            "RETRY_MAX_ATTEMPTS": "5",
        })
        assert settings.RETRY_MAX_ATTEMPTS == 5

    def test_hypothesis_profile_accepts_ci(self):
        settings = _make_settings({
            "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
            "HYPOTHESIS_PROFILE": "ci",
        })
        assert settings.HYPOTHESIS_PROFILE == "ci"

    def test_openai_api_key_override(self):
        settings = _make_settings({
            "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
            "OPENAI_API_KEY": "sk-test-key",
        })
        assert settings.OPENAI_API_KEY == "sk-test-key"

    def test_kb_enabled_can_be_set_to_true(self):
        settings = _make_settings({
            "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
            "KB_ENABLED": "true",
        })
        assert settings.KB_ENABLED is True


# ---------------------------------------------------------------------------
# Tests: get_settings() helper
# ---------------------------------------------------------------------------

class TestGetSettings:
    def test_get_settings_returns_settings_instance(self):
        import importlib
        import src.config as cfg_module
        os.environ["DATABASE_URL"] = "postgresql://u:p@localhost:5432/db"
        importlib.reload(cfg_module)
        settings = cfg_module.get_settings()
        assert settings.DATABASE_URL == "postgresql://u:p@localhost:5432/db"

    def test_get_settings_exits_on_missing_database_url(self):
        import importlib
        import src.config as cfg_module
        importlib.reload(cfg_module)
        with pytest.raises(SystemExit):
            cfg_module.get_settings()
