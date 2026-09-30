import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from agent_core.guards import clean_for_speech, mask_pii
from agent_core.nodes import answer_kind, extract_json, pending_key, safety_check
from agent_core.service import run_agent
from apps.crm.models import Appointment, Ticket


class GuardTests(SimpleTestCase):
    @override_settings(EMERGENCY_MESSAGE="CALL HELP")
    def test_emergency_skips_ai(self):
        out = safety_check({"text": "I have chest pain"})
        self.assertTrue(out["needs_human"])
        self.assertEqual(out["reply"], "CALL HELP")

    def test_curly_apostrophe_emergency(self):
        self.assertTrue(safety_check({"text": "I can\u2019t breathe"})["needs_human"])

    def test_normal_text_passes(self):
        self.assertFalse(safety_check({"text": "book a haircut"})["needs_human"])

    def test_markdown_removed(self):
        self.assertEqual(clean_for_speech("**Hi** #there"), "Hi there")

    def test_pii_masked(self):
        self.assertNotIn("a@b.com", mask_pii("mail me at a@b.com"))
        self.assertNotIn("0300 123 4567", mask_pii("call 0300 123 4567 now"))

    def test_extract_json(self):
        self.assertEqual(extract_json('sure: {"a": 1} done'), {"a": 1})
        self.assertEqual(extract_json("no json here"), {})
        self.assertEqual(extract_json("{broken"), {})

    def test_answer_kind(self):
        self.assertEqual(answer_kind("Yes please"), "yes")
        self.assertEqual(answer_kind("no"), "no")
        self.assertEqual(answer_kind("yes no"), "no")          # "no" wins: never save by accident
        self.assertIsNone(answer_kind("yes I would also like to ask about parking"))
        self.assertIsNone(answer_kind("what are your hours"))


def tomorrow():
    return (timezone.localtime() + timedelta(days=1)).strftime("%Y-%m-%d")


def booking(time="10:00", date=None, service="haircut"):
    return json.dumps({"service": service, "date": date or tomorrow(), "time": time})


class AgentFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("alice", password="x")
        self.other = User.objects.create_user("bob", password="x")

    def ask(self, text, user=None):
        return run_agent((user or self.user).id, text)["reply"]

    @patch("agent_core.nodes.ask_llm")
    def test_emergency_never_calls_llm(self, llm):
        reply = self.ask("I think I'm having a heart attack")
        self.assertIn("emergency", reply.lower())
        llm.assert_not_called()
        self.assertEqual(Ticket.objects.count(), 0)

    @patch("agent_core.nodes.ask_llm", side_effect=["book", booking()])
    def test_booking_needs_confirmation_then_saves(self, llm):
        self.assertIn("Say yes", self.ask("book a haircut tomorrow at 10"))
        self.assertEqual(Appointment.objects.count(), 0)          # nothing saved yet
        self.assertIn("Done", self.ask("yes"))
        self.assertEqual(Appointment.objects.filter(customer=self.user).count(), 1)
        self.assertEqual(llm.call_count, 2)                        # "yes" did not call the LLM

    @patch("agent_core.nodes.ask_llm", side_effect=["book", booking()])
    def test_booking_can_be_declined(self, llm):
        self.ask("book a haircut tomorrow at 10")
        self.assertIn("cancelled", self.ask("no"))
        self.assertEqual(Appointment.objects.count(), 0)

    @patch("agent_core.nodes.ask_llm", side_effect=["book", '{"service": "haircut", "date": null, "time": null}',
                                                    "book", booking(service=None)])
    def test_missing_details_are_collected_across_messages(self, llm):
        self.assertIn("date and time", self.ask("I want a haircut"))
        self.assertIn("Say yes", self.ask("tomorrow at 10"))       # service remembered from the draft

    @patch("agent_core.nodes.ask_llm", side_effect=["book", booking()])
    def test_taken_slot_is_refused_with_alternative(self, llm):
        when = timezone.make_aware(timezone.datetime.strptime(f"{tomorrow()} 10:00", "%Y-%m-%d %H:%M"))
        Appointment.objects.create(customer=self.other, service="x", starts_at=when)
        reply = self.ask("book a haircut tomorrow at 10")
        self.assertIn("taken", reply)
        self.assertIn("next free slot", reply)

    @patch("agent_core.nodes.ask_llm", side_effect=["book", booking(time="22:00")])
    def test_outside_business_hours(self, llm):
        self.assertIn("between", self.ask("book a haircut tomorrow at 10 pm"))

    @patch("agent_core.nodes.ask_llm", side_effect=["book", booking(date="2020-01-01")])
    def test_past_date(self, llm):
        self.assertIn("past", self.ask("book a haircut on 1 January 2020"))

    @patch("agent_core.nodes.ask_llm", side_effect=["book", booking(date="tomorrow")])
    def test_unparseable_date_is_handled(self, llm):
        self.assertIn("couldn't understand", self.ask("book a haircut tomorrow"))

    @patch("agent_core.nodes.ask_llm", side_effect=["ticket", '{"summary": "Internet is down", "priority": "high"}'])
    def test_ticket_flow(self, llm):
        self.assertIn("high priority", self.ask("my internet is down"))
        self.assertIn("ticket number", self.ask("yes").lower())
        self.assertEqual(Ticket.objects.get().priority, "high")

    @patch("agent_core.nodes.ask_llm", side_effect=["ticket", "not json at all"])
    def test_ticket_survives_bad_llm_output(self, llm):
        self.assertIn("normal priority", self.ask("the app keeps crashing"))

    @patch("agent_core.nodes.ask_llm", side_effect=["book", booking(), "status"])
    def test_new_topic_abandons_pending_confirmation(self, llm):
        self.ask("book a haircut tomorrow at 10")
        self.assertIsNotNone(cache.get(pending_key(self.user.id)))
        self.assertIn("no upcoming", self.ask("what do I have coming up?"))
        self.assertIsNone(cache.get(pending_key(self.user.id)))
        self.assertEqual(Appointment.objects.count(), 0)

    @patch("agent_core.nodes.ask_llm", return_value="status")
    def test_status_only_shows_own_data(self, llm):
        Appointment.objects.create(customer=self.user, service="massage",
                                   starts_at=timezone.now() + timedelta(days=2))
        Ticket.objects.create(customer=self.user, summary="Broken chair")
        mine = self.ask("what do I have coming up?")
        self.assertIn("massage", mine)
        self.assertIn("Broken chair", mine)
        theirs = self.ask("what do I have coming up?", user=self.other)
        self.assertNotIn("massage", theirs)
        self.assertIn("no upcoming", theirs)

    @patch("agent_core.nodes.ask_llm", return_value="human")
    def test_handoff_opens_high_priority_ticket(self, llm):
        reply = self.ask("let me talk to a person")
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.priority, "high")
        self.assertIn(str(ticket.id), reply)

    @patch("agent_core.nodes.answer_question", return_value={"answer": "We open at nine."})
    @patch("agent_core.nodes.ask_llm", return_value="faq")
    def test_faq_uses_knowledge_base(self, llm, answer):
        self.assertEqual(self.ask("when do you open?"), "We open at nine.")
        answer.assert_called_once()

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            run_agent(self.user.id, "   ")
        with self.assertRaises(ValueError):
            run_agent(self.user.id, "a" * 1001)
