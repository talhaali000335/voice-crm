from rest_framework.response import Response
from rest_framework.views import APIView

from agent_core.service import run_agent
from rag_core.llm import LLMUnavailable


class AgentChatView(APIView):          # login required (default permission)
    def post(self, request):
        text = request.data.get("text") if isinstance(request.data, dict) else None
        if not isinstance(text, str):
            return Response({"error": 'Send JSON like {"text": "..."}.'}, status=400)
        try:
            out = run_agent(request.user.id, text)
        except ValueError as e:
            return Response({"error": str(e)}, status=400)
        except LLMUnavailable:
            return Response({"error": "The assistant is busy right now. Please try again in a moment."}, status=503)
        except Exception:
            # details go to the logs, not to the user
            return Response({"error": "Something went wrong. Please try again."}, status=500)
        return Response(out)
