from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from erates.models import RateSchedule
from erates.payment_flow import generate_rate_bills


class Command(BaseCommand):
    help = "Bill every owned parcel in a county from its saved rate schedule for a year (safe to re-run)"

    def add_arguments(self, parser):
        parser.add_argument('--county', required=True)
        parser.add_argument('--year', type=int, default=timezone.now().year)

    def handle(self, *args, county, year, **options):
        schedule = RateSchedule.objects.filter(county__name__iexact=county, year=year).select_related('county').first()
        if not schedule:
            raise CommandError(f'No rate schedule for {county} {year}; set one on the Rate payments page first')
        result = generate_rate_bills(schedule)
        self.stdout.write(self.style.SUCCESS(
            f"{schedule.county.name} {year}: created {result['created']}, updated {result['updated']}, "
            f"unchanged {result['unchanged']}"
        ))
