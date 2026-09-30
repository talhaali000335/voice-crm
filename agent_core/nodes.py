import json
import re
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import IntegrityError
from django.utils import timezone

from apps.crm.models import Appointment, Ticket
from rag_core.answer import answer_question
from rag_core.llm import LLMUnavailable, chat

# ── helpers ────────────────────────────────────────


def ask_llm(prompt: str) -> str:
    """Single place the agent calls the LLM (easy to mock in tests)."""
    return chat(prompt, temperature=0.0, max_tokens=200)


def extract_json(text: str) -> dict:
    """Pull the first {...} out of an LLM reply. Returns {} if it isn't valid JSON."""
    match = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(match.group()) if match else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def pending_key(uid): return f"pending:{uid}"   # an action waiting for "yes"
def draft_key(uid): return f"draft:{uid}"       # half-filled booking
def lock_key(uid): return f"confirmlock:{uid}"  # stops a double "yes" creating two records


YES = {"yes", "y", "yeah", "yep", "confirm", "ok", "okay", "sure", "correct"}
NO = {"no", "n", "nope", "cancel", "stop"}


def answer_kind(text: str) -> str | None:
    """'yes' / 'no' if this short message answers a confirmation question, else None."""
    words = re.sub(r"[^a-z ]", "", text.lower()).split()
    if len(words) > 5:
        return None
    if set(words) & NO:          # "no" wins over "yes" so we never save something by accident
        return "no"
    if set(words) & YES:
        return "yes"
    return None


# ── NODE: safety_check ─────────────────────────────
EMERGENCY_WORDS = [
    "suicide", "kill myself", "end my life", "want to die", "chest pain", "can't breathe",
    "cannot breathe", "overdose", "heart attack", "being attacked", "stroke", "bleeding badly",
]


def safety_check(state: dict) -> dict:
    """Runs FIRST. Emergencies never reach the LLM (simple keyword screen; extend for your business)."""
    text = state["text"].lower().replace("\u2019", "'")
    if any(word in text for word in EMERGENCY_WORDS):
        return {"needs_human": True, "intent": "emergency", "reply": settings.EMERGENCY_MESSAGE}
    return {"needs_human": False}


# ── NODE: classify_intent ──────────────────────────
INTENTS = ("book", "ticket", "status", "human", "faq")


def classify_intent(state: dict) -> dict:
    uid = state["user_id"]
    cache.delete(pending_key(uid))      # a new request abandons any unanswered confirmation
    in_booking = bool(cache.get(draft_key(uid)))
    hint = ("The customer is in the middle of booking an appointment. If the message gives or "
            "changes appointment details (service, day or time), answer book.\n" if in_booking else "")
    label = ask_llm(
        "Classify the customer message into exactly ONE label:\n"
        "book   = wants to book an appointment\n"
        "ticket = reports a problem, fault or complaint\n"
        "status = asks about their own existing appointments or tickets\n"
        "human  = wants to speak to a person\n"
        "faq    = any other question\n"
        f"{hint}Reply with the label only.\n\n"
        f"Message: {state['text']}"
    ).lower()
    found = [i for i in INTENTS if i in label]
    return {"intent": found[0] if found else "faq"}


# ── NODE: book_appointment ─────────────────────────
def next_free_slot(after: datetime) -> datetime | None:
    """Next free, in-hours slot within 7 days (one query, then checked in memory)."""
    start_h, end_h = settings.BUSINESS_HOURS
    step = timedelta(minutes=settings.SLOT_MINUTES)
    horizon = after + timedelta(days=7)
    taken = set(Appointment.objects.filter(
        status="booked", starts_at__gt=after, starts_at__lte=horizon).values_list("starts_at", flat=True))
    slot = after
    while slot < horizon:
        slot += step
        if start_h <= timezone.localtime(slot).hour < end_h and slot not in taken:
            return slot
    return None


def book_appointment(state: dict) -> dict:
    uid = state["user_id"]
    today = timezone.localtime().strftime("%A %Y-%m-%d")
    data = extract_json(ask_llm(
        f"Today is {today}. Extract the appointment request from the message.\n"
        'Reply ONLY with JSON like {"service": "haircut", "date": "2026-01-30", "time": "15:00"}.\n'
        "Use null for anything the customer did not say. Time is 24-hour.\n\n"
        f"Message: {state['text']}"
    ))

    # Merge with what the customer already told us in earlier messages
    draft = cache.get(draft_key(uid), {})
    for key in ("service", "date", "time"):
        if data.get(key):
            draft[key] = str(data[key]).strip()[:100]

    missing = [k for k in ("service", "date", "time") if not draft.get(k)]
    if missing:
        cache.set(draft_key(uid), draft, 600)
        return {"reply": "Sure. I still need the " + " and ".join(missing) + " for your appointment."}

    try:
        when = timezone.make_aware(
            datetime.strptime(f"{draft['date']} {draft['time']}", "%Y-%m-%d %H:%M"))
    except ValueError:
        cache.delete(draft_key(uid))
        return {"reply": "I couldn't understand that date and time. Please say it again, "
                         "for example: Friday at 3 pm."}

    start_h, end_h = settings.BUSINESS_HOURS
    if when <= timezone.now():
        cache.delete(draft_key(uid))
        return {"reply": "That time is in the past. Which day and time would you like?"}
    if not (start_h <= when.hour < end_h) or when.minute % settings.SLOT_MINUTES:
        cache.delete(draft_key(uid))
        return {"reply": f"We take bookings between {start_h}:00 and {end_h}:00, "
                         f"on the hour or half hour. What time suits you?"}
    if Appointment.objects.filter(starts_at=when, status="booked").exists():
        alt = next_free_slot(when)
        cache.delete(draft_key(uid))
        hint = f" The next free slot is {timezone.localtime(alt):%A %d %B at %I:%M %p}." if alt else ""
        return {"reply": "Sorry, that slot is taken." + hint}

    # All good: ask for confirmation, DO NOT save yet
    cache.delete(draft_key(uid))
    cache.set(pending_key(uid), {
        "type": "appointment", "service": draft["service"], "starts_at": when.isoformat(),
    }, 300)
    return {"reply": f"Book {draft['service']} on {when:%A %d %B at %I:%M %p}? Say yes to confirm."}


# ── NODE: create_ticket ────────────────────────────
def create_ticket(state: dict) -> dict:
    data = extract_json(ask_llm(
        "Summarise the customer's problem in one sentence and choose a priority.\n"
        'Reply ONLY with JSON like {"summary": "Internet is down", "priority": "high"}.\n'
        "priority must be low, normal or high.\n\n"
        f"Message: {state['text']}"
    ))
    summary = str(data.get("summary") or state["text"])[:500]
    priority = data.get("priority") if data.get("priority") in ("low", "normal", "high") else "normal"
    cache.set(pending_key(state["user_id"]),
              {"type": "ticket", "summary": summary, "priority": priority}, 300)
    return {"reply": f"I'll open a {priority} priority ticket: {summary}. Say yes to confirm."}


# ── NODE: confirm (the "yes / no" step) ────────────
def confirm(state: dict) -> dict:
    uid = state["user_id"]
    if not cache.add(lock_key(uid), 1, 10):          # another "yes" is already being processed
        return {"reply": "One moment, I'm already processing that."}
    try:
        pending = cache.get(pending_key(uid))
        if not pending:
            return {"reply": "There's nothing waiting for confirmation. How can I help?"}
        if answer_kind(state["text"]) == "no":
            cache.delete(pending_key(uid))
            return {"reply": "Okay, I cancelled that. Anything else?"}

        cache.delete(pending_key(uid))
        user = User.objects.get(id=uid)
        if pending["type"] == "appointment":
            try:
                Appointment.objects.create(
                    customer=user, service=pending["service"],
                    starts_at=datetime.fromisoformat(pending["starts_at"]))
            except IntegrityError:
                return {"reply": "Sorry, that slot was just taken. Please choose another time."}
            return {"reply": "Done! Your appointment is booked."}

        ticket = Ticket.objects.create(
            customer=user, summary=pending["summary"], priority=pending["priority"])
        return {"reply": f"Done! Your ticket number is {ticket.id}."}
    finally:
        cache.delete(lock_key(uid))


# ── NODE: my_status ────────────────────────────────
def my_status(state: dict) -> dict:
    uid = state["user_id"]
    appts = Appointment.objects.filter(
        customer_id=uid, status="booked", starts_at__gte=timezone.now())[:3]
    tickets = Ticket.objects.filter(customer_id=uid, status="open")[:3]

    parts = []
    if appts:
        parts.append("Upcoming appointments: " + "; ".join(
            f"{a.service} on {timezone.localtime(a.starts_at):%A %d %B at %I:%M %p}" for a in appts))
    if tickets:
        parts.append("Open tickets: " + "; ".join(f"number {t.id}, {t.summary[:50]}" for t in tickets))
    return {"reply": " ".join(parts) or "You have no upcoming appointments or open tickets."}


# ── NODE: faq_answer (grounded RAG over the uploaded PDFs) ──
def faq_answer(state: dict) -> dict:
    try:
        return {"reply": answer_question(state["text"])["answer"]}
    except LLMUnavailable:
        return {"reply": "I'm having trouble reaching my knowledge base right now. Please try again shortly."}


# ── NODE: handoff ──────────────────────────────────
def handoff(state: dict) -> dict:
    user = User.objects.get(id=state["user_id"])
    ticket = Ticket.objects.create(
        customer=user, summary="Human help requested: " + state["text"][:300], priority="high")
    return {"reply": f"I'm passing you to a human agent. Ticket {ticket.id} has been opened for our team."}
