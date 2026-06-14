from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Profile


class Command(BaseCommand):
    help = "Set a user's role by email. Run locally (docker exec) or in the Render shell."

    def add_arguments(self, parser):
        parser.add_argument("email", help="The user's email address.")
        parser.add_argument(
            "role",
            choices=[Profile.ROLE_STUDENT, Profile.ROLE_TEACHER],
            help="Role to assign.",
        )

    def handle(self, *args, **options):
        email = options["email"]
        role = options["role"]

        try:
            profile = Profile.objects.get(email=email)
        except Profile.DoesNotExist:
            raise CommandError(
                f"No profile for {email!r}. They must sign in with Google once first."
            )

        if profile.role == role:
            self.stdout.write(self.style.WARNING(f"{email} is already {role}; no change."))
            return

        profile.role = role
        profile.save(update_fields=["role"])
        self.stdout.write(self.style.SUCCESS(f"✓ {email} is now {role}."))
