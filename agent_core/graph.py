from typing import TypedDict

from django.core.cache import cache
from langgraph.graph import END, StateGraph

from .guards import clean_for_speech
from .nodes import (answer_kind, book_appointment, classify_intent, confirm, create_ticket,
                    faq_answer, handoff, my_status, pending_key, safety_check)


class AgentState(TypedDict, total=False):
    text: str
    user_id: int
    intent: str
    reply: str
    needs_human: bool


def output_guardrail(state: dict) -> dict:
    """Last step before the user hears/reads the answer."""
    return {"reply": clean_for_speech(state.get("reply", ""))}


def after_safety(state: dict) -> str:
    if state.get("needs_human"):
        return "guard"                          # emergency: reply is already set, skip everything else
    if cache.get(pending_key(state["user_id"])) and answer_kind(state["text"]):
        return "confirm"                        # a yes/no to a waiting confirmation
    return "classify"


def after_classify(state: dict) -> str:
    return "handoff" if state["intent"] == "human" else state["intent"]


def build_crm_graph():
    g = StateGraph(AgentState)
    g.add_node("safety", safety_check)
    g.add_node("classify", classify_intent)
    g.add_node("book", book_appointment)
    g.add_node("ticket", create_ticket)
    g.add_node("confirm", confirm)
    g.add_node("status", my_status)
    g.add_node("faq", faq_answer)
    g.add_node("handoff", handoff)
    g.add_node("guard", output_guardrail)

    g.set_entry_point("safety")
    g.add_conditional_edges("safety", after_safety, {
        "guard": "guard", "confirm": "confirm", "classify": "classify"})
    g.add_conditional_edges("classify", after_classify, {
        "book": "book", "ticket": "ticket", "status": "status", "faq": "faq", "handoff": "handoff"})
    for node in ("book", "ticket", "confirm", "status", "faq", "handoff"):
        g.add_edge(node, "guard")
    g.add_edge("guard", END)
    return g.compile()


crm_graph = build_crm_graph()
