from django.core.management.base import BaseCommand

from erates.models import County
from erates.waivers import apply_waivers


class Command(BaseCommand):
    help = 'Re-applies waivers to open bills so waivers that started or ended today take effect.'

    def handle(self, *args, **options):
        for county in County.objects.filter(waivers__isnull=False).distinct():
            outcome = apply_waivers(county)
            self.stdout.write(f"{county.name}: {outcome['bills_changed']} bills changed")
