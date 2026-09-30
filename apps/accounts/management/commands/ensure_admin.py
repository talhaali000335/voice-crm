import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the first superuser from ADMIN_USERNAME / ADMIN_PASSWORD if it does not exist yet."

    def handle(self, *args, **options):
        username = os.environ.get("ADMIN_USERNAME", "")
        password = os.environ.get("ADMIN_PASSWORD", "")
        if not (username and password):
            self.stdout.write("ensure_admin: ADMIN_USERNAME/ADMIN_PASSWORD not set, skipping.")
            return
        if User.objects.filter(username=username).exists():
            self.stdout.write("ensure_admin: user already exists, nothing changed.")
            return
        User.objects.create_superuser(username=username, password=password)
        self.stdout.write(f"ensure_admin: created superuser '{username}'.")
