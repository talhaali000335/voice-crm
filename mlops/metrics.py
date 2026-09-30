from prometheus_client import Counter, Histogram

AGENT_REQUESTS = Counter("agent_requests_total", "Agent requests by intent", ["intent"])
AGENT_ERRORS = Counter("agent_errors_total", "Agent failures")
AGENT_SECONDS = Histogram("agent_total_seconds", "Time to run the agent graph",
                          buckets=[.5, 1, 2, 4, 8, 15])
VOICE_STT_SECONDS = Histogram("voice_stt_seconds", "Speech-to-text time",
                              buckets=[.25, .5, 1, 2, 4, 8])
RAG_GROUNDING = Histogram("rag_grounding_score", "How well FAQ answers are backed by the documents (0-1)",
                          buckets=[.2, .3, .4, .5, .6, .7, .8, .9, 1.0])
RAG_REFUSALS = Counter("rag_refusals_total", "FAQ questions answered with 'not found'", ["reason"])
