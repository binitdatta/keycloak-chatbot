Let me explain `agent.py` layer by layer, covering every concept from first principles.

---

## What `agent.py` is — the big picture

This file implements an **AI pipeline** using three components working together:

| Component | Role | Java analogy |
|---|---|---|
| `ChatAnthropic` (Claude) | Understands English, produces structured JSON | A smart parser/classifier service |
| `AgentState` | The data bag flowing through the pipeline | A `@RequestScoped` bean carrying shared state |
| `LangGraph` (`StateGraph`) | Orchestrates the pipeline steps in order | A Spring Batch `Job` with sequential `Step`s |

The user types English. The pipeline converts it to a Keycloak API call and returns a human-readable result. Nothing in `keycloak_client.py` ever sees English — it only ever sees typed Python method calls with proper dicts.

---

## Module-level setup (runs once at import time)

```python
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END
from app.keycloak_client import keycloak_admin

settings = get_settings()

llm = ChatAnthropic(
    model="claude-sonnet-4-5",
    api_key=settings.anthropic_api_key,
    max_tokens=4096,
)
```

`llm` is created **once** at import time and reused for every request. It is a stateless client — calling `llm.ainvoke(messages)` sends an HTTPS request to Anthropic's API and returns a response. No state is kept between calls.

`keycloak_admin` is also imported here — it is the singleton `KeycloakAdminClient` instance from `keycloak_client.py`.

Java equivalent:
```java
@Bean  // singleton, created once at startup
public ChatClient claudeClient() {
    return new AnthropicChatClient(apiKey, "claude-sonnet-4-5");
}
```

---

## The System Prompt — the most important string in the file

```python
SYSTEM_PROMPT = """You are a Keycloak Admin REST API expert assistant.
You MUST always respond with valid JSON in this exact format:
{
  "intent": "create_user|update_user|...|unknown",
  "resource_id": "string or null",
  "payload": { ... },
  "explanation": "Brief human-readable explanation",
  "warning": "optional warning if destructive"
}
Rules:
- For create_user: payload must include {"username": "...", "enabled": true}
- Never fabricate IDs...
"""
```

This is the **contract** between your code and the AI. It does three things:

**1. Constrains the output format.** Without this, Claude would respond in conversational English. With it, Claude always responds with parseable JSON that your code can work with.

**2. Defines the vocabulary.** The `intent` field can only be one of 25 known values. This turns the infinite space of possible English requests into a finite set of operations your `_dispatch()` function can handle.

**3. Encodes business rules.** Lines like "Never fabricate IDs" and "Always set enabled: true" are business logic expressed in English rather than code. If you want to change the behaviour of the AI, you edit this string — not the Python code.

Java analogy: imagine a `@Prompt` annotation on a service method that tells an AI assistant the exact JSON schema it must return, plus validation rules. There is no direct Java equivalent — this is a new programming paradigm where you program AI behaviour through natural language instructions.

---

## `AgentState` — the shared data bag

```python
class AgentState(TypedDict):
    user_message: str
    parsed: Optional[dict]
    api_result: Optional[dict]
    final_response: str
    error: Optional[str]
```

`TypedDict` is a Python type that defines a **dictionary with known, typed keys**. It is not a class in the OOP sense — there is no `__init__`, no methods, no inheritance chain. It is purely a type hint that tells the type checker and LangGraph what keys this dict must have.

This dict is the single object that gets passed into every node, mutated (by creating a new copy), and passed to the next node. Every node reads from it and returns an updated version.

Java equivalent: a Spring Batch `JobExecutionContext` or a Spring Integration `MessageHeaders` — a shared map that flows through pipeline steps:

```java
public class AgentState {
    private final String userMessage;
    private Map<String, Object> parsed;      // null until parse_intent_node runs
    private Map<String, Object> apiResult;   // null until execute_api_node runs
    private String finalResponse;            // null until format_response_node runs
    private String error;
}
```

The initial state entering the pipeline looks like:
```python
{
    "user_message": "Create a user alice@example.com",
    "parsed": None,        # ← filled by Node 1
    "api_result": None,    # ← filled by Node 2
    "final_response": "",  # ← filled by Node 3
    "error": None,
}
```

---

## Node 1: `parse_intent_node`

```python
async def parse_intent_node(state: AgentState) -> AgentState:
    try:
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=state["user_message"]),
        ]
        response = await llm.ainvoke(messages)
        raw = response.content.strip()

        json_match = re.search(r'\{[\s\S]*\}', raw)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = json.loads(raw)

        return {**state, "parsed": parsed, "error": None}
    except Exception as e:
        return {**state, "parsed": None, "error": f"Failed to parse intent: {str(e)}"}
```

**`messages = [SystemMessage(...), HumanMessage(...)]`**

This is the conversation structure the Anthropic API expects. `SystemMessage` sets the behaviour and rules. `HumanMessage` is the user's actual input. This is identical to what you see in the Claude web UI — a system prompt followed by a user message.

**`await llm.ainvoke(messages)`**

`ainvoke` is the async version of `invoke`. The `a` prefix is the Python convention for async methods. This makes an HTTPS call to `api.anthropic.com`, sends the messages, and waits for the response. While waiting, the Python event loop can serve other requests — this is why `async/await` matters here.

Claude sees the system prompt and user message, then responds with JSON like:
```json
{
  "intent": "create_user",
  "resource_id": null,
  "payload": {
    "username": "alice",
    "email": "alice@example.com",
    "firstName": "Alice",
    "enabled": true
  },
  "explanation": "Creating user alice with email alice@example.com"
}
```

**`re.search(r'\{[\s\S]*\}', raw)`**

Even though Claude is instructed to return only JSON, it occasionally wraps the JSON in markdown code fences like `` ```json ... ``` `` or adds a sentence before it. The regex `\{[\s\S]*\}` extracts the first `{...}` block found in the response, skipping any surrounding text. `[\s\S]*` means "any character including newlines" — the `\s\S` trick is needed because `.` does not match newlines by default in Python.

**`return {**state, "parsed": parsed, "error": None}`**

`{**state, "parsed": parsed}` is Python's dict spread operator. It creates a **new dict** that is a copy of `state` with the `"parsed"` key overwritten. Nodes never mutate the state in place — they always return a new copy. This is the same immutability pattern used in Redux (JavaScript) and functional programming generally.

Java equivalent:
```java
AgentState newState = state.toBuilder()
    .parsed(parsed)
    .error(null)
    .build();
return newState;
```

---

## Node 2: `execute_api_node`

```python
async def execute_api_node(state: AgentState) -> AgentState:
    if state.get("error") or not state.get("parsed"):
        return state   # ← short-circuit: if Node 1 failed, skip this node

    parsed = state["parsed"]
    intent = parsed.get("intent", "unknown")
    payload = parsed.get("payload", {})
    resource_id = parsed.get("resource_id")

    if intent == "unknown":
        return {**state, "api_result": {"status": "skipped", ...}}

    try:
        status, body = await _dispatch(intent, payload, resource_id)
        return {**state, "api_result": {
            "status_code": status,
            "success": 200 <= status < 300,
            "body": body,
        }}
    except Exception as e:
        return {**state, "api_result": {"status": "error", ...}, "error": str(e)}
```

This node reads `state["parsed"]` (produced by Node 1) and makes the actual API call. The `if state.get("error")` check at the top is the pipeline's error propagation mechanism — if Node 1 failed, Node 2 skips itself and passes the state through unchanged to Node 3 which will format the error message.

`200 <= status < 300` — HTTP success range check. Returns `True` for 200, 201, 204 etc.

---

## `_dispatch` — the intent router

```python
async def _dispatch(intent: str, payload: dict, resource_id: Optional[str]) -> tuple[int, Any]:
    match intent:
        case "create_user":
            return await keycloak_admin.create_user(payload)
        case "update_user":
            return await keycloak_admin.update_user(resource_id, payload)
        case "get_roles":
            return await keycloak_admin.get_realm_roles()
        case "assign_roles":
            roles = payload.get("roles", [])
            return await keycloak_admin.assign_realm_roles_to_user(resource_id, roles)
        case _:
            return 400, {"error": f"Unknown intent: {intent}"}
```

`match intent: case "create_user":` is Python 3.10+ structural pattern matching. It is exactly Java's `switch` statement:

```java
switch (intent) {
    case "create_user":
        return keycloakAdmin.createUser(payload);
    case "update_user":
        return keycloakAdmin.updateUser(resourceId, payload);
    default:
        return new Tuple<>(400, Map.of("error", "Unknown intent: " + intent));
}
```

`case _:` is the default/fallthrough case — the underscore means "match anything".

This is the **only place in the codebase that knows the mapping from intent string to API method**. If you add a new Keycloak operation, you add one `case` here and one method in `keycloak_client.py`. Nothing else changes.

---

## Node 3: `format_response_node`

```python
async def format_response_node(state: AgentState) -> AgentState:
    if state.get("error") and not state.get("api_result"):
        return {**state, "final_response": f"❌ Error: {state['error']}"}

    parsed = state.get("parsed", {})
    api_result = state.get("api_result", {})
    success = api_result.get("success", False)
    status_code = api_result.get("status_code", 0)
    body = api_result.get("body", {})

    if success:
        msg = f"✅ **{explanation}**\n\n**Status:** {status_code}\n\n"
        if body:
            msg += f"**Response:**\n```json\n{json.dumps(body, indent=2)}\n```"
    else:
        msg = f"❌ **Operation failed** (HTTP {status_code})\n\n"
        msg += f"**Error:**\n```json\n{json.dumps(error_detail, indent=2)}\n```"

    return {**state, "final_response": msg}
```

This node converts the raw API result into **markdown** that the chat UI's `marked.parse()` will render as formatted HTML. The `**bold**` and ` ```json ``` ` are markdown syntax. This node has no business logic — it is purely a presentation formatter. Separating it from `execute_api_node` means you can change how results are displayed without touching the API call logic.

---

## Building the LangGraph

```python
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
```

`StateGraph(AgentState)` — creates a graph where every node receives and returns an `AgentState` dict.

`add_node("name", function)` — registers a node. The string name is used in `add_edge` to connect nodes.

`add_edge("A", "B")` — declares that after node A completes, node B runs next.

`graph.compile()` — validates the graph (checks for disconnected nodes, missing edges) and returns an executable object. This is like compiling a Spring Batch job definition — it checks the wiring before any data flows through.

`keycloak_agent = build_graph()` — this runs **once at import time**. The compiled graph is a module-level singleton reused for every request.

The graph you have built is the simplest possible LangGraph — a linear chain:

```
parse_intent → execute_api → format_response → END
```

LangGraph supports much more complex patterns: conditional branching (different nodes based on intent), loops (retry on failure), parallel branches, and human-in-the-loop checkpoints. Your graph does not need any of those — the linear chain is the right choice for this use case.

Java Spring Batch equivalent:
```java
@Bean
public Job keycloakAgentJob() {
    return jobBuilder.get("keycloakAgent")
        .start(parseIntentStep())
        .next(executeApiStep())
        .next(formatResponseStep())
        .build();
}
```

---

## `run_agent` — the public entry point

```python
async def run_agent(user_message: str) -> dict:
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
```

This is the only function `main.py` calls. It hides the entire LangGraph machinery behind a simple interface — you give it a string, you get back a dict. `main.py` does not know or care that there is a graph, three nodes, or an LLM involved.

`keycloak_agent.ainvoke(initial_state)` — runs the full graph asynchronously. LangGraph calls each node in order, passing the state dict through. When `format_response` finishes, `ainvoke` returns the final state.

---

## The complete data flow through one request

```
run_agent("Create a user alice@example.com")
    │
    ▼
initial_state = {user_message: "Create a user...", parsed: None, ...}
    │
    ▼ Node 1: parse_intent_node
    │  → llm.ainvoke([SYSTEM_PROMPT, "Create a user..."])
    │  → Claude returns JSON string
    │  → regex extracts JSON, json.loads parses it
    │  state["parsed"] = {intent: "create_user", payload: {username: "alice", ...}}
    │
    ▼ Node 2: execute_api_node
    │  → _dispatch("create_user", {username: "alice",...}, None)
    │  → keycloak_admin.create_user({username: "alice",...})
    │  → _get_admin_token() → POST master/token → "eyJ..."
    │  → POST /admin/realms/chatbot-test/users {username: "alice",...}
    │  → Keycloak returns HTTP 201
    │  state["api_result"] = {status_code: 201, success: True, body: ""}
    │
    ▼ Node 3: format_response_node
    │  → success=True, status_code=201
    │  → builds markdown string
    │  state["final_response"] = "✅ **Creating user alice...** \n\n**Status:** 201"
    │
    ▼ keycloak_agent.ainvoke() returns final state
    │
    ▼ run_agent() returns
    {
      "response": "✅ **Creating user alice...**\n\n**Status:** 201",
      "parsed": {intent: "create_user", payload: {...}},
      "api_result": {status_code: 201, success: True, body: ""}
    }
    │
    ▼ main.py wraps in ChatResponse → JSON → browser
    │
    ▼ chat.html: marked.parse(data.response) renders markdown as HTML
```