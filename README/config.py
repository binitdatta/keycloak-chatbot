"""Application configuration using pydantic-settings."""
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    # Keycloak
    keycloak_url: str = Field(default="http://localhost:8080", env="KEYCLOAK_URL")
    keycloak_realm: str = Field(default="master", env="KEYCLOAK_REALM")
    keycloak_client_id: str = Field(default="keycloak-chatbot", env="KEYCLOAK_CLIENT_ID")
    # No client_secret for public PKCE clients — Keycloak validates via code_verifier only
    keycloak_admin_client_id: str = Field(default="admin-cli", env="KEYCLOAK_ADMIN_CLIENT_ID")
    keycloak_admin_username: str = Field(default="admin", env="KEYCLOAK_ADMIN_USERNAME")
    keycloak_admin_password: str = Field(default="admin", env="KEYCLOAK_ADMIN_PASSWORD")

    # Anthropic
    anthropic_api_key: str = Field(default="", env="ANTHROPIC_API_KEY")

    # App
    app_secret_key: str = Field(default="change-me-please", env="APP_SECRET_KEY")
    app_host: str = Field(default="0.0.0.0", env="APP_HOST")
    app_port: int = Field(default=8000, env="APP_PORT")
    app_debug: bool = Field(default=False, env="APP_DEBUG")
    app_base_url: str = Field(default="http://localhost:8000", env="APP_BASE_URL")

    @property
    def keycloak_issuer(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}"

    @property
    def keycloak_auth_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}/protocol/openid-connect/auth"

    @property
    def keycloak_token_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}/protocol/openid-connect/token"

    @property
    def keycloak_userinfo_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}/protocol/openid-connect/userinfo"

    @property
    def keycloak_logout_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}/protocol/openid-connect/logout"

    @property
    def keycloak_admin_base(self) -> str:
        return f"{self.keycloak_url}/admin/realms/{self.keycloak_realm}"

    @property
    def redirect_uri(self) -> str:
        return f"{self.app_base_url}/auth/callback"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()