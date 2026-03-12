Let me explain `keycloak_client.py` layer by layer.

---

## What this file is

This file is a **typed async HTTP client** that wraps the Keycloak Admin REST API. It has one job: take Python method calls and convert them into authenticated HTTP requests to Keycloak.

In Java terms this is the combination of:
- A `@Service` class that wraps a REST client
- A token cache (like a `@Cacheable` method storing a Bearer token)
- A `RestTemplate` or `WebClient` wrapper with auth headers pre-applied

The rest of the application — `agent.py` in particular — never constructs a URL, never sets an `Authorization` header, never thinks about token expiry. All of that is encapsulated here.

---

## Module-level singleton

```python
settings = get_settings()

# ... class definition ...

keycloak_admin = KeycloakAdminClient()   # ← last line of the file
```

`keycloak_admin = KeycloakAdminClient()` runs **once at import time** and creates the single instance of this class. Every other file that does `from app.keycloak_client import keycloak_admin` gets the exact same object — same token cache, same state.

Java equivalent:
```java
@Service   // Spring creates one instance, shares it everywhere
public class KeycloakAdminClient { ... }
```

The difference: Spring's `@Service` singleton is managed by the bean container. Python's module-level instance is managed by `sys.modules` — once the module is imported and `keycloak_admin` is created, every subsequent import just gets the cached module with the same object.

---

## The constructor — instance state

```python
class KeycloakAdminClient:

    def __init__(self):
        self._admin_token: Optional[str] = None
        self._token_expiry: float = 0.0
```

`__init__` is Python's constructor — called once when `KeycloakAdminClient()` is called at the bottom of the file.

Only two instance variables are stored, and they exist purely for the token cache:

- `_admin_token` — the cached JWT access token string, or `None` if not yet obtained
- `_token_expiry` — the Unix timestamp (float seconds since epoch) when the token expires

The underscore prefix `_` is Python's convention for "private" — equivalent to Java's `private` keyword, but only by convention (Python does not enforce it). Any code outside the class *can* access `._admin_token` but *should not*.

`Optional[str]` means the value is either a `str` or `None`. Java equivalent: `String adminToken = null`.

`float = 0.0` — initialised to zero, which is January 1, 1970. The first token check `time.time() < 0.0 - 30` will always be `False`, forcing an immediate token fetch on the first call.

---

## `_get_admin_token` — the token cache

This is the most architecturally interesting method in the file. Read it carefully.

```python
async def _get_admin_token(self) -> str:
    import time
    if self._admin_token and time.time() < self._token_expiry - 30:
        return self._admin_token
```

**Cache hit check.** Two conditions must both be true to return the cached token:
1. `self._admin_token` — a token exists (not `None`, not empty string)
2. `time.time() < self._token_expiry - 30` — the current time is at least 30 seconds before expiry

The `- 30` is a **30-second buffer**. Keycloak tokens typically expire after 300 seconds. Without the buffer, you might fetch a token at second 299, it passes the expiry check, but by the time you use it on a Keycloak API call it has expired. The buffer ensures you always refresh the token 30 seconds before it actually expires, eliminating that race condition.

Java equivalent with Spring's `@Cacheable`:
```java
private String cachedToken = null;
private Instant tokenExpiry = Instant.EPOCH;

private String getAdminToken() {
    if (cachedToken != null && 
        Instant.now().isBefore(tokenExpiry.minusSeconds(30))) {
        return cachedToken;   // cache hit
    }
    // ... fetch new token
}
```

```python
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.keycloak_url}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": settings.keycloak_admin_client_id,   # "admin-cli"
                "username": settings.keycloak_admin_username,      # "admin"
                "password": settings.keycloak_admin_password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        token_data = response.json()
        self._admin_token = token_data["access_token"]
        self._token_expiry = time.time() + token_data.get("expires_in", 300)
        return self._admin_token
```

**Cache miss — fetch a new token.** This is the **Resource Owner Password Credentials (ROPC)** grant. You send the admin username and password directly to Keycloak's token endpoint and get back an access token.

This is different from the PKCE flow in `auth.py`. Here there is no browser, no redirect, no user interaction — it is a direct server-to-server call. This is acceptable for a backend service calling its own admin API, but would not be acceptable for user-facing authentication (which is why PKCE exists for the chat login).

Notice the target realm: `/realms/master/...` — the admin token always comes from the `master` realm regardless of which realm you are administering. This is how Keycloak's admin API works.

**`response.raise_for_status()`** — if Keycloak returns a 4xx or 5xx, this raises an `httpx.HTTPStatusError` immediately. It is the equivalent of:
```java
if (!response.getStatusCode().is2xxSuccessful()) {
    throw new HttpClientErrorException(response.getStatusCode());
}
```

**`token_data.get("expires_in", 300)`** — `get(key, default)` returns the value if the key exists, or the default `300` if it does not. Defensive programming — if Keycloak omits `expires_in` for some reason, assume 5 minutes.

**`self._token_expiry = time.time() + token_data.get("expires_in", 300)`** — stores the absolute expiry time as a Unix timestamp. Next time `_get_admin_token` is called, `time.time() < self._token_expiry - 30` will be true and the cached token will be returned instead of making another HTTP call.

---

## `_request` — the central HTTP dispatcher

```python
async def _request(
    self,
    method: str,
    path: str,
    json: Optional[dict] = None,
    params: Optional[dict] = None,
) -> tuple[int, Any]:
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
```

This is the single method that every public method in the class ultimately calls. It is the **Template Method pattern** — the skeleton of every Keycloak API request is defined here once, and the public methods just supply the varying parts (method, path, payload).

Java equivalent: a `RestTemplate` wrapper method:
```java
private <T> ResponseEntity<T> request(
        HttpMethod method, String path, 
        Object body, Class<T> responseType) {
    HttpHeaders headers = new HttpHeaders();
    headers.setBearerAuth(getAdminToken());
    headers.setContentType(MediaType.APPLICATION_JSON);
    return restTemplate.exchange(
        adminBaseUrl + path, method,
        new HttpEntity<>(body, headers), responseType
    );
}
```

**`settings.keycloak_admin_base`** resolves to:
```
http://localhost:8080/admin/realms/chatbot-test
```
So `_request("GET", "/users")` hits `http://localhost:8080/admin/realms/chatbot-test/users`.

**`json=json` vs `data=data`** — when you pass `json=payload` to httpx, it automatically serialises the dict to a JSON string and sets `Content-Type: application/json`. When you pass `data=payload` (as in the token fetch), it form-encodes it as `key=value&key2=value2` with `Content-Type: application/x-www-form-urlencoded`. Keycloak's token endpoint requires form encoding; its resource endpoints require JSON. Each is used in the right place.

**`params=params`** — for GET requests, `params={"search": "alice"}` appends `?search=alice` to the URL as query string parameters. Used by `get_users()` and `get_clients()` to support search filtering.

**Response body handling:**
```python
try:
    body = resp.json()
except Exception:
    body = resp.text
```

Keycloak returns JSON for most responses but plain text (or empty body) for others — for example a `201 Created` on user creation returns an empty body, and a `204 No Content` on deletion also returns nothing. `resp.json()` would throw on an empty body, so the `except` falls back to `resp.text`. This means `body` is always something — either a parsed dict/list or a raw string — and the caller never crashes on an unexpected content type.

**`return resp.status_code, body`** — returns a tuple of two values. Python functions can return multiple values this way — the caller unpacks them:
```python
status, body = await self._request("GET", "/users")
# status = 200
# body   = [{...}, {...}]
```

Java does not have tuple returns — you would need a `ResponseEntity<T>` or a custom wrapper class.

---

## The public methods — CRUD over HTTP

Every public method is a one-liner that calls `_request` with the right HTTP verb and URL path:

```python
async def create_user(self, payload: dict) -> tuple[int, Any]:
    return await self._request("POST", "/users", json=payload)

async def get_user(self, user_id: str) -> tuple[int, Any]:
    return await self._request("GET", f"/users/{user_id}")

async def update_user(self, user_id: str, payload: dict) -> tuple[int, Any]:
    return await self._request("PUT", f"/users/{user_id}", json=payload)

async def delete_user(self, user_id: str) -> tuple[int, Any]:
    return await self._request("DELETE", f"/users/{user_id}")
```

This maps directly to standard REST conventions and to the Keycloak Admin REST API spec:

| Method | Path | Keycloak Admin API |
|---|---|---|
| `POST /users` | create | `POST /admin/realms/{realm}/users` |
| `GET /users/{id}` | read one | `GET /admin/realms/{realm}/users/{id}` |
| `GET /users` | read all | `GET /admin/realms/{realm}/users` |
| `PUT /users/{id}` | update | `PUT /admin/realms/{realm}/users/{id}` |
| `DELETE /users/{id}` | delete | `DELETE /admin/realms/{realm}/users/{id}` |

The same CRUD pattern repeats for clients, roles, groups, identity providers, client scopes, and protocol mappers. Every resource section follows the same shape — the only things that change are the path prefix (`/users`, `/clients`, `/roles` etc.) and the identifier type (UUID for users and clients, name string for roles, alias string for IDPs).

**A few notable non-standard ones:**

```python
async def reset_user_password(self, user_id: str, payload: dict) -> tuple[int, Any]:
    return await self._request("PUT", f"/users/{user_id}/reset-password", json=payload)
```
This is a sub-resource action — not a CRUD operation on the user itself but an action on the user's credentials sub-resource.

```python
async def assign_realm_roles_to_user(self, user_id: str, roles: list) -> tuple[int, Any]:
    return await self._request("POST", f"/users/{user_id}/role-mappings/realm", json=roles)
```
`json=roles` passes a **list**, not a dict. The Keycloak API here expects a JSON array of role objects: `[{"id": "...", "name": "app-admin"}]`. httpx handles this fine — `json=` accepts any JSON-serialisable value.

```python
async def get_realm(self) -> tuple[int, Any]:
    return await self._request("GET", "")

async def update_realm(self, payload: dict) -> tuple[int, Any]:
    return await self._request("PUT", "", json=payload)
```
Empty path `""` means the URL is exactly `settings.keycloak_admin_base` with nothing appended — `http://localhost:8080/admin/realms/chatbot-test`. This is the realm resource itself.

---

## The complete call chain from user message to Keycloak

```
Browser: POST /api/chat  {"message": "Create user alice"}
    │
    ▼ main.py: chat_api()
    │
    ▼ agent.py: run_agent("Create user alice")
    │
    ▼ Node 1: parse_intent_node
    │   llm.ainvoke → Claude → {intent: "create_user", payload: {username: "alice",...}}
    │
    ▼ Node 2: execute_api_node
    │   _dispatch("create_user", {username: "alice"}, None)
    │   keycloak_admin.create_user({username: "alice", enabled: true})
    │       │
    │       ▼ _request("POST", "/users", json={username: "alice",...})
    │           │
    │           ▼ _get_admin_token()
    │               cache miss → POST master/token {admin credentials}
    │               ← access_token = "eyJ...", expires_in = 300
    │               stores token + expiry timestamp
    │               ← returns "eyJ..."
    │           │
    │           ▼ POST http://localhost:8080/admin/realms/chatbot-test/users
    │               Authorization: Bearer eyJ...
    │               Content-Type: application/json
    │               Body: {"username": "alice", "enabled": true, ...}
    │           │
    │           ← HTTP 201 Created, body = ""
    │           ← return (201, "")
    │       ← return (201, "")
    │   ← return (201, "")
    │
    ▼ Node 3: format_response_node
    │   success=True, status_code=201 → "✅ Creating user alice... Status: 201"
    │
    ▼ main.py: ChatResponse → JSON → browser
```

The next time any Keycloak method is called within 270 seconds, `_get_admin_token()` hits the cache and returns immediately — no extra HTTP request to Keycloak.