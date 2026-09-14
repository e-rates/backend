from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from erates.payment_flow import generate_rate_bills


class Command(BaseCommand):
    help = "Create one pending land-rates bill per owned parcel for a rating year (safe to re-run)"

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=timezone.now().year)
        parser.add_argument('--deadline', required=True, help='YYYY-MM-DD; unpaid bills become overdue after this')

    def handle(self, *args, year, deadline, **options):
        try:
            due = datetime.strptime(deadline, '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=ZoneInfo('Africa/Nairobi'))
        except ValueError as exc:
            raise CommandError(f'Invalid --deadline: {exc}') from exc
        result = generate_rate_bills(year, due)
        self.stdout.write(self.style.SUCCESS(
            f"{year}: created {result['created']} bills, {result['skipped']} already existed"
        ))
