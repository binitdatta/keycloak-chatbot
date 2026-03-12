Let me explain `auth.py` layer by layer, covering every design decision.

---

## What `auth.py` is

In Spring Security terms this file is the combination of:

- `SecurityConfig.java` — the filter chain configuration
- `OAuth2LoginController.java` — the login redirect and callback handlers
- A custom `AuthenticationFilter` — the `require_auth` session guard
- `UserDetailsService` — the userinfo fetch and session population

All explicit, no magic, no annotations processor.

---

## Module-level setup (runs once at import time)

```python
logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])
_signer = URLSafeTimedSerializer(settings.app_secret_key, salt="pkce-state")
```

**`logger`** — standard Python logger named after the module (`app.auth`). Appears in uvicorn output prefixed with the module name.

**`settings`** — cached singleton, same object every file gets.

**`router = APIRouter(prefix="/auth")`** — this is a sub-router. Every route defined on it gets `/auth` prepended automatically. So `@router.get("/login")` becomes `GET /auth/login` when included in `main.py`. Java equivalent: a `@RestController` with `@RequestMapping("/auth")` at the class level.

**`_signer = URLSafeTimedSerializer(...)`** — this is the most important line in the file. Understanding it requires understanding why it exists, which is the whole PKCE state problem.

---

## The PKCE state problem — why `_signer` exists

The standard OIDC flow uses the session cookie to carry the `state` nonce and `code_verifier` across the login redirect:

```
GET /auth/login
  → generate state + verifier
  → write both to session cookie
  → redirect to Keycloak

GET /auth/callback?code=...&state=...
  → read state from session cookie ← THIS FAILS
  → verify state matches
```

The problem is the redirect crosses origins: your app is on `localhost:8000`, Keycloak is on `localhost:8080`. When Keycloak redirects back to your callback, the browser treats it as a cross-site navigation. Depending on the browser and `SameSite` cookie policy, the session cookie written at `/auth/login` may not be sent back on the `/auth/callback` request — so `request.session` arrives empty.

The fix used here completely eliminates the session dependency for the PKCE handshake. Instead of storing the verifier in the session cookie, it is **packed into the `state` parameter itself** as a signed token. The `state` parameter travels to Keycloak and back via the URL, not via a cookie — so there is no cross-site cookie problem.

`URLSafeTimedSerializer` from the `itsdangerous` library creates a token that is:
- **Signed** — cannot be forged without knowing `app_secret_key`
- **Timed** — expires after 300 seconds (enforced in `parse_state_token`)
- **URL-safe** — can be passed as a query parameter without encoding issues

Java equivalent: a `HmacUtils.hmacSha256Hex(secretKey, payload)` signed JWT with an expiry claim.

---

## PKCE pair generation

```python
def generate_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge
```

PKCE (Proof Key for Code Exchange) is the mechanism that makes a public client (no client secret) secure. Here is what each line does:

```
verifier  = 64 random URL-safe bytes  →  "xK9mP2..."  (kept secret, sent later)
digest    = SHA-256(verifier)          →  raw 32 bytes
challenge = BASE64URL(digest)          →  "abc123..."  (sent to Keycloak now)
```

The flow in plain English:
1. You send `challenge` to Keycloak at login time
2. Keycloak stores it against the authorization code it issues
3. At callback time you send `verifier`
4. Keycloak recomputes `SHA-256(verifier)` and checks it matches the stored `challenge`
5. Only the party who generated the original `verifier` can pass this check

This proves you are the same party who initiated the login, without ever having a client secret. It is the standard way to secure browser and mobile apps where a secret cannot be kept confidential.

Java equivalent using Spring Security's PKCE support:
```java
// Spring handles this automatically with:
.oauth2Login(oauth2 -> oauth2
    .authorizationEndpoint(auth -> auth
        .authorizationRequestRepository(new HttpSessionOAuth2AuthorizationRequestRepository())
    )
)
// The PKCE pair generation and verification is handled inside Spring Security
```

Here it is done manually and explicitly.

---

## State token creation and parsing

```python
def make_state_token(nonce: str, verifier: str) -> str:
    payload = {"n": nonce, "v": verifier, "t": int(time.time())}
    return _signer.dumps(payload)
```

This packs three things into one signed URL-safe string:
- `"n"` — the nonce (random value for CSRF protection)
- `"v"` — the PKCE `code_verifier` (the secret that proves identity at callback)
- `"t"` — the timestamp (used by `max_age=300` expiry check)

The result looks like `eyJuIjogIi4uLiIsICJ2IjogIi4uLiIsICJ0IjogMTcwMH0.signature` and is passed as the `state` query parameter to Keycloak. Keycloak carries it through the login flow and sends it back unchanged on the redirect to `/auth/callback`.

```python
def parse_state_token(token: str) -> dict:
    try:
        return _signer.loads(token, max_age=300)
    except SignatureExpired:
        raise HTTPException(status_code=400, detail="State token expired...")
    except BadSignature:
        raise HTTPException(status_code=400, detail="Invalid state token signature")
```

`_signer.loads(token, max_age=300)` does three things atomically:
1. Verifies the HMAC signature — rejects if tampered
2. Checks the token is less than 300 seconds old — rejects if stale
3. Deserialises the payload back to a dict

The two exception types map to two distinct attack scenarios:
- `SignatureExpired` — legitimate user who took too long (> 5 min) to complete login
- `BadSignature` — attacker who tried to forge or modify the state parameter

---

## GET /auth/login

```python
@router.get("/login")
async def login(request: Request):
    nonce = secrets.token_urlsafe(16)
    verifier, challenge = generate_pkce_pair()
    state_token = make_state_token(nonce, verifier)

    params = {
        "response_type": "code",        # Authorization Code Flow
        "client_id": settings.keycloak_client_id,
        "redirect_uri": settings.redirect_uri,
        "scope": "openid profile email",
        "state": state_token,           # signed token carrying verifier
        "code_challenge": challenge,    # SHA-256(verifier)
        "code_challenge_method": "S256",
    }
    auth_url = f"{settings.keycloak_auth_url}?{urlencode(params)}"
    return RedirectResponse(auth_url)
```

This builds the authorization URL and redirects the browser to Keycloak. Nothing is written to the session — the entire PKCE state travels inside `state_token` in the URL.

The full redirect URL looks like:
```
http://localhost:8080/realms/chatbot-test/protocol/openid-connect/auth
  ?response_type=code
  &client_id=keycloak-chatbot
  &redirect_uri=http://localhost:8000/auth/callback
  &scope=openid+profile+email
  &state=eyJuIjogIi4uLiJ9.signature
  &code_challenge=abc123...
  &code_challenge_method=S256
```

Java equivalent:
```java
@GetMapping("/auth/login")
public RedirectView login() {
    String verifier = generateVerifier();
    String challenge = generateChallenge(verifier);
    String state = signedStateToken(verifier);

    UriComponentsBuilder builder = UriComponentsBuilder
        .fromUriString(keycloakAuthUrl)
        .queryParam("response_type", "code")
        .queryParam("client_id", clientId)
        .queryParam("state", state)
        .queryParam("code_challenge", challenge)
        .queryParam("code_challenge_method", "S256");

    return new RedirectView(builder.toUriString());
}
```

---

## GET /auth/callback

This is the most complex handler in the file. It does four sequential things.

### Step 1 — Recover the verifier from the state token

```python
async def callback(request: Request, code: str, state: str, error: Optional[str] = None):
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    payload = parse_state_token(state)
    verifier = payload["v"]
```

`code: str` and `state: str` — FastAPI automatically extracts these from the query string `?code=...&state=...`. No `@RequestParam` annotation needed — the parameter names match the query string keys.

`error: Optional[str] = None` — if Keycloak sends `?error=access_denied`, this catches it. The `= None` default means the parameter is optional — FastAPI won't reject requests that don't include it.

`parse_state_token(state)` verifies the signature and expiry, then returns the dict containing `"v"` (the verifier). If this fails the request is rejected immediately with a 400.

### Step 2 — Exchange the authorization code for tokens

```python
async with httpx.AsyncClient(timeout=30) as client:
    resp = await client.post(
        settings.keycloak_token_url,
        data={
            "grant_type": "authorization_code",
            "client_id": settings.keycloak_client_id,
            "redirect_uri": settings.redirect_uri,
            "code": code,
            "code_verifier": verifier,    # ← proves we initiated the login
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    tokens = resp.json()
```

`async with httpx.AsyncClient(...) as client` — this is an async context manager. It opens an HTTP connection pool, uses it for the request, then closes it cleanly when the `with` block exits. It is the async equivalent of Java's `try-with-resources` on an `HttpClient`.

`data={...}` sends a form-encoded body (`application/x-www-form-urlencoded`), not JSON. Keycloak's token endpoint requires this format — it is part of the OAuth2 spec.

**No `client_secret` is sent.** The `code_verifier` is the proof of identity for a public PKCE client. Keycloak computes `SHA-256(verifier)` and checks it matches the `code_challenge` it stored when `/auth/login` was called.

Keycloak responds with:
```json
{
  "access_token": "eyJ...",    (large JWT, 1-2KB)
  "refresh_token": "eyJ...",   (large JWT, 1-2KB)
  "id_token": "eyJ...",        (large JWT, 1-2KB)
  "expires_in": 300
}
```

### Step 3 — Fetch userinfo

```python
async with httpx.AsyncClient(timeout=30) as client:
    ui_resp = await client.get(
        settings.keycloak_userinfo_url,
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    userinfo = ui_resp.json() if ui_resp.status_code == 200 else {}
```

The access token is a Bearer token — it is sent in the `Authorization` header on every subsequent API call. Keycloak's `/userinfo` endpoint validates the token and returns the user's claims:
```json
{
  "sub": "cd79b477-...",
  "preferred_username": "testadmin",
  "email": "testadmin@gmail.com",
  "name": "Test User Admin",
  "realm_access": { "roles": ["default-roles-chatbot-test"] }
}
```

### Step 4 — Write only small user info to session

```python
request.session["user"] = {
    "sub": userinfo.get("sub", ""),
    "username": username,
    "email": userinfo.get("email", ""),
    "name": name,
    "roles": userinfo.get("realm_access", {}).get("roles", []),
}
```

This is a deliberate design decision. The full JWT tokens (`access_token`, `refresh_token`, `id_token`) are each 1–2KB. The session cookie is serialised JSON, signed, and base64-encoded — storing three JWTs would push the cookie well past the 4KB browser limit, causing the browser to silently drop it. That was the second bug you debugged.

The fix: store only the small user dict (~200 bytes). The access token is used once (to fetch userinfo) and then discarded. The session just needs to know who is logged in, not carry the full token.

### Step 5 — Return an HTML page instead of a bare redirect

```python
return HTMLResponse(content=f"""
  <meta http-equiv="refresh" content="1;url=/chat">
  <script>setTimeout(() => window.location.href = '/chat', 1000);</script>
  <p>✅ Signed in as <strong>{name}</strong> — redirecting…</p>
""")
```

A bare `302 RedirectResponse("/chat")` would race against the `Set-Cookie` header — some browsers navigate to `/chat` before fully committing the cookie, so `/chat` sees an empty session and redirects back to `/`.

Returning a full HTML page gives the browser a complete round-trip to commit the `Set-Cookie` header before any navigation happens. The 1-second delay via both `meta refresh` and `setTimeout` ensures the cookie is written before `/chat` is requested.

---

## GET /auth/logout

```python
@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    params = {
        "post_logout_redirect_uri": settings.app_base_url,
        "client_id": settings.keycloak_client_id,
    }
    logout_url = f"{settings.keycloak_logout_url}?{urlencode(params)}"
    return RedirectResponse(logout_url)
```

Two things happen:

**`request.session.clear()`** — wipes the server-side session dict. On the response, `SessionMiddleware` will write an empty signed cookie, effectively logging the user out of this app.

**Redirect to Keycloak logout URL** — this performs SSO logout. Without this step, the user's Keycloak SSO session would still be active — they could immediately log back in without entering credentials. Sending them to Keycloak's logout endpoint invalidates the Keycloak session too. `post_logout_redirect_uri` tells Keycloak where to send the browser after it has completed logout.

---

## `require_auth` — the inline security guard

```python
def require_auth(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
```

This is not a route handler — it has no `@router` decorator. It is a plain function used as a FastAPI dependency via `Depends(require_auth)` in `main.py`:

```python
async def chat_api(
    body: ChatRequest,
    request: Request,
    user: dict = Depends(require_auth),   # ← called before chat_api runs
):
```

FastAPI calls `require_auth(request)` before calling `chat_api`. If it raises `HTTPException(401)`, FastAPI short-circuits and `chat_api` never executes. If it returns the user dict, that value is injected as the `user` parameter.

Java equivalent:
```java
// Spring Security equivalent:
@PreAuthorize("isAuthenticated()")

// Or as a HandlerInterceptor:
@Override
public boolean preHandle(HttpServletRequest request, ...) {
    if (session.getAttribute("user") == null) {
        response.sendError(401);
        return false;   // ← handler method never runs
    }
    return true;
}
```

The difference is visibility: in Spring Security the guard is wired globally or via annotations that are separate from the method signature. With `Depends()` the guard is declared right in the function signature — you can see at a glance that `chat_api` requires authentication without reading any other file.

---

## The complete login flow end to end

```
Browser: GET /auth/login
    │
    ├── generate nonce + PKCE pair (verifier, challenge)
    ├── pack {nonce, verifier, timestamp} into signed state_token
    └── 302 → Keycloak /auth?state=state_token&code_challenge=challenge

Keycloak: shows login page
    │
    └── user authenticates
    └── 302 → /auth/callback?code=AUTH_CODE&state=state_token

Browser: GET /auth/callback?code=AUTH_CODE&state=state_token
    │
    ├── parse_state_token(state) → verify signature + expiry → recover verifier
    ├── POST keycloak/token {code, code_verifier} → get access_token
    ├── GET keycloak/userinfo {Bearer access_token} → get user claims
    ├── session["user"] = {sub, username, email, name, roles}
    └── HTMLResponse with 1s delay → /chat

Browser: GET /chat
    │
    ├── session["user"] exists → render chat.html
    └── session empty → redirect to /
```