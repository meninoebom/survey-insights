import pytest
from pydantic import ValidationError

from survey.config import Settings


def test_loads_from_env_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    for unset in ("MIN_RELIABLE_N", "SOURCE_DIR", "BUNDLED_SAMPLE", "LLM_API_KEY"):
        monkeypatch.delenv(unset, raising=False)
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql://u:p@localhost/db"
    assert settings.min_reliable_n == 30  # default
    assert settings.source_dir is None
    assert settings.bundled_sample is None
    assert settings.llm_api_key is None


def test_min_reliable_n_overrides_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("MIN_RELIABLE_N", "50")
    settings = Settings(_env_file=None)
    assert settings.min_reliable_n == 50


def test_missing_required_database_url_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
