from pydantic_settings import BaseSettings, SettingsConfigDict

# Telegram user_ids of the shop operator(s). Messages from these users are
# tagged [операт.] in chunked LLM input; messages from any other user_id
# (or NULL) are tagged [клиент]. Currently single operator using two
# Telegram accounts (RU + KZ phone numbers). Add new user_id here when
# hiring an assistant or connecting a Telegram bot.
OPERATOR_TELEGRAM_USER_IDS: frozenset[str] = frozenset({
    "user5748681414",  # primary account (RU number)
    "user565055562",   # secondary account (KZ number)
})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "pili_crm"
    db_user: str = "pili"
    db_password: str = "pili"
    database_url: str = "postgresql+asyncpg://pili:pili@localhost:5432/pili_crm"
    test_database_url: str | None = None

    MEDIA_EXTRACT_MODEL_PRIMARY: str = "qwen/qwen3-vl-30b"
    MEDIA_EXTRACT_MODEL_FALLBACK: str = "qwen/qwen3-vl-8b"
    MEDIA_EXTRACT_DEFAULT_ENDPOINT: str = "http://localhost:1234/v1"
    LM_STUDIO_API_BASE: str = "http://localhost:1234"


settings = Settings()
