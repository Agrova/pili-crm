from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "pili_crm"
    db_user: str = "pili"
    db_password: str = "pili"
    database_url: str = "postgresql+asyncpg://pili:pili@localhost:5432/pili_crm"
    test_database_url: str | None = None


settings = Settings()
