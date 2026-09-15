from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.gis.geos import Polygon, Point
from django.utils import timezone
from datetime import datetime
from zoneinfo import ZoneInfo

from erates.models import County, User, Account, Parcel, ParcelHistory, Payment
from erates.rates import annual_rate

NAIROBI_TZ = ZoneInfo('Africa/Nairobi')

class Command(BaseCommand):
    help = "Seed realistic demo data: Counties, Officials, Landowners, Parcels with polygons, and Multi-Year Rate Bills"

    def handle(self, *args, **options):
        self.stdout.write("Seeding counties...")
        counties_data = [
            {
                "name": "Nyeri",
                "paybill": "174379",
                "rates_office_email": "rates@nyeri.go.ke",
                "rates_office_phone": "+254700000001",
            },
            {
                "name": "Nairobi",
                "paybill": "222222",
                "rates_office_email": "rates@nairobi.go.ke",
                "rates_office_phone": "+254700000002",
            },
            {
                "name": "Kiambu",
                "paybill": "333333",
                "rates_office_email": "rates@kiambu.go.ke",
                "rates_office_phone": "+254700000003",
            },
        ]
        
        for c_info in counties_data:
            c, created = County.objects.update_or_create(
                name=c_info["name"],
                defaults={
                    "paybill": c_info["paybill"],
                    "rates_office_email": c_info["rates_office_email"],
                    "rates_office_phone": c_info["rates_office_phone"],
                    "is_active": True,
                }
            )
            self.stdout.write(f"  County: {c.name} ({'created' if created else 'updated'})")

        self.stdout.write("Seeding county officials...")
        officials_data = [
            {"username": "nyeri_official", "email": "official@nyeri.go.ke", "role": "admin", "county": "Nyeri", "phone": "+254701000001"},
            {"username": "auditor_nyeri", "email": "auditor@nyeri.go.ke", "role": "auditor", "county": "Nyeri", "phone": "+254701000002"},
            {"username": "nairobi_official", "email": "official@nairobi.go.ke", "role": "admin", "county": "Nairobi", "phone": "+254701000003"},
            {"username": "kiambu_official", "email": "official@kiambu.go.ke", "role": "admin", "county": "Kiambu", "phone": "+254701000004"},
        ]
        for off in officials_data:
            user = User.objects.filter(username=off["username"]).first()
            if not user:
                user = User.objects.create_user(
                    username=off["username"],
                    email=off["email"],
                    password="Password123!",
                    role=off["role"],
                    county=off["county"],
                    phone=off["phone"],
                    is_staff=True,
                    is_verified=True,
                )
                self.stdout.write(f"  Created official: {user.username} ({user.county})")
            else:
                user.role = off["role"]
                user.county = off["county"]
                user.is_staff = True
                user.is_verified = True
                user.set_password("Password123!")
                user.save()
                self.stdout.write(f"  Updated official: {user.username} ({user.county})")

        self.stdout.write("Seeding land owners...")
        landowners_data = [
            {"username": "john_kamau", "email": "john.kamau@example.com", "county": "Nyeri", "phone": "+254711000001"},
            {"username": "mary_wambui", "email": "mary.wambui@example.com", "county": "Nyeri", "phone": "+254711000002"},
            {"username": "david_kariuki", "email": "david.kariuki@example.com", "county": "Nyeri", "phone": "+254711000003"},
            {"username": "grace_muthoni", "email": "grace.muthoni@example.com", "county": "Nyeri", "phone": "+254711000004"},
            {"username": "peter_ndungu", "email": "peter.ndungu@example.com", "county": "Nyeri", "phone": "+254711000005"},
            {"username": "alice_atieno", "email": "alice.atieno@example.com", "county": "Nairobi", "phone": "+254711000006"},
            {"username": "samuel_mwangi", "email": "samuel.mwangi@example.com", "county": "Kiambu", "phone": "+254711000007"},
        ]
        
        users_by_username = {}
        for lo in landowners_data:
            user = User.objects.filter(username=lo["username"]).first()
            if not user:
                user = User.objects.create_user(
                    username=lo["username"],
                    email=lo["email"],
                    password="Password123!",
                    role="user",
                    county=lo["county"],
                    phone=lo["phone"],
                    is_verified=True,
                )
                self.stdout.write(f"  Created landowner: {user.username}")
            else:
                user.county = lo["county"]
                user.is_verified = True
                user.set_password("Password123!")
                user.save()
                self.stdout.write(f"  Updated landowner: {user.username}")
            
            # Ensure main account exists
            account, _ = Account.objects.get_or_create(
                owner_user=user,
                account_type="main",
                defaults={"currency": "KES", "current_balance": Decimal("0.00"), "status": "active"}
            )
            users_by_username[user.username] = (user, account)

        admin_nyeri = User.objects.filter(username="nyeri_official").first()

        self.stdout.write("Seeding parcels with geometries...")
        parcels_data = [
            {
                "parcel_ref": "NYI/RWR/001",
                "county": "Nyeri",
                "sub_county": "Nyeri Central",
                "ward": "Rware",
                "owner": "john_kamau",
                "land_use": "commercial",
                "usv": Decimal("3500000.00"),
                "area_m2": 1250.0,
                "poly": ((36.9450, -0.4200), (36.9480, -0.4200), (36.9480, -0.4230), (36.9450, -0.4230), (36.9450, -0.4200)),
            },
            {
                "parcel_ref": "NYI/RWR/002",
                "county": "Nyeri",
                "sub_county": "Nyeri Central",
                "ward": "Rware",
                "owner": "mary_wambui",
                "land_use": "residential",
                "usv": Decimal("2200000.00"),
                "area_m2": 980.0,
                "poly": ((36.9485, -0.4200), (36.9515, -0.4200), (36.9515, -0.4230), (36.9485, -0.4230), (36.9485, -0.4200)),
            },
            {
                "parcel_ref": "NYI/KGM/010",
                "county": "Nyeri",
                "sub_county": "Nyeri Central",
                "ward": "Kiganjo/Mathari",
                "owner": "david_kariuki",
                "land_use": "agricultural",
                "usv": Decimal("1800000.00"),
                "area_m2": 4500.0,
                "poly": ((36.9600, -0.4100), (36.9650, -0.4100), (36.9650, -0.4150), (36.9600, -0.4150), (36.9600, -0.4100)),
            },
            {
                "parcel_ref": "NYI/RUR/045",
                "county": "Nyeri",
                "sub_county": "Nyeri Central",
                "ward": "Ruring'u",
                "owner": "grace_muthoni",
                "land_use": "residential",
                "usv": Decimal("2800000.00"),
                "area_m2": 1600.0,
                "poly": ((36.9520, -0.4300), (36.9560, -0.4300), (36.9560, -0.4340), (36.9520, -0.4340), (36.9520, -0.4300)),
            },
            {
                "parcel_ref": "NYI/KMK/102",
                "county": "Nyeri",
                "sub_county": "Nyeri Central",
                "ward": "Kamakwa/Mukaro",
                "owner": "peter_ndungu",
                "land_use": "industrial",
                "usv": Decimal("4200000.00"),
                "area_m2": 2800.0,
                "poly": ((36.9380, -0.4350), (36.9420, -0.4350), (36.9420, -0.4390), (36.9380, -0.4390), (36.9380, -0.4350)),
            },
            {
                "parcel_ref": "NYI/KMK/103",
                "county": "Nyeri",
                "sub_county": "Nyeri Central",
                "ward": "Kamakwa/Mukaro",
                "owner": "john_kamau",
                "land_use": "residential",
                "usv": Decimal("1950000.00"),
                "area_m2": 1100.0,
                "poly": ((36.9425, -0.4350), (36.9465, -0.4350), (36.9465, -0.4390), (36.9425, -0.4390), (36.9425, -0.4350)),
            },
            {
                "parcel_ref": "NRB/CBD/001",
                "county": "Nairobi",
                "sub_county": "Starehe",
                "ward": "Nairobi Central",
                "owner": "alice_atieno",
                "land_use": "commercial",
                "usv": Decimal("12500000.00"),
                "area_m2": 2100.0,
                "poly": ((36.8200, -1.2850), (36.8240, -1.2850), (36.8240, -1.2890), (36.8200, -1.2890), (36.8200, -1.2850)),
            },
            {
                "parcel_ref": "KBU/THK/050",
                "county": "Kiambu",
                "sub_county": "Thika",
                "ward": "Township",
                "owner": "samuel_mwangi",
                "land_use": "commercial",
                "usv": Decimal("5500000.00"),
                "area_m2": 3200.0,
                "poly": ((37.0650, -1.0350), (37.0700, -1.0350), (37.0700, -1.0400), (37.0650, -1.0400), (37.0650, -1.0350)),
            },
        ]

        parcels_by_ref = {}
        for p_info in parcels_data:
            geom = Polygon(p_info["poly"], srid=4326)
            centroid = geom.centroid
            owner_user = users_by_username[p_info["owner"]][0]
            
            parcel, p_created = Parcel.objects.update_or_create(
                parcel_ref=p_info["parcel_ref"],
                defaults={
                    "owner_user": owner_user,
                    "county": p_info["county"],
                    "sub_county": p_info["sub_county"],
                    "ward": p_info["ward"],
                    "land_use": p_info["land_use"],
                    "unimproved_site_value": p_info["usv"],
                    "area_m2": p_info["area_m2"],
                    "geom": geom,
                    "centroid": centroid,
                    "status": "active",
                    "props": {"REG_SECTIO": f"SECTION-{p_info['ward'][:3].upper()}"},
                }
            )
            parcels_by_ref[parcel.parcel_ref] = parcel
            if p_created:
                ParcelHistory.objects.create(
                    parcel=parcel,
                    owner_user=owner_user,
                    geom=geom,
                    area_m2=p_info["area_m2"],
                    changed_by=admin_nyeri or owner_user,
                    change_reason="Initial registration and allocation"
                )
            self.stdout.write(f"  Parcel: {parcel.parcel_ref} ({parcel.county}, {parcel.ward})")

        self.stdout.write("Seeding multi-year rate bills and payment records...")
        years_config = [
            (2024, datetime(2024, 6, 30, 23, 59, 59, tzinfo=NAIROBI_TZ)),
            (2025, datetime(2025, 6, 30, 23, 59, 59, tzinfo=NAIROBI_TZ)),
            (2026, datetime(2026, 3, 31, 23, 59, 59, tzinfo=NAIROBI_TZ)),
        ]

        status_matrix = {
            2024: {
                "NYI/RWR/001": True,
                "NYI/RWR/002": True,
                "NYI/KGM/010": False,
                "NYI/RUR/045": True,
                "NYI/KMK/102": False,
                "NYI/KMK/103": True,
                "NRB/CBD/001": True,
                "KBU/THK/050": True,
            },
            2025: {
                "NYI/RWR/001": True,
                "NYI/RWR/002": False,
                "NYI/KGM/010": False,
                "NYI/RUR/045": True,
                "NYI/KMK/102": False,
                "NYI/KMK/103": True,
                "NRB/CBD/001": True,
                "KBU/THK/050": False,
            },
            2026: {
                "NYI/RWR/001": True,
                "NYI/RWR/002": False,
                "NYI/KGM/010": False,
                "NYI/RUR/045": False,
                "NYI/KMK/102": True,
                "NYI/KMK/103": False,
                "NRB/CBD/001": True,
                "KBU/THK/050": False,
            },
        }

        bills_created = 0
        bills_updated = 0
        for yr, deadline in years_config:
            for p_info in parcels_data:
                ref = p_info["parcel_ref"]
                parcel = parcels_by_ref[ref]
                owner_user, account = users_by_username[p_info["owner"]]
                is_paid = status_matrix[yr].get(ref, False)
                amount = annual_rate(parcel)
                
                idempotency_key = f"rates:{ref}:{yr}"
                st = "completed" if is_paid else "pending"
                proc = "M-Pesa" if is_paid else None
                pref = f"MPESA{yr}{ref.replace('/', '')[:8]}" if is_paid else None

                payment = Payment.objects.filter(idempotency_key=idempotency_key).first()
                if not payment:
                    payment = Payment.objects.create(
                        idempotency_key=idempotency_key,
                        user=owner_user,
                        account=account,
                        parcel=parcel,
                        payment_year=yr,
                        amount=amount,
                        currency="KES",
                        status=st,
                        processor=proc,
                        processor_ref=pref,
                        deadline=deadline,
                        metadata={"kind": "land_rates", "basis": "usv", "land_use": parcel.land_use},
                    )
                    bills_created += 1
                else:
                    payment.amount = amount
                    payment.status = st
                    payment.processor = proc
                    payment.processor_ref = pref
                    payment.deadline = deadline
                    payment.save()
                    bills_updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Successfully seeded demo database! (Bills created: {bills_created}, updated: {bills_updated})"
        ))
