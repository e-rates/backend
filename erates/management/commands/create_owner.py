import secrets

from django.core.management.base import BaseCommand, CommandError

from erates.models import User


class Command(BaseCommand):
    help = "Create (or promote) a platform owner account: the people who onboard counties"

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)
        parser.add_argument('--email')
        parser.add_argument('--promote', action='store_true', help='Promote an existing account instead of creating one')

    def handle(self, *args, username, email, promote, **options):
        username = username.lower()
        existing = User.objects.filter(username=username).first()
        password = 'Owner-' + secrets.token_urlsafe(9)

        if existing:
            if not promote:
                raise CommandError(f'{username} already exists; pass --promote to make them a platform owner')
            existing.role, existing.county = 'owner', ''
            existing.is_staff = existing.is_superuser = True
            existing.save()
            self.stdout.write(self.style.SUCCESS(f'{username} is now a platform owner (password unchanged)'))
            return

        if not email:
            raise CommandError('--email is required when creating a new owner')
        user = User.objects.create_user(username=username, email=email, password=password, role='owner')
        user.is_staff = user.is_superuser = True
        user.must_change_password = True
        user.save()
        self.stdout.write(self.style.SUCCESS(f'Platform owner {username} created'))
        self.stdout.write(f'Temporary password: {password}')
        self.stdout.write('Change it at first sign-in.')
