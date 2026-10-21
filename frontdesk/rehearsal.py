"""Rehearsal mode: a simple rule-based agent, without Bedrock, to run the demo when the model is unavailable.

It understands booking sentences in English with rules (service, day, period, time, name, phone) and calls
the same MCP server tools, over the same path as the real agent (tools/list and tools/call). Service and staff
names come from the business itself (list_services), so it works for any type of business.
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
NOT_NAMES = {
    "a", "an", "the", "here", "just", "looking", "calling", "trying", "wondering", "interested", "free",
    "available", "good", "fine", "sorry", "ready", "gonna", "going", "booking", "hoping", "new", "back",
    "haircut", "cut", "hair", "beard", "trim", "shave", "fade", "today", "tomorrow", "morning", "afternoon",
    "evening", "tonight", "noon", "yes", "yeah", "yep", "no", "nope", "sure", "ok", "okay", "please", "thanks",
    "thank", "you", "um", "uh", "so", "and", "it", "is", "its", "name", "my", "me", "for", "at", "on", "in",
    "to", "with", "am", "pm", "i", "im", "that", "this", "works", "great", "perfect", "sounds", "cool",
    "number", "phone", "cell", "mobile", "zero", "oh", *NUMBER_WORDS, *WEEKDAYS,
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


def _service(t: str, names: list[str] | None = None) -> str | None:
    """The service the customer mentions: one of the business's own services when it matches, else barbershop words."""
    if names:
        best, score = None, 0
        for name in names:
            words = [w for w in _norm(name).split() if w not in ("and", "the", "a", "of", "s")]
            hits = sum(1 for w in words if re.search(rf"\b{re.escape(w)}s?\b", t))
            if hits and (hits > score or (hits == score and best and len(name) > len(best) and hits == len(words))):
                best, score = name, hits
        if best:
            whole = [n for n in names if all(re.search(rf"\b{re.escape(w)}s?\b", t) for w in _norm(n).split() if w not in ("and", "the", "a", "of", "s"))]
            return max(whole, key=len) if whole else best
        if not any(n.lower() in ("haircut", "beard trim") for n in names):
            return None
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


def _staff(t: str, team: list[str]) -> str | None:
    for name in team:
        for part in _norm(name).replace(".", " ").split():
            if part in ("dr", "ms", "mr", "mrs") or len(part) < 2:
                continue
            if re.search(rf"\b{re.escape(part)}s?\b", t):  # "diegos" from "Diego's"
                return name
    return None


PHONE_WORDS = {"zero": "0", "oh": "0", **{w: str(n) for w, n in NUMBER_WORDS.items() if n <= 9}}


def _phone(t: str) -> str | None:
    """A phone number said or typed: '310 555 0142', '(310) 555-0142', 'three one zero five five five...'."""
    spoken = " ".join(PHONE_WORDS.get(w, w) for w in t.split())
    m = re.search(r"(?:\+?1[ -]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b", spoken)
    if m:
        return m.group(0)
    digits = re.findall(r"\b\d\b", spoken)
    run = re.search(r"(?:\b\d\b ?){10,11}", spoken)
    if run and len(digits) >= 10:
        return run.group(0).replace(" ", "")
    return None


YES = r"^(yes|yeah|yep|yup|sure|please|ok|okay|sounds good|do it|that works|perfect|great)\b"


def _usual(t: str) -> bool:
    return bool(re.search(r"\b(the usual|my usual|usual|same as last time|same thing|same again)\b", t))


def _span(t: str) -> tuple[str | None, str | None]:
    """'from 1 to 3 pm' -> ('1 pm', '3 pm')."""
    m = re.search(r"\bfrom (\d{1,2}(?::\d{2})?) ?(am|pm)? (?:to|until|till) (\d{1,2}(?::\d{2})?) ?(am|pm)?\b", t)
    if not m:
        return None, None
    end_suffix = m.group(4) or ""
    start_suffix = m.group(2) or end_suffix
    return f"{m.group(1)} {start_suffix}".strip(), f"{m.group(3)} {end_suffix}".strip()


def _intent(t: str, st: dict) -> str:
    if re.search(r"\bcancel|\bcall off\b", t):
        return "cancel"
    if _staff(t, st.get("team", [])) and re.search(r"\bblock|\b(is|will be|s) (off|out)\b|\bday off\b|\btake .* off\b|\bnot (coming|working)\b", t):
        return "block"
    if re.search(r"\b(waitlist|wait list|waiting list|put me on|let me know if (something|anything) opens)\b", t):
        return "waitlist"
    if st.get("offer") == "waitlist" and re.search(YES, t):
        return "waitlist"
    if re.search(r"\b(reschedule|move|change|push|switch|another time|different time)\b", t):
        return "reschedule"
    if re.search(r"\b(summary|my day|my schedule|how many (customers|clients|appointments)|how is (today|the day|business)|hows (today|the day|business|my day)|todays schedule|on the books)\b", t):
        return "summary"
    if re.search(r"\b(how much|price|prices|cost|costs|what services|what do you (offer|do|have)|menu)\b", t):
        return "services"
    if re.search(r"\b(are you open|you open|open on|what time do you (open|close)|hours|when do you (open|close)|do you open)\b", t) and not _service(t, st.get("services")):
        return "hours"
    return "book"


async def respond(text: str, session_id: str | None = None, server: Any = None, business: str = "",
                  context: dict | None = None) -> dict:
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

    context = context or {}
    if context.get("caller_phone") and not st.get("phone"):
        st["phone"] = context["caller_phone"]
        if context.get("caller_name"):
            st["name"] = context["caller_name"]
    waiting_for_name = st.get("pending") in ("name", "name_phone")
    phone_said = _phone(t)
    found = {
        "date": _date(t), "period": _period(t), "time": _time(t) if not phone_said else None,
        "name": _name(text, re.sub(r"[\d()+.-]+", " ", t), waiting_for_name), "phone": phone_said,
    }
    try:
        async with Client(target, **options) as mcp:
            t0 = time.perf_counter()
            await mcp.list_tools()
            ms_tools_list = round((time.perf_counter() - t0) * 1000)
            if "services" not in st:  # the business's own services and team, to understand what is said
                catalog = agent.result_data(await mcp.call_tool("list_services", {}))
                st["services"] = [x["name"] for x in catalog.get("services", [])]
                st["team"] = catalog.get("team", [])
            found["service"] = _service(t, st["services"])
            found["staff"] = _staff(t, st["team"])
            if found["name"] and found["staff"] and _norm(found["name"]).split()[0] in _norm(found["staff"]).split():
                found["name"] = None
            intent = _intent(t, st)
            if intent == "book" and st.get("intent") in ("cancel", "reschedule", "waitlist") and not found["service"]:
                intent = st["intent"]
            if _usual(t) or (st.get("offer") == "usual" and re.search(YES, t)):
                found["service"] = "the usual"
            if intent == "block":
                found["name"] = None  # the owner is talking about a staff member, not giving a customer name
            for k, v in found.items():
                if v:
                    st[k] = v
            st["intent"] = intent
            st.pop("pending", None)
            st.pop("offer", None)

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
    reply = await _decide_inner(st, intent, t, use)
    if "waitlist?" in reply or "put you on the waitlist" in reply:
        st["offer"] = "waitlist"
    elif reply.endswith("Want the usual?"):
        st["offer"] = "usual"
    return reply


def _service_for_lookup(st: dict) -> str | None:
    """The real service name (for availability and the waitlist) when the customer asked for 'the usual'."""
    if st.get("service") == "the usual":
        return st.get("usual_service")
    return st.get("service")


async def _decide_inner(st: dict, intent: str, t: str, use) -> str:
    if intent == "block":
        start, end = _span(t)
        r = await use("block_time", {"staff": st.get("staff"), "date": _date(t) or st.get("date") or "today",
                                     "start_time": start, "end_time": end,
                                     "period": None if start else _period(t)})
        st.clear()
        return r["speech"]

    if intent == "waitlist":
        service = _service_for_lookup(st)
        if not service:
            return "Sure. Which service would you like?"
        if not st.get("date"):
            return "Sure. For which day?"
        missing = _missing_contact(st)
        if missing:
            return missing
        r = await use("join_waitlist", {"customer_name": st["name"], "customer_phone": st["phone"], "service": service,
                                        "date": st["date"], "time": st.get("wanted_time") or st.get("time"),
                                        "period": None if (st.get("wanted_time") or st.get("time")) else st.get("period"),
                                        "staff": st.get("staff")})
        if r.get("ok"):
            _reset(st)
        return r["speech"]

    if intent == "services":
        return (await use("list_services", {}))["speech"]

    if intent == "summary":
        return (await use("daily_summary", {"date": st.get("date")}))["speech"]

    if intent == "cancel":
        if not st.get("name") and not st.get("phone"):
            st["pending"] = "name"
            return "Sure. What name is the appointment under?"
        r = await use("cancel_appointment", {"customer_phone": st.get("phone"), "customer_name": st.get("name"),
                                             "date": st.get("date") if _date(t) else None})
        if r.get("ok"):
            _reset(st)
        return r["speech"]

    if intent == "reschedule":
        if not st.get("name") and not st.get("phone"):
            st["pending"] = "name"
            return "Sure. What name is the appointment under?"
        if not st.get("time"):
            return "What day and time would you like instead?"
        r = await use("reschedule_appointment", {"customer_phone": st.get("phone"), "customer_name": st.get("name"),
                                                 "new_date": st.get("date"), "new_time": st["time"]})
        if r.get("ok"):
            _reset(st)
        else:
            st.pop("time", None)
        return r["speech"]

    if intent == "hours":
        day = _date(t) or "today"
        r = await use("check_availability", {"date": day, "service": st["services"][0] if st.get("services") else "haircut"})
        if r.get("ok") and r.get("slots"):
            st["date"] = day
            return f"Yes, we're open. {r['speech']}"
        return r["speech"]

    # book
    greeting = bool(t) and set(t.split()) <= GREETINGS
    if greeting and not (st.get("phone") and not st.get("looked_up")):
        return "Hi there! Want to book an appointment? Just tell me the service and the day."
    if (st.get("name") or st.get("phone")) and not st.get("looked_up") and st.get("service") in (None, "the usual"):
        # a customer who says their name first (or calls from a known number): greet them like a regular
        st["looked_up"] = True
        r = await use("customer_history", {"customer_phone": st.get("phone"), "customer_name": st.get("name")})
        profile = r.get("profile") or {}
        if profile.get("visits") or profile.get("upcoming"):
            st["name"] = profile["customer"]
            st["usual_service"] = profile.get("usual_service")
            if profile.get("phone") and not st.get("phone"):
                st["phone"] = profile["phone"]
        if not st.get("service") or not st.get("date"):
            if not st.get("name") and not (profile.get("visits") or profile.get("upcoming")):
                return "Hi there! Want to book an appointment? Just tell me the service and the day."
            return r["speech"]
    if not st.get("service"):
        parts = []
        if st.get("date"):
            parts.append(st["date"])
        if st.get("time"):
            parts.append(f"at {st['time']}")
        options = [x.lower() for x in st.get("services", [])][:4]
        question = f"which service would you like: {_join_or(options)}?" if options else "which service would you like?"
        if parts:
            return f"Sure! For {' '.join(parts)}, {question}"
        return f"Sure! {question[0].upper()}{question[1:]}"
    if not st.get("date"):
        return f"What day would you like the {'usual' if st['service'] == 'the usual' else st['service']}?"
    if not st.get("time"):
        r = await use("check_availability", {"date": st["date"], "service": _service_for_lookup(st) or st["services"][0],
                                             "period": st.get("period"), "staff": st.get("staff")})
        return r["speech"]
    missing = _missing_contact(st)
    if missing:
        return missing
    r = await use("book_appointment", {"customer_name": st["name"], "customer_phone": st["phone"], "service": st["service"],
                                       "date": st["date"], "time": st["time"], "staff": st.get("staff")})
    if r.get("ok"):
        _reset(st)
    else:
        st["wanted_time"] = st.pop("time", None)
    return r["speech"]


def _missing_contact(st: dict) -> str | None:
    """Name and phone are always required: asks for whatever is missing, in one question."""
    if not st.get("name") and not st.get("phone"):
        st["pending"] = "name_phone"
        return "Perfect. Can I get your name and the best phone number?"
    if not st.get("name"):
        st["pending"] = "name"
        return "Perfect. Can I get your name?"
    if not st.get("phone"):
        st["pending"] = "phone"
        return f"Thanks, {st['name'].split()[0]}. And the best phone number for the booking?"
    return None


def _join_or(items: list[str]) -> str:
    if len(items) <= 2:
        return " or ".join(items)
    return ", ".join(items[:-1]) + ", or " + items[-1]


def _reset(st: dict) -> None:
    """Forgets the finished request, but remembers who the customer is and what the business offers."""
    keep = {k: st[k] for k in ("name", "phone", "looked_up", "usual_service", "services", "team") if st.get(k)}
    st.clear()
    st.update(keep)


def end_session(session_id: str) -> None:
    _states.pop(session_id, None)
