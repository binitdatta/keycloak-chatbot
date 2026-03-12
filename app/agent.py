"""LangGraph agent: parses English prompt → JSON payload → invokes Keycloak Admin REST API."""
from __future__ import annotations

import json
import re
from typing import Any, TypedDict, Optional, Annotated
import operator

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END

from app.config import get_settings
from app.keycloak_client import keycloak_admin

settings = get_settings()

# ── LLM ───────────────────────────────────────────────────────────────────────
llm = ChatAnthropic(
    model="claude-sonnet-4-5",
    api_key=settings.anthropic_api_key,
    max_tokens=4096,
)

SYSTEM_PROMPT = """You are a Keycloak Admin REST API expert assistant.
Your job is to:
1. Understand the user's English request about Keycloak administration
2. Determine the correct Keycloak Admin REST API operation
3. Extract and construct the proper JSON payload
4. Return a structured response

You MUST always respond with valid JSON in this exact format:
{
  "intent": "one of: create_user|update_user|delete_user|get_users|get_user|reset_password|create_client|update_client|delete_client|get_clients|create_role|update_role|delete_role|get_roles|assign_roles|create_group|update_group|delete_group|get_groups|add_to_group|create_idp|update_idp|delete_idp|get_idps|get_realm|update_realm|create_client_scope|get_client_scopes|create_protocol_mapper|unknown",
  "resource_id": "string or null - user ID, client UUID, role name, group ID, IDP alias etc. if needed",
  "payload": { ... },
  "explanation": "Brief human-readable explanation of what will be done",
  "warning": "optional warning if destructive operation"
}

Rules:
- For create_user: payload must include at minimum {"username": "...", "enabled": true}
- For credentials/passwords: use {"credentials": [{"type": "password", "value": "...", "temporary": false}]}
- For client creation: include {"clientId": "...", "enabled": true, "protocol": "openid-connect"}
- For identity providers: include {"alias": "...", "providerId": "oidc|saml|google|github|facebook|microsoft|twitter", "config": {...}}
- For realm updates: include only the fields to change
- If the request is ambiguous, set intent to "unknown" and ask for clarification in explanation
- Never fabricate IDs; if an ID is required but not provided, set resource_id to null and explain
- Always set "enabled": true for new users/clients unless explicitly disabled
- For email: include "email" field; for firstName/lastName include those fields
"""

# ── Graph State ───────────────────────────────────────────────────────────────
class AgentState(TypedDict):
    user_message: str
    parsed: Optional[dict]
    api_result: Optional[dict]
    final_response: str
    error: Optional[str]


# ── Nodes ─────────────────────────────────────────────────────────────────────
async def parse_intent_node(state: AgentState) -> AgentState:
    """Use Claude to parse user intent and extract JSON payload."""
    try:
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=state["user_message"]),
        ]
        response = await llm.ainvoke(messages)
        raw = response.content.strip()

        # Extract JSON from response
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = json.loads(raw)

        return {**state, "parsed": parsed, "error": None}
    except Exception as e:
        return {
            **state,
            "parsed": None,
            "error": f"Failed to parse intent: {str(e)}",
        }


async def execute_api_node(state: AgentState) -> AgentState:
    """Execute the appropriate Keycloak Admin REST API call."""
    if state.get("error") or not state.get("parsed"):
        return state

    parsed = state["parsed"]
    intent = parsed.get("intent", "unknown")
    payload = parsed.get("payload", {})
    resource_id = parsed.get("resource_id")

    if intent == "unknown":
        return {
            **state,
            "api_result": {"status": "skipped", "message": parsed.get("explanation", "Unknown intent")},
        }

    try:
        status, body = await _dispatch(intent, payload, resource_id)
        return {
            **state,
            "api_result": {
                "status_code": status,
                "success": 200 <= status < 300,
                "body": body,
            },
        }
    except Exception as e:
        return {
            **state,
            "api_result": {"status": "error", "message": str(e)},
            "error": str(e),
        }


async def _dispatch(intent: str, payload: dict, resource_id: Optional[str]) -> tuple[int, Any]:
    """Dispatch to the correct keycloak_admin method."""
    match intent:
        # Users
        case "create_user":
            return await keycloak_admin.create_user(payload)
        case "update_user":
            return await keycloak_admin.update_user(resource_id, payload)
        case "delete_user":
            return await keycloak_admin.delete_user(resource_id)
        case "get_users":
            return await keycloak_admin.get_users(payload if payload else None)
        case "get_user":
            return await keycloak_admin.get_user(resource_id)
        case "reset_password":
            return await keycloak_admin.reset_user_password(resource_id, payload)
        # Clients
        case "create_client":
            return await keycloak_admin.create_client(payload)
        case "update_client":
            return await keycloak_admin.update_client(resource_id, payload)
        case "delete_client":
            return await keycloak_admin.delete_client(resource_id)
        case "get_clients":
            return await keycloak_admin.get_clients(payload if payload else None)
        # Roles
        case "create_role":
            return await keycloak_admin.create_realm_role(payload)
        case "update_role":
            return await keycloak_admin.update_realm_role(resource_id, payload)
        case "delete_role":
            return await keycloak_admin.delete_realm_role(resource_id)
        case "get_roles":
            return await keycloak_admin.get_realm_roles()
        case "assign_roles":
            roles = payload.get("roles", [])
            return await keycloak_admin.assign_realm_roles_to_user(resource_id, roles)
        # Groups
        case "create_group":
            return await keycloak_admin.create_group(payload)
        case "update_group":
            return await keycloak_admin.update_group(resource_id, payload)
        case "delete_group":
            return await keycloak_admin.delete_group(resource_id)
        case "get_groups":
            return await keycloak_admin.get_groups()
        case "add_to_group":
            group_id = payload.get("groupId", resource_id)
            user_id = payload.get("userId")
            return await keycloak_admin.add_user_to_group(user_id, group_id)
        # Identity Providers
        case "create_idp":
            return await keycloak_admin.create_identity_provider(payload)
        case "update_idp":
            return await keycloak_admin.update_identity_provider(resource_id, payload)
        case "delete_idp":
            return await keycloak_admin.delete_identity_provider(resource_id)
        case "get_idps":
            return await keycloak_admin.get_identity_providers()
        # Realm
        case "get_realm":
            return await keycloak_admin.get_realm()
        case "update_realm":
            return await keycloak_admin.update_realm(payload)
        # Client Scopes
        case "create_client_scope":
            return await keycloak_admin.create_client_scope(payload)
        case "get_client_scopes":
            return await keycloak_admin.get_client_scopes()
        # Protocol Mappers
        case "create_protocol_mapper":
            client_id = payload.pop("clientId", resource_id)
            return await keycloak_admin.create_protocol_mapper(client_id, payload)
        case _:
            return 400, {"error": f"Unknown intent: {intent}"}


async def format_response_node(state: AgentState) -> AgentState:
    """Format a human-readable final response."""
    if state.get("error") and not state.get("api_result"):
        return {**state, "final_response": f"❌ Error: {state['error']}"}

    parsed = state.get("parsed", {})
    api_result = state.get("api_result", {})
    intent = parsed.get("intent", "unknown")
    explanation = parsed.get("explanation", "")
    warning = parsed.get("warning", "")

    if intent == "unknown" or api_result.get("status") == "skipped":
        return {**state, "final_response": f"ℹ️ {explanation}"}

    success = api_result.get("success", False)
    status_code = api_result.get("status_code", 0)
    body = api_result.get("body", {})

    if success:
        msg = f"✅ **{explanation}**\n\n"
        msg += f"**Status:** {status_code}\n\n"
        if body and body != "" and body != []:
            if isinstance(body, (dict, list)):
                msg += f"**Response:**\n```json\n{json.dumps(body, indent=2)}\n```"
            else:
                msg += f"**Response:** {body}"
        if warning:
            msg += f"\n\n⚠️ **Warning:** {warning}"
    else:
        error_detail = body if body else state.get("error", "Unknown error")
        msg = f"❌ **Operation failed** (HTTP {status_code})\n\n"
        msg += f"**Intent:** {explanation}\n\n"
        msg += f"**Error:**\n```json\n{json.dumps(error_detail, indent=2) if isinstance(error_detail, (dict, list)) else error_detail}\n```"

    return {**state, "final_response": msg}


# ── Graph ──────────────────────────────────────────────────────────────────────
def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("parse_intent", parse_intent_node)
    graph.add_node("execute_api", execute_api_node)
    graph.add_node("format_response", format_response_node)

    graph.set_entry_point("parse_intent")
    graph.add_edge("parse_intent", "execute_api")
    graph.add_edge("execute_api", "format_response")
    graph.add_edge("format_response", END)

    return graph.compile()


keycloak_agent = build_graph()


async def run_agent(user_message: str) -> dict:
    """Run the LangGraph agent and return structured result."""
    initial_state: AgentState = {
        "user_message": user_message,
        "parsed": None,
        "api_result": None,
        "final_response": "",
        "error": None,
    }
    result = await keycloak_agent.ainvoke(initial_state)
    return {
        "response": result["final_response"],
        "parsed": result.get("parsed"),
        "api_result": result.get("api_result"),
    }