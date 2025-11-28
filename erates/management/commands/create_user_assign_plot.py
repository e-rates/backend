from django.core.management.base import BaseCommand, CommandError
from django.utils.crypto import get_random_string
from django.contrib.gis.geos import Point
from django.db import transaction

from erates.models import User, Parcel, ParcelHistory


class Command(BaseCommand):
    help = 'Create a user and assign them the parcel with parcel_ref "I PLOT" (creates parcel if missing)'

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, help='Username (optional)')
        parser.add_argument('--email', type=str, help='Email (optional)')
        parser.add_argument('--password', type=str, help='Password (optional)')
        parser.add_argument('--force', action='store_true', help='Force reassign parcel if already owned')

    def handle(self, *args, **options):
        username = options.get('username')
        email = options.get('email')
        password = options.get('password')
        force = options.get('force')

        if not username:
            username = f'user_{get_random_string(6).lower()}'

        if not email:
            email = f'{username}@example.com'

        if not password:
            password = get_random_string(12)

        # Create user
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=username, email=email, password=password)
        except Exception as e:
            raise CommandError(f'Failed to create user: {e}')

        self.stdout.write(self.style.SUCCESS(f'Created user: {username}'))
        self.stdout.write(f'Password: {password}')

        # Parcel ref we want
        target_ref = 'I PLOT'

        try:
            parcel = Parcel.objects.filter(parcel_ref=target_ref, is_deleted=False).first()

            if parcel:
                if parcel.owner_user and not force:
                    self.stdout.write(self.style.WARNING(
                        f'Parcel "{target_ref}" already owned by {parcel.owner_user.username}. Use --force to reassign.'
                    ))
                    return

                # Assign parcel
                previous_owner = parcel.owner_user
                parcel.owner_user = user
                parcel.status = 'active'
                parcel.save()

                ParcelHistory.objects.create(
                    parcel=parcel,
                    owner_user=user,
                    geom=parcel.geom,
                    area_m2=parcel.area_m2,
                    changed_by=user,
                    change_reason=f'Assigned to {user.username} via management command'
                )

                self.stdout.write(self.style.SUCCESS(f'Assigned existing parcel "{target_ref}" to {username}'))
                return

            # If parcel not found, create a minimal parcel with Point(0,0)
            point = Point(0.0, 0.0, srid=4326)
            parcel = Parcel.objects.create(
                owner_user=user,
                parcel_ref=target_ref,
                geom=point,
                centroid=point,
                area_m2=0.0,
                county='Unknown',
                sub_county='Unknown',
                ward='Unknown',
                props={}
            )

            ParcelHistory.objects.create(
                parcel=parcel,
                owner_user=user,
                geom=parcel.geom,
                area_m2=parcel.area_m2,
                changed_by=user,
                change_reason=f'Created and assigned to {user.username} via management command'
            )

            self.stdout.write(self.style.SUCCESS(f'Created parcel "{target_ref}" and assigned to {username}'))

        except Exception as e:
            raise CommandError(f'Failed to assign or create parcel: {e}')
