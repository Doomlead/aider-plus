from pathlib import Path

from aider.settings import (
    collect_agent_env_updates,
    collect_provider_key_updates,
    parse_conf_text,
    read_env_values,
    upsert_conf_text,
    write_env_updates,
)


def test_collect_provider_and_agent_settings_updates():
    provider_updates = collect_provider_key_updates(
        "openai-key",
        "",
        "openrouter-key",
        "GEMINI_API_KEY=gemini-key\nIGNORED\nXAI_API_KEY=xai-key",
    )
    agent_updates = collect_agent_env_updates(
        {"product": "gpt-4o", "engineering": "claude-sonnet-4-5"},
        {"product": True, "qa": False, "ux": "default"},
        {"product": "product-key"},
        {"engineering": "http://localhost:11434"},
    )

    assert provider_updates == {
        "OPENAI_API_KEY": "openai-key",
        "OPENROUTER_API_KEY": "openrouter-key",
        "GEMINI_API_KEY": "gemini-key",
        "XAI_API_KEY": "xai-key",
    }
    assert agent_updates["AIDER_COMPANY_MODEL_PRODUCT"] == "gpt-4o"
    assert agent_updates["AIDER_COMPANY_MODEL_ENGINEERING"] == "claude-sonnet-4-5"
    assert agent_updates["AIDER_COMPANY_CACHING_PRODUCT"] == "true"
    assert agent_updates["AIDER_COMPANY_CACHING_QA"] == "false"
    assert agent_updates["AIDER_COMPANY_API_KEY_PRODUCT"] == "product-key"
    assert agent_updates["AIDER_COMPANY_LOCAL_ENGINEERING"] == "http://localhost:11434"
    assert "AIDER_COMPANY_CACHING_UX" not in agent_updates


def test_env_writer_preserves_existing_values(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=old\nCUSTOM=value\n", encoding="utf-8")

    write_env_updates(
        env_path, {"OPENAI_API_KEY": "new", "AIDER_COMPANY_MODEL_QA": "o3"}
    )

    assert read_env_values(env_path) == {
        "AIDER_COMPANY_MODEL_QA": "o3",
        "CUSTOM": "value",
        "OPENAI_API_KEY": "new",
    }


def test_conf_text_upsert_preserves_other_configuration():
    conf_text = "dark-mode: true\nmodel: old-model\n# keep comments\n"

    updated = upsert_conf_text(
        conf_text,
        {"model": "new-model", "weak-model": "small-model"},
    )

    assert "dark-mode: true" in updated
    assert "# keep comments" in updated
    assert parse_conf_text(updated)["model"] == "new-model"
    assert parse_conf_text(updated)["weak-model"] == "small-model"
