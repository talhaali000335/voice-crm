import hmac
import re

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")


class RegisterView(APIView):
    """Self-service sign-up. Guarded by: on/off switch, optional invite code,
    password validators, and a strict per-IP rate limit (security/middleware.py)."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        if not settings.REGISTRATION_ENABLED:
            return Response({"error": "Registration is closed."}, status=403)
        data = request.data if isinstance(request.data, dict) else {}

        invite = settings.REGISTRATION_INVITE_CODE
        if invite and not hmac.compare_digest(str(data.get("invite_code", "")), invite):
            return Response({"error": "Invalid invite code."}, status=403)

        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        email = str(data.get("email", "")).strip()

        if not USERNAME_RE.match(username):
            return Response({"error": "Username must be 3-30 characters: letters, numbers, . _ -"}, status=400)
        if email:
            try:
                validate_email(email)
            except ValidationError:
                return Response({"error": "Enter a valid email address."}, status=400)
        try:
            validate_password(password, user=User(username=username, email=email))
        except ValidationError as e:
            return Response({"error": " ".join(e.messages)}, status=400)

        try:
            User.objects.create_user(username=username, email=email, password=password)
        except IntegrityError:
            return Response({"error": "That username is already taken."}, status=409)
        return Response({"username": username}, status=201)


class MeView(APIView):
    def get(self, request):
        return Response({"username": request.user.username, "is_staff": request.user.is_staff})
