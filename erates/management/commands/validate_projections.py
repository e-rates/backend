"""
Management command to validate projection accuracy of parcels.
Checks for any alignment issues or coordinate problems.

Usage:
    python manage.py validate_projections
    python manage.py validate_projections --parcel-ref PARCEL_ID
"""
from django.core.management.base import BaseCommand
from erates.models import Parcel


class Command(BaseCommand):
    help = 'Validate parcel projections and check for coordinate issues'

    def add_arguments(self, parser):
        parser.add_argument(
            '--parcel-ref',
            type=str,
            help='Validate only specific parcel reference',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed information for each parcel',
        )

    def handle(self, *args, **options):
        parcel_ref = options.get('parcel_ref')
        verbose = options['verbose']
        
        # Get queryset
        if parcel_ref:
            parcels = Parcel.objects.filter(parcel_ref=parcel_ref, is_deleted=False)
            if not parcels.exists():
                self.stdout.write(self.style.ERROR(f'Parcel not found: {parcel_ref}'))
                return
        else:
            parcels = Parcel.objects.filter(is_deleted=False)
        
        total = parcels.count()
        self.stdout.write(f'Validating {total} parcels...\n')
        
        valid_count = 0
        invalid_count = 0
        warning_count = 0
        
        # Kenya bounds for reference
        kenya_bounds = {
            'west': 33.0,
            'south': -5.0,
            'east': 42.0,
            'north': 6.0
        }
        
        for parcel in parcels:
            issues = []
            warnings = []
            
            try:
                # Check SRID
                if not parcel.geom.srid or parcel.geom.srid != 4326:
                    issues.append(f'Invalid SRID: {parcel.geom.srid} (should be 4326)')
                
                # Get extent
                extent = parcel.geom.extent  # (xmin, ymin, xmax, ymax)
                
                # Check WGS84 bounds
                if extent[0] < -180 or extent[2] > 180:
                    issues.append(f'Longitude out of bounds: {extent[0]:.6f} to {extent[2]:.6f}')
                
                if extent[1] < -90 or extent[3] > 90:
                    issues.append(f'Latitude out of bounds: {extent[1]:.6f} to {extent[3]:.6f}')
                
                # Check Kenya bounds (warning only)
                if not (kenya_bounds['west'] <= extent[0] <= kenya_bounds['east'] and
                        kenya_bounds['south'] <= extent[1] <= kenya_bounds['north']):
                    warnings.append(f'Outside Kenya bounds: ({extent[0]:.6f}, {extent[1]:.6f}) to ({extent[2]:.6f}, {extent[3]:.6f})')
                
                # Check geometry validity
                if not parcel.geom.valid:
                    issues.append('Invalid geometry')
                
                # Check if area makes sense
                if parcel.area_m2:
                    # Very small (< 1 m²) or very large (> 100 km²) might indicate issues
                    if parcel.area_m2 < 1:
                        warnings.append(f'Very small area: {parcel.area_m2:.6f} m²')
                    elif parcel.area_m2 > 100_000_000:  # 100 km²
                        warnings.append(f'Very large area: {parcel.area_m2:.2f} m² ({parcel.area_m2/1_000_000:.2f} km²)')
                
                # Display results
                if issues:
                    invalid_count += 1
                    self.stdout.write(
                        self.style.ERROR(f'[X] {parcel.parcel_ref}:')
                    )
                    for issue in issues:
                        self.stdout.write(f'   - {issue}')
                elif warnings:
                    warning_count += 1
                    if verbose:
                        self.stdout.write(
                            self.style.WARNING(f'[!] {parcel.parcel_ref}:')
                        )
                        for warning in warnings:
                            self.stdout.write(f'   - {warning}')
                else:
                    valid_count += 1
                    if verbose:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'[OK] {parcel.parcel_ref}: OK '
                                f'({extent[0]:.6f}, {extent[1]:.6f}) to ({extent[2]:.6f}, {extent[3]:.6f}), '
                                f'Area: {parcel.area_m2:.2f} m^2'
                            )
                        )
                
            except Exception as e:
                invalid_count += 1
                self.stdout.write(
                    self.style.ERROR(f'[X] {parcel.parcel_ref}: Error - {str(e)}')
                )
        
        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(f'Total parcels: {total}')
        self.stdout.write(self.style.SUCCESS(f'[OK] Valid: {valid_count}'))
        if warning_count > 0:
            self.stdout.write(self.style.WARNING(f'[!] Warnings: {warning_count}'))
        if invalid_count > 0:
            self.stdout.write(self.style.ERROR(f'[X] Invalid: {invalid_count}'))
        
        if invalid_count == 0:
            self.stdout.write(self.style.SUCCESS('\n[OK] All parcels have valid projections!'))
        else:
            self.stdout.write(self.style.ERROR(f'\n[!] Found {invalid_count} parcels with projection issues'))
            self.stdout.write('Run with --verbose flag to see all details')
