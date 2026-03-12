# KeycloakAI — Intelligent Realm Administration

An AI-powered Keycloak admin chatbot. Authenticate via SSO, describe what you want in plain English,
and let Claude extract the JSON payload and call the Keycloak Admin REST API for you.

---

## Table of Contents

1. [How to Start the App](#1-how-to-start-the-app)
2. [Project Layout](#2-project-layout)
3. [Reading Order — How the Code Fits Together](#3-reading-order)
4. [Step-by-Step: What Happens When You Run the Server](#4-step-by-step-server-startup)
5. [Step-by-Step: What Happens When You Log In](#5-step-by-step-login-flow)
6. [Step-by-Step: What Happens When You Send a Chat Message](#6-step-by-step-chat-flow)
7. [Configuration Reference (.env)](#7-configuration-reference)
8. [Keycloak Setup](#8-keycloak-setup)
9. [Dependencies](#9-dependencies)
10. [Example Prompts](#10-example-prompts)

---

## 1. How to Start the App

### Development (logs to console)

```bash
python run.py
```

### Production / staging

```bash
uvicorn asgi:app --host 0.0.0.0 --port 8000 --workers 1 --log-level debug
```

> **Why `asgi:app` and not `app.main:app`?**
> `asgi.py` loads the `.env` file *before* any app module is imported.
> This ensures `pydantic-settings` reads the correct environment variables
> at module-import time. Always start via `asgi:app` — never point uvicorn
> directly at `app.main:app` or the `.env` may not be loaded in time.

---

## Critical Questions

``` 
How does this command : uvicorn asgi:app --host 0.0.0.0 --port 8000 --workers 1 --log-level debug find and run the main.py if asgi.py is this 
```

``` 
"""
ASGI entrypoint — mirrors the wsgi.py pattern used in the Flask Orders API.

Load .env BEFORE importing any app modules so that pydantic-settings
(and any module-level code that reads os.environ) sees the correct values.

Start with:
    uvicorn asgi:app --host 0.0.0.0 --port 8000 --workers 1 --log-level debug
Or via run.py (development):
    python run.py
"""
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env before importing any app code ────────────────────────────────
project_root = Path(__file__).resolve().parent
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path, override=False)

# ── Now safe to import app modules ─────────────────────────────────────────
from app.main import app  # noqa: E402  (import after env load is intentional)

__all__ = ["app"]
```

Great question. The answer is in this one line:

```python
from app.main import app
```

Here's exactly what happens, step by step:

**1. uvicorn parses `asgi:app`**

The colon is a separator: `asgi` = the module to import, `app` = the name to look up inside it. uvicorn does the Python equivalent of:
```python
import asgi
callable = asgi.app
```

**2. `import asgi` executes `asgi.py` top to bottom**

Python runs every line at import time:
- `load_dotenv(...)` — reads `.env` into `os.environ`
- `from app.main import app` — this is where `main.py` enters the picture

**3. `from app.main import app` executes `main.py` top to bottom**

When Python imports `app.main`, it runs all of `main.py` — every line at module level:
- The `FastAPI()` object is created and assigned to the name `app`
- `SessionMiddleware` is added
- Static files and templates are mounted
- All routes (`/`, `/chat`, `/api/chat`, etc.) are registered

That `app` object now lives at `app.main.app`.

**4. `from app.main import app` binds the name in `asgi.py`**

After the import, `asgi.app` and `app.main.app` are **the same object in memory** — just two names pointing at the same `FastAPI` instance. `asgi.py` didn't create a new app, it just re-exported the one that `main.py` created.

**5. uvicorn gets `asgi.app` and starts serving it**

uvicorn now holds a reference to that `FastAPI` object and starts listening for HTTP connections.

---

So the chain is:

```
uvicorn command
    → imports asgi.py
        → load_dotenv()           ← .env loaded here
        → imports app/main.py     ← FastAPI app created here
            → imports app/config.py   ← Settings() reads os.environ
            → imports app/auth.py
            → imports app/agent.py
                → imports app/keycloak_client.py
    → looks up name "app" in asgi module
    → starts serving it
```

The critical insight is that `from app.main import app` does **not** mean "go run main.py later when a request comes in." It means "run main.py right now, at import time, and give me the object named `app` that it created." The `.env` load happens one line earlier, which is the entire reason `asgi.py` exists as a separate file.
## 2. Project Layout

```
keycloak-chatbot/
│
├── asgi.py                   ← ASGI entrypoint: loads .env, then imports app
├── run.py                    ← Development runner: same .env-first pattern
├── .env                      ← All secrets and configuration (never commit)
├── requirements.txt          ← Python dependencies
│
├── app/
│   ├── __init__.py           ← Empty (marks app/ as a Python package)
│   ├── config.py             ← pydantic-settings: reads .env, exposes Settings
│   ├── main.py               ← FastAPI app: middleware, routes, API endpoints
│   ├── auth.py               ← OIDC PKCE login/callback/logout + require_auth()
│   ├── agent.py              ← LangGraph pipeline: parse → execute → format
│   └── keycloak_client.py    ← Async Keycloak Admin REST API client
│
├── templates/
│   ├── landing.html          ← Public landing page (no login required)
│   └── chat.html             ← Chat UI (requires login)
│
├── static/                   ← CSS, JS, images (served by FastAPI StaticFiles)
│
└── backup/
    ├── chat_old.html         ← Previous dark-theme version
    └── landing_old.html      ← Previous dark-theme version
```

---

## 3. Reading Order

Start here if you want to understand the whole project from the ground up.

### Pass 1 — Entry points (how the process starts)

| File | What to look for |
|------|-----------------|
| `asgi.py` | `load_dotenv()` is called before any app import. This is the critical pattern that ensures config is loaded before pydantic-settings runs. |
| `run.py` | Same `load_dotenv()` pattern. Calls `uvicorn.run("asgi:app", ...)` — note it points at `asgi`, not `app.main`. |

### Pass 2 — Configuration (what can be changed)

| File | What to look for |
|------|-----------------|
| `app/config.py` | `Settings` class — every field maps 1:1 to a `.env` variable via `env=`. The `@property` methods build all Keycloak URLs from two base fields. `get_settings()` is `@lru_cache` so it is only instantiated once per process. |
| `.env` | Actual values for your environment. See [Configuration Reference](#7-configuration-reference). |

### Pass 3 — App wiring (how FastAPI is assembled)

| File | What to look for |
|------|-----------------|
| `app/main.py` | `SessionMiddleware` is added first — everything else depends on it. `StaticFiles` and `Jinja2Templates` are mounted. `auth_router` is included. The three page routes (`/`, `/chat`) and two API routes (`/api/chat`, `/api/examples`) are defined here. |

### Pass 4 — Authentication (how login works)

| File | What to look for |
|------|-----------------|
| `app/auth.py` | `generate_pkce_pair()` — creates `code_verifier` / `code_challenge`. `GET /auth/login` — builds the Keycloak redirect URL and writes state + verifier to session. `GET /auth/callback` — verifies state, exchanges code for tokens, fetches userinfo, writes `session["user"]`. `require_auth()` — FastAPI `Depends()` guard used on `/api/chat`. |

### Pass 5 — The AI pipeline (how a message is processed)

| File | What to look for |
|------|-----------------|
| `app/agent.py` | `SYSTEM_PROMPT` — the exact instructions Claude receives. `AgentState` TypedDict — the state dict that flows through all three nodes. `parse_intent_node` → `execute_api_node` → `format_response_node`. `_dispatch()` — `match/case` that maps intent strings to `keycloak_admin` methods. `build_graph()` — wires the three nodes linearly. `run_agent()` — the single public function called by `main.py`. |
| `app/keycloak_client.py` | `_get_admin_token()` — ROPC grant against `master` realm, token cached with 30s safety buffer. `_request()` — attaches Bearer token, handles JSON/text responses. Every public method is a one-liner wrapper over `_request()`. |

### Pass 6 — UI (what the user sees)

| File | What to look for |
|------|-----------------|
| `templates/landing.html` | Bootstrap 5 static page. Only Jinja logic is `{% if user %}` in the navbar. |
| `templates/chat.html` | Jinja variables: `user.name`, `user.username`, `user.email`, `keycloak_realm`. JS: `loadExamples()` fetches `/api/examples`. `sendMessage()` POSTs to `/api/chat` and renders markdown. `op-details` toggle shows raw `parsed_intent` + `api_result`. |

---

## 4. Step-by-Step: Server Startup

```bash
uvicorn asgi:app --host 0.0.0.0 --port 8000 --workers 1 --log-level debug
```

**Step 1 — uvicorn loads `asgi.py`**
- `Path(__file__).resolve().parent` resolves the project root
- `load_dotenv(dotenv_path=env_path, override=False)` reads `.env` into `os.environ`
- `override=False` means values already set in the shell are not overwritten

**Step 2 — `from app.main import app` runs**
- `app/config.py` is imported → `Settings()` reads from `os.environ` (now populated)
- `app/auth.py` is imported → calls `get_settings()` → returns the cached `Settings` instance
- `app/agent.py` is imported → `ChatAnthropic` LLM is instantiated → LangGraph is compiled
- `app/keycloak_client.py` is imported → `KeycloakAdminClient()` singleton is created (no network call yet)

**Step 3 — FastAPI `app` object is configured in `main.py`**
- `SessionMiddleware` is registered with `app_secret_key`
- `./static` is mounted at `/static`
- `./templates` is registered with Jinja2
- `/auth/*` routes are registered from `auth_router`
- `/`, `/chat`, `/api/chat`, `/api/examples`, `/api/health` routes are registered

**Step 4 — uvicorn starts listening on `0.0.0.0:8000`**

---

## 5. Step-by-Step: Login Flow

```
Browser → GET /auth/login
```

**Step 1 — `/auth/login` runs**
- A random `state` nonce is generated and stored in the session cookie
- `generate_pkce_pair()` creates:
  - `code_verifier` — a 64-byte random URL-safe string, stored in session
  - `code_challenge` — `BASE64URL(SHA256(verifier))`, sent to Keycloak
- Browser is 302-redirected to Keycloak's `/auth` endpoint with `code_challenge_method=S256`

**Step 2 — User authenticates at Keycloak**
- Keycloak shows its login page
- On success, Keycloak 302-redirects to `APP_BASE_URL/auth/callback?code=...&state=...`

**Step 3 — `/auth/callback` runs**
- `state` is verified against the session value (CSRF protection)
- `code_verifier` is read from the session
- An async `httpx.post` to Keycloak's token endpoint sends `code` + `code_verifier`
- **No `client_secret` is sent** — this is a public PKCE client; the verifier is the proof
- Keycloak returns `access_token`, `refresh_token`, `id_token`
- A second async `httpx.get` fetches userinfo using the access token
- `session["user"]` is written: `{sub, username, email, name, roles}`
- Browser is 302-redirected to `/chat`

**Step 4 — `/chat` is served**
- `session["user"]` is read and passed to `chat.html` as a template variable

---

## 6. Step-by-Step: Chat Message Flow

```
User types: "Create a user alice@example.com with first name Alice, last name Smith"
```

**Step 1 — Browser JS POSTs to `/api/chat`**
```json
{ "message": "Create a user alice@example.com ..." }
```

**Step 2 — `/api/chat` handler (`main.py`)**
- `Depends(require_auth)` checks `session["user"]` → 401 if missing
- `run_agent(message)` is called

**Step 3 — `run_agent()` initialises `AgentState`**
```python
{ "user_message": "...", "parsed": None, "api_result": None, "final_response": "", "error": None }
```

**Step 4 — Node 1: `parse_intent_node`**
- `ChatAnthropic.ainvoke(SYSTEM_PROMPT + user_message)` calls Claude
- Claude returns structured JSON:
```json
{
  "intent": "create_user",
  "resource_id": null,
  "payload": { "username": "alice", "email": "alice@example.com",
               "firstName": "Alice", "lastName": "Smith", "enabled": true },
  "explanation": "Creating user alice with email alice@example.com"
}
```
- Stored in `state["parsed"]`

**Step 5 — Node 2: `execute_api_node`**
- `_dispatch("create_user", payload, None)` calls `keycloak_admin.create_user(payload)`
- `_get_admin_token()` authenticates to `master` realm via ROPC (cached for ~5 min)
- `_request("POST", "/users", json=payload)` sends:
```
POST {KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users
Authorization: Bearer <admin-token>
{ "username": "alice", "email": "alice@example.com", ... }
```
- Returns `(201, "")` → stored in `state["api_result"]`

**Step 6 — Node 3: `format_response_node`**
- Reads `parsed["explanation"]` and `api_result["status_code"]`
- Builds a markdown string:
```
✅ **Creating user alice with email alice@example.com**
**Status:** 201
```
- Stored in `state["final_response"]`

**Step 7 — Response returned**
- `run_agent()` returns `{response, parsed, api_result}`
- `/api/chat` wraps it in `ChatResponse`
- Browser JS renders `marked.parse(data.response)` as a chat bubble
- Clicking "View raw operation details" shows the raw `parsed_intent` + `api_result` JSON

---

## 7. Configuration Reference

Create `.env` in the project root. All fields are required unless marked optional.

```env
# ── Keycloak ──────────────────────────────────────────────────────────────
# URL of your Keycloak server (no trailing slash)
KEYCLOAK_URL=http://localhost:8080

# Realm users log in to and that will be administered
KEYCLOAK_REALM=chatbot-test

# Client ID of the OIDC public (PKCE) client in KEYCLOAK_REALM
# Client authentication must be OFF — no client_secret is used
KEYCLOAK_CLIENT_ID=keycloak-chatbot

# Confidential client in master realm for Admin REST API (client credentials grant)
# Service account must have realm-management → realm-admin role assigned
KEYCLOAK_ADMIN_CLIENT_ID=keycloak-chatbot-backend
KEYCLOAK_ADMIN_CLIENT_SECRET=your-client-secret-from-keycloak-credentials-tab

# ── Anthropic ─────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...

# ── App ───────────────────────────────────────────────────────────────────
# Minimum 32 random characters — signs the session cookie
APP_SECRET_KEY=some-long-random-string-minimum-32-chars

# Public base URL — used to build the OIDC redirect_uri sent to Keycloak
APP_BASE_URL=http://localhost:8000

# Host / port for uvicorn (used by run.py only)
APP_HOST=0.0.0.0
APP_PORT=8000

# Set true to enable uvicorn auto-reload (development only)
APP_DEBUG=false
```

**URLs computed automatically by `config.py`** (no need to set these):

| Property | Pattern |
|----------|---------|
| `keycloak_auth_url` | `{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth` |
| `keycloak_token_url` | `{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token` |
| `keycloak_userinfo_url` | `{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/userinfo` |
| `keycloak_logout_url` | `{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/logout` |
| `keycloak_admin_base` | `{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}` |
| `redirect_uri` | `{APP_BASE_URL}/auth/callback` |

---

## 8. Keycloak Setup

### Create the realm

1. Log in to `http://localhost:8080`
2. Top-left dropdown → **Create realm** → name it `chatbot-test`

### Create the OIDC client

1. **Clients → Create client**
2. Client type: **OpenID Connect**
3. Client ID: `keycloak-chatbot`
4. **Client authentication: OFF** — creates a public PKCE client (no secret needed)
5. Standard flow: **ON** | Direct access grants: **OFF**
6. Valid redirect URIs: `http://localhost:8000/auth/callback`
7. Web origins: `http://localhost:8000`

### Create a test user

1. **Users → Create user** — set username, email, first/last name
2. **Email verified: ON**
3. **Credentials tab** → set a non-temporary password

> Create a confidential client `keycloak-chatbot-backend` in the `master` realm.
> Enable Service accounts roles. Assign realm-management → realm-admin to its
> service account. Copy the secret from the Credentials tab into .env.

---

## 9. Dependencies

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

| Package | Role |
|---------|------|
| `fastapi` | Web framework — routes, dependency injection, request/response models |
| `uvicorn[standard]` | ASGI server that runs the FastAPI app |
| `starlette` | `SessionMiddleware` for signed cookie sessions |
| `itsdangerous` | Signs the session cookie (used internally by Starlette) |
| `python-dotenv` | Loads `.env` in `asgi.py` / `run.py` before any app code runs |
| `pydantic-settings` | Typed config via `Settings` class reading from environment variables |
| `httpx` | Async HTTP client used by `auth.py` (token exchange) and `keycloak_client.py` |
| `langchain-anthropic` | LangChain wrapper for the Anthropic Claude API |
| `langgraph` | Builds and executes the three-node agent graph |
| `anthropic` | Underlying Anthropic SDK (required by `langchain-anthropic >= 0.3.3`) |
| `jinja2` | HTML template rendering |
| `python-multipart` | Required by FastAPI for form data parsing |

---

## 10. Example Prompts

These are also served by `/api/examples` and shown in the chat sidebar.

**Users**
```
Create a user with username john.doe, email john@example.com, first name John and last name Doe
List all users in the realm
Reset password for user ID <uuid> to SecurePass@2024 (temporary)
```

**Clients**
```
Create an OpenID Connect client called my-spa-app with redirect URI https://myapp.com/*
List all clients in the realm
Update client my-spa-app to enable direct access grants
```

**Roles**
```
Create a realm role called app-admin with description "Application administrator"
List all realm roles
Assign the app-admin role to user ID <uuid>
```

**Groups**
```
Create a group called developers
List all groups
Add user ID <uuid> to group ID <uuid>
```

**Identity Providers**
```
Create a Google identity provider with client ID my-google-client and client secret mysecret
List all identity providers
```

**Realm & Client Scopes**
```
Get current realm settings
Update the realm to enable user registration and remember me
List all client scopes in the realm
```