"""Front Desk agent: Amazon Nova on Bedrock talking to the customer and using the MCP server.

For each customer utterance:
1. read the MCP server's tools (tools/list) and convert them to the Converse API format;
2. send the conversation to the model on Bedrock;
3. while the model asks for tools, run each one on the MCP server (tools/call) and return the result;
4. answer with the model's final sentence plus the list of calls made (name, arguments, latency).

Configuration through environment variables:
- FRONTDESK_MODEL: one or more models / inference profiles, comma-separated, in order of preference
  (default: us.amazon.nova-lite-v1:0, us.amazon.nova-micro-v1:0, us.amazon.nova-2-lite-v1:0). If a model
  has no quota or no access, the agent moves on to the next one.
- FRONTDESK_MCP_URL: MCP server address (default: this same server, http://127.0.0.1:PORT/mcp;
  "local" uses the in-process server, without HTTP).
- AWS_REGION / AWS_PROFILE: AWS Region and profile (default: us-east-2 and boto3's default credentials).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import anyio

from mcp import Client

from frontdesk import schedule

try:  # Python 3.11+
    BaseExceptionGroup
except NameError:  # Python 3.10: backport installed with anyio
    from exceptiongroup import BaseExceptionGroup

DEFAULT_MODELS = "us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0,us.amazon.nova-2-lite-v1:0"
MODEL_PAUSE_S = 3600  # after a "no quota", skip that model for 1 hour
MAX_ROUNDS = 6  # model -> tool round trips per utterance
MAX_HISTORY = 20  # messages kept per session
MAX_SESSIONS = 200

INSTRUCTIONS = """You are the virtual receptionist at {business}, a barbershop, answering by voice in American English.
Today is {today} and the time is {time}.

Rules:
- Always use the tools to look up services, open slots, and to book, cancel, reschedule and read the daily summary. Never make up times, prices or appointments.
- Every tool returns a "speech" field. Base your answer on it, without adding information the tool did not return.
- Before booking you need the customer's name, the service, the day and the time. If something is missing, ask only for what is missing.
- Pass dates and times to the tools the way the customer said them (e.g. "Friday", "tomorrow", "9 AM").
- Keep it short, two sentences at most, friendly and natural to be spoken out loud. No lists, no markdown, no emojis.
- If the customer says their name before booking, use customer_history to greet returning customers; if they want "the usual", book with service "the usual".
- If the time they want is taken or nothing is open, offer the waitlist and use join_waitlist if they accept.
- If the person is the owner asking about the schedule, use daily_summary. If the owner wants to take time off a barber's schedule, use block_time."""


@dataclass
class Session:
    messages: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


_sessions: dict[str, Session] = {}


def _session(session_id: str | None) -> tuple[str, Session]:
    sid = session_id or uuid.uuid4().hex
    if sid not in _sessions:
        if len(_sessions) >= MAX_SESSIONS:
            oldest = min(_sessions, key=lambda k: _sessions[k].created_at)
            _sessions.pop(oldest, None)
        _sessions[sid] = Session()
    return sid, _sessions[sid]


class AgentError(RuntimeError):
    """Agent failure with a message ready to show on screen."""


def _root(e: BaseException) -> BaseException:
    """The MCP client runs inside a TaskGroup, which wraps the real error in an ExceptionGroup."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


def explain_error(e: BaseException) -> str:
    e = _root(e)
    name = type(e).__name__
    code = _code(e)
    login = "Run `aws login --region us-east-2 --profile juan` and start the server with AWS_PROFILE=juan."
    if name in ("NoCredentialsError", "TokenRetrievalError", "UnauthorizedSSOTokenError") or code in (
        "UnrecognizedClientException", "ExpiredTokenException", "InvalidSignatureException"
    ):
        return f"No valid AWS credentials ({code or name}). {login}"
    if name == "MissingDependencyException":
        return ("An AWS dependency is missing to read the `aws login` credentials. "
                'Run `pip install -r requirements.txt` (or `pip install "boto3[crt]"`) and restart the server.')
    if name == "ProfileNotFound":
        return f"AWS profile not found ({e}). Check the AWS_PROFILE variable."
    if code == "ThrottlingException" and "per day" in str(e).lower():
        return ("The AWS account has no daily Bedrock quota for the Nova models tried "
                f"({', '.join(models())}). Ask AWS Support for a quota increase, "
                "or run the server with FRONTDESK_AGENT=rehearsal to test without Bedrock.")
    if code == "AccessDeniedException":
        return f"Bedrock denied access to the model {model()}: {e}"
    if code in ("ValidationException", "ResourceNotFoundException"):
        return f"Bedrock rejected the request ({code}) for the model {model()}: {e}"
    return f"{name}: {e}"


def end_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


# ------------------------------------------------------------- MCP -> Bedrock

def _simplify_schema(schema: Any) -> Any:
    """Reduces the MCP JSON Schema to the subset the Converse API accepts.

    The SDK generates optional fields as anyOf [string, null] with default null; Bedrock
    prefers a plain type, and the field stays optional because it is not in "required".
    """
    if isinstance(schema, list):
        return [_simplify_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    s = {k: v for k, v in schema.items() if k not in ("title", "default")}
    options = s.get("anyOf")
    if isinstance(options, list):
        non_null = [o for o in options if not (isinstance(o, dict) and o.get("type") == "null")]
        if len(non_null) == 1:
            s.pop("anyOf")
            s = {**_simplify_schema(non_null[0]), **s}
    return {k: _simplify_schema(v) for k, v in s.items()}


def tools_for_bedrock(tools) -> list[dict]:
    specs = []
    for t in tools:
        schema = _simplify_schema(t.input_schema or {"type": "object", "properties": {}})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        specs.append({"toolSpec": {
            "name": t.name,
            "description": (t.description or t.name).strip(),
            "inputSchema": {"json": schema},
        }})
    return specs


def result_data(result) -> dict:
    """Uses the MCP structuredContent; if missing, parses the JSON in the text content."""
    if isinstance(getattr(result, "structured_content", None), dict):
        return result.structured_content
    raw = "".join(getattr(c, "text", "") for c in result.content if getattr(c, "type", "") == "text")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": not result.is_error, "speech": raw}
    return data if isinstance(data, dict) else {"ok": not result.is_error, "speech": raw}


# ------------------------------------------------------------------ Bedrock

_bedrock_client = None


def _bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3
        from botocore.config import Config

        session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None)
        _bedrock_client = session.client(
            "bedrock-runtime",
            region_name=os.environ.get("AWS_REGION", "us-east-2"),
            config=Config(retries={"max_attempts": 2, "mode": "standard"}, read_timeout=60),
        )
    return _bedrock_client


def models() -> list[str]:
    return [m.strip() for m in os.environ.get("FRONTDESK_MODEL", DEFAULT_MODELS).split(",") if m.strip()]


_paused: dict[str, float] = {}


def model() -> str:
    """Currently preferred model (the first one that is not paused)."""
    t = time.time()
    for m in models():
        if _paused.get(m, 0) <= t:
            return m
    return models()[0]


def _code(e: BaseException) -> str:
    response = getattr(e, "response", None)
    return response.get("Error", {}).get("Code", "") if isinstance(response, dict) else ""


def _switching_model_helps(e: BaseException) -> bool:
    """Errors that depend on the model (quota, access, model missing in the Region): worth trying another."""
    code, text = _code(e), str(e).lower()
    if code == "ThrottlingException":
        return "per day" in text or "tokens" in text
    if code in ("AccessDeniedException", "ResourceNotFoundException"):
        return True
    return code == "ValidationException" and ("model" in text or "inference profile" in text)


def _converse(**kwargs) -> dict:
    return _bedrock().converse(**kwargs)


def _call_model(converse, **kwargs) -> tuple[dict, str]:
    """Calls the first available model; if it has no quota or no access, tries the next one."""
    t = time.time()
    candidates = [m for m in models() if _paused.get(m, 0) <= t] or models()
    last_error: BaseException | None = None
    for m in candidates:
        try:
            return converse(modelId=m, **kwargs), m
        except Exception as e:
            if not _switching_model_helps(e):
                raise
            _paused[m] = time.time() + MODEL_PAUSE_S
            last_error = e
    raise last_error  # type: ignore[misc]


def _instructions(business: str) -> str:
    moment = schedule.now()
    return INSTRUCTIONS.format(
        business=business,
        today=f"{schedule.WEEKDAY_NAMES[moment.weekday()]}, {schedule.MONTH_NAMES[moment.month - 1]} {moment.day}, {moment.year}",
        time=schedule.say_time(moment.time()),
    )


def default_mcp_target():
    """The agent's MCP server: a URL, or "local" to use the in-process server (no HTTP)."""
    url = os.environ.get("FRONTDESK_MCP_URL") or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}/mcp"
    if url == "local":
        from frontdesk.server import mcp
        return mcp
    return url


def _trim_history(messages: list[dict]) -> list[dict]:
    """Keeps the latest messages, always starting at a user text message."""
    if len(messages) <= MAX_HISTORY:
        return messages
    cut = messages[-MAX_HISTORY:]
    while cut and not (cut[0]["role"] == "user" and any("text" in c for c in cut[0]["content"])):
        cut = cut[1:]
    return cut


async def respond(
    text: str,
    session_id: str | None = None,
    server: Any = None,
    business: str = "Main Street Barbers",
    converse=None,
) -> dict:
    """Handles one customer utterance and returns the agent's answer with the tool calls.

    `server` is the MCP server URL (default: FRONTDESK_MCP_URL) or an in-memory MCPServer (tests).
    `converse` replaces the Bedrock call (tests).
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty utterance.")
    converse = converse or _converse
    target = server if server is not None else default_mcp_target()
    options = {"mode": "legacy"} if isinstance(target, str) else {}
    sid, session = _session(session_id)
    messages = _trim_history(session.messages + [{"role": "user", "content": [{"text": text}]}])

    calls: list[dict] = []
    used_models: list[str] = []
    t_total = time.perf_counter()

    try:
        answer, ms_model, ms_tools_list = await _rounds(target, options, messages, calls, business, converse, used_models)
    except BaseException as e:
        root = _root(e)
        if isinstance(root, (KeyboardInterrupt, SystemExit, anyio.get_cancelled_exc_class())):
            raise
        raise AgentError(explain_error(root)) from root

    if not answer:
        answer = calls[-1]["speech"] if calls else "Sorry, I didn't get that. Could you say it again?"
    session.messages = messages
    return {
        "session": sid,
        "reply": answer,
        "calls": calls,
        "model": used_models[-1] if used_models else model(),
        "ms_model": ms_model,
        "ms_tools_list": ms_tools_list,
        "ms_total": round((time.perf_counter() - t_total) * 1000),
    }


async def _rounds(target, options, messages, calls, business, converse, used_models) -> tuple[str, int, int]:
    ms_model = 0
    answer = ""
    async with Client(target, **options) as mcp_client:
        t0 = time.perf_counter()
        listing = await mcp_client.list_tools()
        ms_tools_list = round((time.perf_counter() - t0) * 1000)
        tools = tools_for_bedrock(listing.tools)

        for _ in range(MAX_ROUNDS):
            t0 = time.perf_counter()
            output, used = await anyio.to_thread.run_sync(lambda: _call_model(
                converse,
                system=[{"text": _instructions(business)}],
                messages=messages,
                toolConfig={"tools": tools},
                inferenceConfig={"maxTokens": 400, "temperature": 0.2},
            ))
            ms_model += round((time.perf_counter() - t0) * 1000)
            used_models.append(used)
            message = output["output"]["message"]
            messages.append(message)

            requests = [c["toolUse"] for c in message["content"] if "toolUse" in c]
            if output.get("stopReason") != "tool_use" or not requests:
                answer = " ".join(c["text"] for c in message["content"] if "text" in c).strip()
                break

            results = []
            for req in requests:
                arguments = req.get("input") or {}
                t0 = time.perf_counter()
                try:
                    r = await mcp_client.call_tool(req["name"], arguments)
                    data = result_data(r)
                    failed = bool(r.is_error)
                except Exception as e:  # network failure or unknown tool: the model needs to know
                    data, failed = {"ok": False, "speech": f"Tool call failed: {e}"}, True
                ms = round((time.perf_counter() - t0) * 1000)
                calls.append({
                    "tool": req["name"],
                    "arguments": arguments,
                    "ms": ms,
                    "ok": bool(data.get("ok", not failed)),
                    "speech": data.get("speech", ""),
                })
                results.append({"toolResult": {
                    "toolUseId": req["toolUseId"],
                    "content": [{"json": data}],
                    **({"status": "error"} if failed else {}),
                }})
            messages.append({"role": "user", "content": results})
        else:
            answer = "Sorry, I got a bit tangled up there. Could you say that again?"
    return answer, ms_model, ms_tools_list
