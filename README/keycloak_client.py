"""Keycloak Admin REST API client."""
import httpx
import asyncio
from typing import Any, Optional
from app.config import get_settings

settings = get_settings()


class KeycloakAdminClient:
    """Async Keycloak Admin REST API client with token management."""

    def __init__(self):
        self._admin_token: Optional[str] = None
        self._token_expiry: float = 0.0

    async def _get_admin_token(self) -> str:
        """Obtain admin access token via client credentials or password grant."""
        import time
        if self._admin_token and time.time() < self._token_expiry - 30:
            return self._admin_token

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.keycloak_url}/realms/master/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": settings.keycloak_admin_client_id,
                    "username": settings.keycloak_admin_username,
                    "password": settings.keycloak_admin_password,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            token_data = response.json()
            self._admin_token = token_data["access_token"]
            import time as t
            self._token_expiry = t.time() + token_data.get("expires_in", 300)
            return self._admin_token

    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> tuple[int, Any]:
        """Make an authenticated admin API request."""
        token = await self._get_admin_token()
        url = f"{settings.keycloak_admin_base}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                method, url, json=json, params=params, headers=headers
            )
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            return resp.status_code, body

    # ── Users ──────────────────────────────────────────────────────────────
    async def create_user(self, payload: dict) -> tuple[int, Any]:
        return await self._request("POST", "/users", json=payload)

    async def get_users(self, params: Optional[dict] = None) -> tuple[int, Any]:
        return await self._request("GET", "/users", params=params)

    async def get_user(self, user_id: str) -> tuple[int, Any]:
        return await self._request("GET", f"/users/{user_id}")

    async def update_user(self, user_id: str, payload: dict) -> tuple[int, Any]:
        return await self._request("PUT", f"/users/{user_id}", json=payload)

    async def delete_user(self, user_id: str) -> tuple[int, Any]:
        return await self._request("DELETE", f"/users/{user_id}")

    async def reset_user_password(self, user_id: str, payload: dict) -> tuple[int, Any]:
        return await self._request("PUT", f"/users/{user_id}/reset-password", json=payload)

    async def send_verify_email(self, user_id: str) -> tuple[int, Any]:
        return await self._request("PUT", f"/users/{user_id}/send-verify-email")

    # ── Clients ────────────────────────────────────────────────────────────
    async def create_client(self, payload: dict) -> tuple[int, Any]:
        return await self._request("POST", "/clients", json=payload)

    async def get_clients(self, params: Optional[dict] = None) -> tuple[int, Any]:
        return await self._request("GET", "/clients", params=params)

    async def get_client(self, client_id: str) -> tuple[int, Any]:
        return await self._request("GET", f"/clients/{client_id}")

    async def update_client(self, client_id: str, payload: dict) -> tuple[int, Any]:
        return await self._request("PUT", f"/clients/{client_id}", json=payload)

    async def delete_client(self, client_id: str) -> tuple[int, Any]:
        return await self._request("DELETE", f"/clients/{client_id}")

    async def get_client_secret(self, client_id: str) -> tuple[int, Any]:
        return await self._request("GET", f"/clients/{client_id}/client-secret")

    # ── Roles ──────────────────────────────────────────────────────────────
    async def create_realm_role(self, payload: dict) -> tuple[int, Any]:
        return await self._request("POST", "/roles", json=payload)

    async def get_realm_roles(self) -> tuple[int, Any]:
        return await self._request("GET", "/roles")

    async def get_realm_role(self, role_name: str) -> tuple[int, Any]:
        return await self._request("GET", f"/roles/{role_name}")

    async def update_realm_role(self, role_name: str, payload: dict) -> tuple[int, Any]:
        return await self._request("PUT", f"/roles/{role_name}", json=payload)

    async def delete_realm_role(self, role_name: str) -> tuple[int, Any]:
        return await self._request("DELETE", f"/roles/{role_name}")

    async def assign_realm_roles_to_user(self, user_id: str, roles: list) -> tuple[int, Any]:
        return await self._request("POST", f"/users/{user_id}/role-mappings/realm", json=roles)

    # ── Groups ─────────────────────────────────────────────────────────────
    async def create_group(self, payload: dict) -> tuple[int, Any]:
        return await self._request("POST", "/groups", json=payload)

    async def get_groups(self) -> tuple[int, Any]:
        return await self._request("GET", "/groups")

    async def update_group(self, group_id: str, payload: dict) -> tuple[int, Any]:
        return await self._request("PUT", f"/groups/{group_id}", json=payload)

    async def delete_group(self, group_id: str) -> tuple[int, Any]:
        return await self._request("DELETE", f"/groups/{group_id}")

    async def add_user_to_group(self, user_id: str, group_id: str) -> tuple[int, Any]:
        return await self._request("PUT", f"/users/{user_id}/groups/{group_id}")

    # ── Identity Providers ─────────────────────────────────────────────────
    async def create_identity_provider(self, payload: dict) -> tuple[int, Any]:
        return await self._request("POST", "/identity-provider/instances", json=payload)

    async def get_identity_providers(self) -> tuple[int, Any]:
        return await self._request("GET", "/identity-provider/instances")

    async def get_identity_provider(self, alias: str) -> tuple[int, Any]:
        return await self._request("GET", f"/identity-provider/instances/{alias}")

    async def update_identity_provider(self, alias: str, payload: dict) -> tuple[int, Any]:
        return await self._request("PUT", f"/identity-provider/instances/{alias}", json=payload)

    async def delete_identity_provider(self, alias: str) -> tuple[int, Any]:
        return await self._request("DELETE", f"/identity-provider/instances/{alias}")

    # ── Realm Settings ─────────────────────────────────────────────────────
    async def get_realm(self) -> tuple[int, Any]:
        return await self._request("GET", "")

    async def update_realm(self, payload: dict) -> tuple[int, Any]:
        return await self._request("PUT", "", json=payload)

    # ── Client Scopes ──────────────────────────────────────────────────────
    async def create_client_scope(self, payload: dict) -> tuple[int, Any]:
        return await self._request("POST", "/client-scopes", json=payload)

    async def get_client_scopes(self) -> tuple[int, Any]:
        return await self._request("GET", "/client-scopes")

    # ── Protocol Mappers ───────────────────────────────────────────────────
    async def create_protocol_mapper(self, client_id: str, payload: dict) -> tuple[int, Any]:
        return await self._request("POST", f"/clients/{client_id}/protocol-mappers/models", json=payload)


keycloak_admin = KeycloakAdminClient()
