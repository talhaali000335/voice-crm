import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings

from security.middleware import client_ip, hit


class SecurityTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_health_check_ignores_host_header(self):
        r = self.client.get("/health/", HTTP_HOST="10.0.2.15:8000")     # how the ALB calls the task
        self.assertEqual((r.status_code, r.content), (200, b"ok"))

    def test_other_paths_still_validate_host(self):
        self.assertEqual(self.client.get("/health/ready/", HTTP_HOST="evil.example").status_code, 400)

    def test_ready_endpoint(self):
        r = self.client.get("/health/ready/")
        self.assertEqual((r.status_code, r.json()), (200, {"database": "ok", "cache": "ok"}))

    def test_api_requires_login(self):
        r = self.client.post("/api/agent/chat/", data='{"text": "hi"}', content_type="application/json")
        self.assertEqual(r.status_code, 401)

    def test_counter_is_exact(self):
        self.assertEqual([hit("k", 3, 60) for _ in range(5)], [True, True, True, False, False])

    def test_login_is_rate_limited(self):
        body = json.dumps({"username": "nobody", "password": "wrong"})
        codes = [self.client.post("/api/token/", body, content_type="application/json").status_code
                 for _ in range(11)]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(codes[10], 429)

    def test_limits_are_per_client(self):
        body = json.dumps({"username": "nobody", "password": "wrong"})
        for _ in range(10):
            self.client.post("/api/token/", body, content_type="application/json", REMOTE_ADDR="1.1.1.1")
        r = self.client.post("/api/token/", body, content_type="application/json", REMOTE_ADDR="2.2.2.2")
        self.assertEqual(r.status_code, 401)        # a different IP is not blocked

    @override_settings(TRUSTED_PROXY_COUNT=2)
    def test_client_ip_ignores_spoofed_header_start(self):
        req = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="9.9.9.9, 2.2.2.2, 3.3.3.3", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(client_ip(req), "2.2.2.2")     # 9.9.9.9 was written by the client: ignored

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_client_ip_without_proxy_uses_socket_address(self):
        req = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="9.9.9.9", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(client_ip(req), "10.0.0.1")

    @patch("rag_core.llm.chat")
    def test_rate_limiter_fails_open_when_cache_is_down(self, _):
        with patch("security.middleware.cache.add", side_effect=ConnectionError):
            r = self.client.post("/api/token/", "{}", content_type="application/json")
        self.assertEqual(r.status_code, 400)            # reached the view instead of a 500

    def test_passwords_are_never_sanitized(self):
        pw = "Zx<b>9!longpassword"
        r = self.client.post("/api/auth/register/", json.dumps({"username": "carol", "password": pw}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(User.objects.get(username="carol").check_password(pw))
        r = self.client.post("/api/token/", json.dumps({"username": "carol", "password": pw}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)

    def test_text_fields_are_sanitized(self):
        user = User.objects.create_user("dave", password="x")
        self.client.force_login(user)
        token = self.client.post("/api/token/", json.dumps({"username": "dave", "password": "x"}),
                                 content_type="application/json").json()["access"]
        with patch("apps.agent.views.run_agent", return_value={"reply": "ok", "intent": "faq"}) as run:
            self.client.post("/api/agent/chat/", json.dumps({"text": "hello <script>alert(1)</script>\x00 there"}),
                             content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        sent = run.call_args[0][1]
        self.assertNotIn("<script>", sent)
        self.assertNotIn("\x00", sent)

    def test_oversized_json_is_rejected(self):
        r = self.client.post("/api/agent/chat/", json.dumps({"text": "a" * 70000}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 413)

    def test_metrics_disabled_without_token(self):
        self.assertEqual(self.client.get("/health/metrics/").status_code, 404)

    @override_settings(METRICS_TOKEN="secret-token")
    def test_metrics_need_the_token(self):
        self.assertEqual(self.client.get("/health/metrics/").status_code, 403)
        self.assertEqual(self.client.get("/health/metrics/", HTTP_X_METRICS_TOKEN="wrong").status_code, 403)
        r = self.client.get("/health/metrics/", HTTP_X_METRICS_TOKEN="secret-token")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"agent_errors_total", r.content)
