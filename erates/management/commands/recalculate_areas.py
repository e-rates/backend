"""
Management command to recalculate parcel areas using accurate geodetic methods.
This fixes any area calculation issues from previous imports.

Usage:
    python manage.py recalculate_areas
    python manage.py recalculate_areas --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from erates.models import Parcel
from pyproj import Geod


class Command(BaseCommand):
    help = 'Recalculate parcel areas using accurate geodetic methods (WGS84 ellipsoid)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )
        parser.add_argument(
            '--parcel-ref',
            type=str,
            help='Recalculate only for specific parcel reference',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        parcel_ref = options.get('parcel_ref')
        
        # Get queryset
        if parcel_ref:
            parcels = Parcel.objects.filter(parcel_ref=parcel_ref, is_deleted=False)
            if not parcels.exists():
                self.stdout.write(self.style.ERROR(f'Parcel not found: {parcel_ref}'))
                return
        else:
            parcels = Parcel.objects.filter(is_deleted=False)
        
        total = parcels.count()
        self.stdout.write(f'Processing {total} parcels...')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be saved'))
        
        # Use WGS84 ellipsoid for geodetic area calculation
        geod = Geod(ellps='WGS84')
        
        updated = 0
        errors = 0
        unchanged = 0
        
        for parcel in parcels:
            try:
                old_area = parcel.area_m2
                new_area = None
                
                # Calculate accurate area
                if parcel.geom:
                    if parcel.geom.geom_type == 'Polygon':
                        coords = list(parcel.geom.coords[0])
                        lons = [c[0] for c in coords]
                        lats = [c[1] for c in coords]
                        area, _ = geod.polygon_area_perimeter(lons, lats)
                        new_area = abs(area)
                    elif parcel.geom.geom_type == 'MultiPolygon':
                        total_area = 0
                        for polygon in parcel.geom:
                            coords = list(polygon.coords[0])
                            lons = [c[0] for c in coords]
                            lats = [c[1] for c in coords]
                            area, _ = geod.polygon_area_perimeter(lons, lats)
                            total_area += abs(area)
                        new_area = total_area
                
                if new_area is not None:
                    # Check if area changed significantly (more than 0.1%)
                    if old_area:
                        percent_change = abs((new_area - old_area) / old_area * 100)
                        if percent_change < 0.1:
                            unchanged += 1
                            continue
                        
                        self.stdout.write(
                            f'{parcel.parcel_ref}: {old_area:.2f} m² → {new_area:.2f} m² '
                            f'({percent_change:+.2f}% change)'
                        )
                    else:
                        self.stdout.write(
                            f'{parcel.parcel_ref}: No previous area → {new_area:.2f} m²'
                        )
                    
                    if not dry_run:
                        parcel.area_m2 = new_area
                        parcel.save(update_fields=['area_m2', 'updated_at'])
                    
                    updated += 1
                else:
                    errors += 1
                    self.stdout.write(
                        self.style.WARNING(f'{parcel.parcel_ref}: Could not calculate area')
                    )
                    
            except Exception as e:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(f'{parcel.parcel_ref}: Error - {str(e)}')
                )
        
        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS(f'Total parcels: {total}'))
        self.stdout.write(self.style.SUCCESS(f'Updated: {updated}'))
        self.stdout.write(f'Unchanged: {unchanged}')
        if errors > 0:
            self.stdout.write(self.style.ERROR(f'Errors: {errors}'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes were saved'))
        else:
            self.stdout.write(self.style.SUCCESS('\nAreas recalculated successfully!'))
