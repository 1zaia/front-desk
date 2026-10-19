"""Rehearsal mode: a simple rule-based agent, without Bedrock, to run the demo when the model is unavailable.

It understands booking sentences in English with rules (service, day, period, time, name) and calls
the same MCP server tools, over the same path as the real agent (tools/list and tools/call).
Enabled with FRONTDESK_AGENT=rehearsal. The screen shows "rehearsal mode".
"""
from __future__ import annotations

import re
import time
import unicodedata
import uuid
from typing import Any

from mcp import Client

from frontdesk import agent

MODE_NAME = "rehearsal mode (no Bedrock)"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
BARBERS = ("marcus", "diego")
NOT_NAMES = {
    "a", "an", "the", "here", "just", "looking", "calling", "trying", "wondering", "interested", "free",
    "available", "good", "fine", "sorry", "ready", "gonna", "going", "booking", "hoping", "new", "back",
    "haircut", "cut", "hair", "beard", "trim", "shave", "fade", "today", "tomorrow", "morning", "afternoon",
    "evening", "tonight", "noon", "yes", "yeah", "yep", "no", "nope", "sure", "ok", "okay", "please", "thanks",
    "thank", "you", "um", "uh", "so", "and", "it", "is", "its", "name", "my", "me", "for", "at", "on", "in",
    "to", "with", "am", "pm", "i", "im", "that", "this", "works", "great", "perfect", "sounds", "cool",
    *WEEKDAYS, *BARBERS,
}
GREETINGS = {"hi", "hello", "hey", "there", "good", "morning", "afternoon", "evening", "how", "are", "you",
             "doing", "yo", "howdy", "whats", "up"}

_states: dict[str, dict] = {}


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"\ba\.\s?m\.?", "am", t)
    t = re.sub(r"\bp\.\s?m\.?", "pm", t)
    t = t.replace("o'clock", "oclock").replace("'", "")
    t = re.sub(r"[^\w/:+]", " ", t)
    for word, n in NUMBER_WORDS.items():
        t = re.sub(rf"\b(at|around|by) {word}\b", rf"\1 {n}", t)
        t = re.sub(rf"\b{word} (am|pm|oclock|thirty)\b", rf"{n} \1", t)
    return " ".join(t.split())


def _service(t: str) -> str | None:
    haircut = re.search(r"\b(haircut|hair cut|cut|cuts|trim my hair|fade|buzz)\b", t) or re.search(r"\bhair\b", t)
    if "beard" in t and haircut:
        return "haircut and beard"
    if re.search(r"\b(combo|the works)\b", t):
        return "haircut and beard"
    if "shave" in t:
        return "hot towel shave"
    if "beard" in t:
        return "beard trim"
    if haircut or re.search(r"\btrim\b", t):
        return "haircut"
    return None


def _date(t: str) -> str | None:
    if "day after tomorrow" in t:
        return "day after tomorrow"
    if re.search(r"\btomorrow\b", t):
        return "tomorrow"
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening))\b", t):
        return "today"
    m = re.search(r"\b(\d{1,2}/\d{1,2})\b", t)
    if m:
        return m.group(1)
    m = re.search(rf"\b({MONTHS}) (\d{{1,2}})(?:st|nd|rd|th)?\b", t)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = re.search(rf"\bthe (\d{{1,2}})(st|nd|rd|th)\b(?: of ({MONTHS}))?", t)
    if m:
        return f"{m.group(3)} {m.group(1)}" if m.group(3) else f"the {m.group(1)}{m.group(2)}"
    for d in WEEKDAYS:
        m = re.search(rf"\b(next )?{d}s?\b", t)
        if m:
            return f"next {d}" if m.group(1) else d
    return None


def _period(t: str) -> str | None:
    if re.search(r"\btonight\b", t):
        return "evening"
    for p in ("morning", "afternoon", "evening"):
        if re.search(rf"\b{p}\b", t) and not re.search(rf"\bgood {p}\b", t):
            return p
    return None


def _time(t: str) -> str | None:
    if re.search(r"\b(noon|midday|lunchtime)\b", t):
        return "noon"
    m = re.search(r"\bhalf past (\d{1,2})\b(?: (am|pm))?", t)
    if m:
        return f"{int(m.group(1))}:30 {m.group(2) or ''}".strip()
    m = re.search(r"\b(\d{1,2}):(\d{2}) ?(am|pm)?\b", t)
    if m:
        return f"{int(m.group(1))}:{m.group(2)} {m.group(3) or ''}".strip()
    m = re.search(r"\b(\d{1,2}) ?(am|pm)\b", t)
    if m:
        return f"{int(m.group(1))} {m.group(2)}"
    m = re.search(r"\b(\d{1,2}) (thirty|oclock)\b", t)
    if m:
        return f"{int(m.group(1))}:30" if m.group(2) == "thirty" else f"{int(m.group(1))}"
    m = re.search(r"\b(?:at|around|by|for) (\d{1,2})\b(?!/|st|nd|rd|th)", t)
    if m and int(m.group(1)) <= 12:
        return m.group(1)
    return None


def _name(original: str, t: str, waiting_for_name: bool) -> str | None:
    m = re.search(r"\b(?:my name is|my names|name is|names|i am|im|this is|call me|its|under|it is|put it under)\s+([a-z]+(?:\s+[a-z]+)?)", t)
    candidate = m.group(1) if m else None
    if candidate and candidate.split()[0] in NOT_NAMES:
        candidate = None
    if not candidate and waiting_for_name:
        words = [w for w in t.split() if w not in NOT_NAMES and not w.isdigit()]
        if 1 <= len(words) <= 3:
            candidate = " ".join(words[:2])
    if not candidate:
        return None
    parts = []
    for p in candidate.split():
        if p in NOT_NAMES or p.isdigit():
            break
        parts.append(p)
    if not parts:
        return None
    # recover the original spelling (accents, capitals) from what was said
    originals = {_norm(w): w for w in re.findall(r"[\wÀ-ÿ]+", original)}
    return " ".join(originals.get(p, p).capitalize() for p in parts)


def _barber(t: str) -> str | None:
    for name in BARBERS:
        if re.search(rf"\b{name}\b", t):
            return name.capitalize()
    return None


def _intent(t: str) -> str:
    if re.search(r"\bcancel|\bcall off\b", t):
        return "cancel"
    if re.search(r"\b(reschedule|move|change|push|switch|another time|different time)\b", t):
        return "reschedule"
    if re.search(r"\b(summary|my day|my schedule|how many (customers|clients|appointments)|how is (today|the day|business)|hows (today|the day|business|my day)|todays schedule|on the books)\b", t):
        return "summary"
    if re.search(r"\b(how much|price|prices|cost|costs|what services|what do you (offer|do|have)|menu)\b", t):
        return "services"
    if re.search(r"\b(are you open|you open|open on|what time do you (open|close)|hours|when do you (open|close)|do you open)\b", t) and not _service(t):
        return "hours"
    return "book"


async def respond(text: str, session_id: str | None = None, server: Any = None, business: str = "") -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty utterance.")
    sid = session_id or uuid.uuid4().hex
    st = _states.setdefault(sid, {})
    t = _norm(text)
    target = server if server is not None else agent.default_mcp_target()
    options = {"mode": "legacy"} if isinstance(target, str) else {}
    calls: list[dict] = []
    t_start = time.perf_counter()

    waiting_for_name = st.get("pending") == "name"
    found = {
        "service": _service(t), "date": _date(t), "period": _period(t), "time": _time(t),
        "name": _name(text, t, waiting_for_name), "barber": _barber(t),
    }
    intent = _intent(t)
    if intent == "book" and st.get("intent") in ("cancel", "reschedule") and not found["service"]:
        intent = st["intent"]
    for k, v in found.items():
        if v:
            st[k] = v
    st["intent"] = intent
    st.pop("pending", None)

    try:
        async with Client(target, **options) as mcp:
            t0 = time.perf_counter()
            await mcp.list_tools()
            ms_tools_list = round((time.perf_counter() - t0) * 1000)

            async def use(name: str, args: dict) -> dict:
                args = {k: v for k, v in args.items() if v}
                t1 = time.perf_counter()
                r = await mcp.call_tool(name, args)
                data = agent.result_data(r)
                calls.append({"tool": name, "arguments": args, "ms": round((time.perf_counter() - t1) * 1000),
                              "ok": bool(data.get("ok")), "speech": data.get("speech", "")})
                return data

            reply = await _decide(st, intent, t, use)
    except BaseException as e:
        root = agent._root(e)
        raise agent.AgentError(agent.explain_error(root)) from root

    return {
        "session": sid,
        "reply": reply,
        "calls": calls,
        "model": MODE_NAME,
        "ms_model": 0,
        "ms_tools_list": ms_tools_list,
        "ms_total": round((time.perf_counter() - t_start) * 1000),
    }


async def _decide(st: dict, intent: str, t: str, use) -> str:
    if intent == "services":
        return (await use("list_services", {}))["speech"]

    if intent == "summary":
        return (await use("daily_summary", {"date": st.get("date")}))["speech"]

    if intent == "cancel":
        if not st.get("name"):
            st["pending"] = "name"
            return "Sure. What name is the appointment under?"
        r = await use("cancel_appointment", {"customer_name": st["name"], "date": st.get("date") if _date(t) else None})
        if r.get("ok"):
            _reset(st)
        return r["speech"]

    if intent == "reschedule":
        if not st.get("name"):
            st["pending"] = "name"
            return "Sure. What name is the appointment under?"
        if not st.get("time"):
            return "What day and time would you like instead?"
        r = await use("reschedule_appointment", {"customer_name": st["name"], "new_date": st.get("date"),
                                                 "new_time": st["time"]})
        if r.get("ok"):
            _reset(st)
        else:
            st.pop("time", None)
        return r["speech"]

    if intent == "hours":
        day = _date(t) or "today"
        r = await use("check_availability", {"date": day, "service": "haircut"})
        if r.get("ok") and r.get("slots"):
            st["date"] = day
            return f"Yes, we're open. {r['speech']}"
        return r["speech"]

    # book
    if t and set(t.split()) <= GREETINGS:
        return "Hi there! Want to book an appointment? Just tell me the service and the day."
    if not st.get("service"):
        parts = []
        if st.get("date"):
            parts.append(st["date"])
        if st.get("time"):
            parts.append(f"at {st['time']}")
        question = "which service would you like: a haircut, a beard trim, haircut and beard, or a hot towel shave?"
        if parts:
            return f"Sure! For {' '.join(parts)}, {question}"
        return f"Sure! {question[0].upper()}{question[1:]}"
    if not st.get("date"):
        return f"What day would you like the {st['service']}?"
    if not st.get("time"):
        r = await use("check_availability", {"date": st["date"], "service": st["service"],
                                             "period": st.get("period"), "barber": st.get("barber")})
        return r["speech"]
    if not st.get("name"):
        st["pending"] = "name"
        return "Perfect. Can I get your name?"
    r = await use("book_appointment", {"customer_name": st["name"], "service": st["service"], "date": st["date"],
                                       "time": st["time"], "barber": st.get("barber")})
    if r.get("ok"):
        _reset(st)
    else:
        st.pop("time", None)
    return r["speech"]


def _reset(st: dict) -> None:
    name = st.get("name")
    st.clear()
    if name:
        st["name"] = name


def end_session(session_id: str) -> None:
    _states.pop(session_id, None)
