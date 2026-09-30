import logging
import time

from mlops.metrics import AGENT_ERRORS, AGENT_REQUESTS, AGENT_SECONDS
from rag_core.llm import LLMUnavailable

from .graph import crm_graph
from .guards import mask_pii

logger = logging.getLogger(__name__)
MAX_TEXT = 1000


def run_agent(user_id: int, text: str) -> dict:
    """Used by both the text and the voice endpoint. Raises ValueError for bad input,
    LLMUnavailable when the language models are down."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Say or type something first.")
    if len(text) > MAX_TEXT:
        raise ValueError(f"Message too long (max {MAX_TEXT} characters).")

    start = time.time()
    try:
        result = crm_graph.invoke({"text": text, "user_id": user_id, "needs_human": False})
    except LLMUnavailable:
        AGENT_ERRORS.inc()
        raise
    except Exception:
        AGENT_ERRORS.inc()
        logger.exception("agent failed")
        raise

    intent = result.get("intent", "continued")
    AGENT_SECONDS.observe(time.time() - start)
    AGENT_REQUESTS.labels(intent=intent).inc()
    logger.info("agent user=%s intent=%s text=%s", user_id, intent, mask_pii(text[:80]))
    return {"reply": result["reply"], "intent": intent}
