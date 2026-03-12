"""Keycloak SSO authentication via OIDC Authorization Code Flow."""
import httpx
import secrets
import hashlib
import base64
import logging
import time
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])
_signer = URLSafeTimedSerializer(settings.app_secret_key, salt="pkce-state")


def generate_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def make_state_token(nonce: str, verifier: str) -> str:
    payload = {"n": nonce, "v": verifier, "t": int(time.time())}
    return _signer.dumps(payload)


def parse_state_token(token: str) -> dict:
    try:
        return _signer.loads(token, max_age=300)
    except SignatureExpired:
        raise HTTPException(status_code=400, detail="State token expired — please try logging in again")
    except BadSignature:
        raise HTTPException(status_code=400, detail="Invalid state token signature")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"State token error: {e}")


@router.get("/login")
async def login(request: Request):
    nonce = secrets.token_urlsafe(16)
    verifier, challenge = generate_pkce_pair()
    state_token = make_state_token(nonce, verifier)

    params = {
        "response_type": "code",
        "client_id": settings.keycloak_client_id,
        "redirect_uri": settings.redirect_uri,
        "scope": "openid profile email",
        "state": state_token,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    from urllib.parse import urlencode
    auth_url = f"{settings.keycloak_auth_url}?{urlencode(params)}"
    logger.warning(f"[LOGIN] redirecting to Keycloak...")
    return RedirectResponse(auth_url)


@router.get("/callback")
async def callback(request: Request, code: str, state: str, error: Optional[str] = None):
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    logger.warning(f"[CALLBACK] parsing state token...")
    payload = parse_state_token(state)
    verifier = payload["v"]
    logger.warning(f"[CALLBACK] verifier recovered OK")

    # Exchange code for tokens
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            settings.keycloak_token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.keycloak_client_id,
                "redirect_uri": settings.redirect_uri,
                "code": code,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail=f"Token exchange failed: {resp.text}")
        tokens = resp.json()

    # Fetch userinfo
    async with httpx.AsyncClient(timeout=30) as client:
        ui_resp = await client.get(
            settings.keycloak_userinfo_url,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        userinfo = ui_resp.json() if ui_resp.status_code == 200 else {}

    username = userinfo.get("preferred_username", "unknown")
    name = userinfo.get("name", username)
    logger.warning(f"[CALLBACK] logged in as: {username}")

    # Store ONLY small user info — NOT the full JWT tokens
    # Full JWTs are large (2-4KB) and cause the cookie to exceed the 4KB
    # browser limit, which makes the browser silently drop the entire cookie
    request.session["user"] = {
        "sub": userinfo.get("sub", ""),
        "username": username,
        "email": userinfo.get("email", ""),
        "name": name,
        "roles": userinfo.get("realm_access", {}).get("roles", []),
    }

    logger.warning(f"[CALLBACK] session written, user={username}")

    return HTMLResponse(content=f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="1;url=/chat">
  <title>Signing in…</title>
  <style>
    body {{ font-family: sans-serif; background: #080d14; color: #00e5c3;
           display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    p {{ font-size: 1.1rem; }}
  </style>
</head>
<body>
  <p>✅ Signed in as <strong>{name}</strong> — redirecting…</p>
  <script>setTimeout(() => window.location.href = '/chat', 1000);</script>
</body>
</html>
""")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    from urllib.parse import urlencode
    params = {
        "post_logout_redirect_uri": settings.app_base_url,
        "client_id": settings.keycloak_client_id,
    }
    logout_url = f"{settings.keycloak_logout_url}?{urlencode(params)}"
    return RedirectResponse(logout_url)


@router.get("/me")
async def me(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return JSONResponse(user)


def require_auth(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


@router.get("/success")
async def success(request: Request):
    """Debug route — shows session contents after login."""
    user = request.session.get("user")
    all_keys = list(request.session.keys())
    logger.warning(f"[SUCCESS] session_keys={all_keys}, user={user}")
    return JSONResponse({
        "session_keys": all_keys,
        "user": user,
        "message": "Session debug info"
    })