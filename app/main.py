"""Main FastAPI application."""
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from typing import Optional

from app.config import get_settings
from app.auth import router as auth_router, require_auth
from app.agent import run_agent

settings = get_settings()

app = FastAPI(
    title="Keycloak Admin Chatbot",
    description="AI-powered Keycloak administration via natural language",
    version="1.0.0",
)

# Session middleware — same_site="lax" is critical for OIDC redirect flows
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret_key,
    session_cookie="kc_chatbot_session",
    max_age=3600,
    same_site="lax",
    https_only=False,
)

# Static files & templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Routers
app.include_router(auth_router)


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/")
    return templates.TemplateResponse(
        "chat.html", {
            "request": request,
            "user": user,
            "keycloak_realm": settings.keycloak_realm,
        }
    )


# ── API ────────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    parsed: Optional[dict] = None
    api_result: Optional[dict] = None

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    user = request.session.get("user")
    return templates.TemplateResponse(
        "landing.html", {"request": request, "user": user}   # now serves why.html content
    )

@app.get("/features", response_class=HTMLResponse)
async def features_page(request: Request):
    user = request.session.get("user")
    return templates.TemplateResponse(
        "features.html", {"request": request, "user": user}  # now serves old landing content
    )

# Remove or update the /why route — it now lives at /
@app.get("/why", response_class=HTMLResponse)
async def why_page(request: Request):
    return RedirectResponse("/")   # /why now just redirects to home

@app.post("/api/chat", response_model=ChatResponse)
async def chat_api(
    body: ChatRequest,
    request: Request,
    user: dict = Depends(require_auth),
):
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    result = await run_agent(body.message.strip())
    return ChatResponse(
        response=result["response"],
        parsed=result.get("parsed"),
        api_result=result.get("api_result"),
    )


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "keycloak-chatbot"}


@app.get("/api/examples")
async def get_examples():
    return {
        "examples": [
            {"category": "Users", "prompt": "Create a user with username john.doe, email john@example.com, first name John and last name Doe"},
            {"category": "Users", "prompt": "List all users in the realm"},
            {"category": "Users", "prompt": "Reset password for user ID abc123 to SecurePass@2024 (temporary)"},
            {"category": "Clients", "prompt": "Create an OpenID Connect client called my-spa-app with redirect URI https://myapp.com/*"},
            {"category": "Clients", "prompt": "List all clients in the realm"},
            {"category": "Clients", "prompt": "Update client my-spa-app to enable direct access grants"},
            {"category": "Roles", "prompt": "Create a realm role called app-admin with description Application administrator"},
            {"category": "Roles", "prompt": "List all realm roles"},
            {"category": "Groups", "prompt": "Create a group called developers"},
            {"category": "Identity Providers", "prompt": "Create a Google identity provider with client ID my-google-client and client secret mysecret"},
            {"category": "Identity Providers", "prompt": "List all identity providers"},
            {"category": "Realm", "prompt": "Update the realm to enable user registration and remember me"},
            {"category": "Client Scopes", "prompt": "List all client scopes in the realm"},
        ]
    }