import json

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings

GOOD = {"username": "erin", "password": "a-long-unusual-passphrase"}


class RegistrationTests(TestCase):
    def setUp(self):
        cache.clear()

    def post(self, data):
        return self.client.post("/api/auth/register/", json.dumps(data), content_type="application/json")

    def test_register_and_login(self):
        self.assertEqual(self.post(GOOD).status_code, 201)
        r = self.client.post("/api/token/", json.dumps(GOOD), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.get(username="erin").is_staff)

    def test_weak_passwords_rejected(self):
        for pw in ("short", "password1234", "1234567890123"):
            self.assertEqual(self.post({"username": "erin", "password": pw}).status_code, 400)

    def test_password_similar_to_username_rejected(self):
        self.assertEqual(self.post({"username": "erinwilliams", "password": "erinwilliams1"}).status_code, 400)

    def test_bad_username_rejected(self):
        self.assertEqual(self.post({"username": "a b!", "password": GOOD["password"]}).status_code, 400)

    def test_bad_email_rejected(self):
        self.assertEqual(self.post({**GOOD, "email": "nope"}).status_code, 400)

    def test_duplicate_username(self):
        self.post(GOOD)
        self.assertEqual(self.post(GOOD).status_code, 409)

    def test_non_object_body(self):
        self.assertEqual(self.post(["x"]).status_code, 400)

    @override_settings(REGISTRATION_ENABLED=False)
    def test_can_be_switched_off(self):
        self.assertEqual(self.post(GOOD).status_code, 403)

    @override_settings(REGISTRATION_INVITE_CODE="open-sesame")
    def test_invite_code(self):
        self.assertEqual(self.post(GOOD).status_code, 403)
        self.assertEqual(self.post({**GOOD, "invite_code": "wrong"}).status_code, 403)
        self.assertEqual(self.post({**GOOD, "invite_code": "open-sesame"}).status_code, 201)

    def test_registration_is_rate_limited(self):
        codes = [self.post({"username": f"user{i}", "password": GOOD["password"]}).status_code for i in range(6)]
        self.assertEqual(codes[-1], 429)

    def test_me_endpoint(self):
        User.objects.create_user("frank", password="x", is_staff=True)
        token = self.client.post("/api/token/", json.dumps({"username": "frank", "password": "x"}),
                                 content_type="application/json").json()["access"]
        r = self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(r.json(), {"username": "frank", "is_staff": True})
