"""
Django management command to import shapefiles into the Parcel model.

Usage:
    python manage.py import_shapefiles <path_to_shapefile> --user <username> [options]

Examples:
    python manage.py import_shapefiles data/parcels.shp --user admin
    python manage.py import_shapefiles data/parcels.shp --user admin --ref-field PARCEL_ID --status active
    python manage.py import_shapefiles data/parcels.shp --user admin --clear
"""

from django.core.management.base import BaseCommand, CommandError
from django.contrib.gis.gdal import DataSource
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.db import transaction
from erates.models import User, Parcel
import os
import warnings


class Command(BaseCommand):
    help = 'Import shapefiles into the Parcel model with GIS data'

    def add_arguments(self, parser):
        # Required arguments
        parser.add_argument(
            'shapefile_path',
            type=str,
            help='Path to the shapefile (.shp file)'
        )
        
        parser.add_argument(
            '--user',
            type=str,
            required=True,
            help='Username of the owner for imported parcels'
        )
        
        # Optional arguments
        parser.add_argument(
            '--ref-field',
            type=str,
            default='PARCEL_ID',
            help='Shapefile field to use as parcel_ref (default: PARCEL_ID)'
        )
        
        parser.add_argument(
            '--status',
            type=str,
            default='active',
            choices=['active', 'disputed', 'transferred', 'archived'],
            help='Status for imported parcels (default: active)'
        )
        
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing parcels before importing'
        )
        
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview import without saving to database'
        )
        
        parser.add_argument(
            '--srid',
            type=int,
            default=4326,
            help='Target SRID for geometry (default: 4326 - WGS84)'
        )

    def handle(self, *args, **options):
        shapefile_path = options['shapefile_path']
        username = options['user']
        ref_field = options['ref_field']
        status = options['status']
        clear = options['clear']
        dry_run = options['dry_run']
        target_srid = options['srid']
        
        # Suppress GDAL/PROJ warnings (common on Windows with multiple PROJ installations)
        warnings.filterwarnings('ignore', category=RuntimeWarning)
        os.environ['CPL_LOG'] = 'OFF'  # Suppress GDAL error messages

        # Validate shapefile exists
        if not os.path.exists(shapefile_path):
            raise CommandError(f'Shapefile not found: {shapefile_path}')

        # Get or validate user
        try:
            owner_user = User.objects.get(username=username)
            self.stdout.write(self.style.SUCCESS(f'Found user: {owner_user.username}'))
        except User.DoesNotExist:
            raise CommandError(f'User not found: {username}')

        # Clear existing parcels if requested
        if clear and not dry_run:
            count = Parcel.objects.count()
            if count > 0:
                confirm = input(f'This will delete {count} existing parcels. Continue? (yes/no): ')
                if confirm.lower() == 'yes':
                    Parcel.objects.all().delete()
                    self.stdout.write(self.style.WARNING(f'Deleted {count} existing parcels'))
                else:
                    self.stdout.write(self.style.ERROR('Import cancelled'))
                    return

        # Open the shapefile
        try:
            ds = DataSource(shapefile_path)
            layer = ds[0]
            
            self.stdout.write(self.style.SUCCESS(f'\nShapefile opened successfully'))
            self.stdout.write(f'  Layer name: {layer.name}')
            self.stdout.write(f'  Features: {len(layer)}')
            self.stdout.write(f'  Geometry type: {layer.geom_type}')
            
            # Determine source SRID
            source_srid = None
            if layer.srs:
                try:
                    source_srid = layer.srs.srid
                except:
                    pass
            
            self.stdout.write(f'  Source SRID: {source_srid if source_srid else "Unknown (assuming WGS84)"}')
            
            # Warn if no SRID but trying to transform
            if not source_srid and target_srid != 4326:
                self.stdout.write(self.style.WARNING(
                    f'  Warning: No SRID in shapefile. Assuming data is already in EPSG:{target_srid}'
                ))
            
            self.stdout.write(f'  Target SRID: {target_srid}')
            self.stdout.write(f'  Fields: {", ".join(layer.fields)}')
            
        except Exception as e:
            raise CommandError(f'Error opening shapefile: {str(e)}')

        # Check if ref_field exists
        if ref_field not in layer.fields:
            self.stdout.write(self.style.WARNING(
                f'\nWarning: Field "{ref_field}" not found in shapefile.'
            ))
            self.stdout.write(f'Available fields: {", ".join(layer.fields)}')
            raise CommandError('Please specify correct --ref-field')

        # Preview mode
        if dry_run:
            self.stdout.write(self.style.WARNING('\n=== DRY RUN MODE - No data will be saved ===\n'))
            self._preview_features(layer, ref_field, owner_user, target_srid, limit=5)
            return

        # Import features
        self.stdout.write(self.style.SUCCESS('\n=== Starting Import ===\n'))
        
        imported = 0
        skipped = 0
        errors = []

        with transaction.atomic():
            for feature in layer:
                try:
                    # Get parcel reference
                    parcel_ref = feature.get(ref_field)
                    if not parcel_ref:
                        skipped += 1
                        errors.append(f'Feature {feature.fid}: Missing {ref_field}')
                        continue

                    # Convert to string and clean
                    parcel_ref = str(parcel_ref).strip()

                    # Check for duplicates
                    if Parcel.objects.filter(parcel_ref=parcel_ref).exists():
                        skipped += 1
                        errors.append(f'Duplicate parcel_ref: {parcel_ref}')
                        continue

                    # Get geometry
                    geom = feature.geom
                    if not geom:
                        skipped += 1
                        errors.append(f'{parcel_ref}: No geometry')
                        continue

                    # Transform to target SRID if needed
                    try:
                        if geom.srid and geom.srid != target_srid:
                            geom.transform(target_srid)
                    except Exception:
                        # If transformation fails, assume it's already in target SRID
                        pass

                    # Convert GDAL geometry to GEOS geometry and explicitly set SRID
                    geos_geom = GEOSGeometry(geom.wkt, srid=target_srid)
                    
                    # Ensure SRID is properly set
                    if not geos_geom.srid:
                        geos_geom.srid = target_srid

                    # Ensure it's a valid polygon/multipolygon
                    if geos_geom.geom_type == 'Polygon':
                        geos_geom = MultiPolygon(geos_geom)
                    elif geos_geom.geom_type not in ['Polygon', 'MultiPolygon']:
                        skipped += 1
                        errors.append(f'{parcel_ref}: Invalid geometry type {geos_geom.geom_type}')
                        continue

                    # Extract all properties from shapefile
                    props = {}
                    area_from_shapefile = None
                    for field in layer.fields:
                        value = feature.get(field)
                        if value is not None:
                            props[field] = str(value) if not isinstance(value, (int, float, bool)) else value
                            
                            # Check for area fields in shapefile
                            field_upper = field.upper()
                            if field_upper in ['AREA_M2', 'AREA', 'AREA_HA', 'AREA_ACRES'] and isinstance(value, (int, float)):
                                if field_upper == 'AREA_HA':
                                    area_from_shapefile = value * 10000  # Convert hectares to m²
                                elif field_upper == 'AREA_ACRES':
                                    area_from_shapefile = value * 4046.86  # Convert acres to m²
                                elif field_upper in ['AREA_M2', 'AREA']:
                                    area_from_shapefile = value

                    # Create parcel - pass area if we found it in shapefile
                    parcel = Parcel(
                        owner_user=owner_user,
                        parcel_ref=parcel_ref,
                        geom=geos_geom,
                        status=status,
                        props=props
                    )
                    
                    # If we found area in shapefile, use it instead of calculating
                    if area_from_shapefile:
                        parcel.area_m2 = area_from_shapefile
                    
                    parcel.save()

                    imported += 1
                    
                    if imported % 100 == 0:
                        self.stdout.write(f'  Imported {imported} parcels...')

                except Exception as e:
                    skipped += 1
                    errors.append(f'Feature {feature.fid}: {str(e)}')

        # Summary
        self.stdout.write(self.style.SUCCESS(f'\n=== Import Complete ==='))
        self.stdout.write(self.style.SUCCESS(f'  Successfully imported: {imported}'))
        
        if skipped > 0:
            self.stdout.write(self.style.WARNING(f'  Skipped: {skipped}'))
            
        if errors:
            self.stdout.write(self.style.ERROR(f'\n=== Errors ({len(errors)}) ==='))
            for error in errors[:10]:  # Show first 10 errors
                self.stdout.write(self.style.ERROR(f'  - {error}'))
            if len(errors) > 10:
                self.stdout.write(self.style.ERROR(f'  ... and {len(errors) - 10} more'))

    def _preview_features(self, layer, ref_field, owner_user, target_srid, limit=5):
        """Preview first few features without importing"""
        self.stdout.write(f'Preview of first {limit} features:\n')
        
        for i, feature in enumerate(layer):
            if i >= limit:
                break
                
            parcel_ref = feature.get(ref_field)
            geom = feature.geom
            
            self.stdout.write(f'\n--- Feature {i + 1} ---')
            self.stdout.write(f'  Parcel Ref: {parcel_ref}')
            self.stdout.write(f'  Geometry Type: {geom.geom_type if geom else "None"}')
            self.stdout.write(f'  Area (approx): {geom.area if geom else "N/A"}')
            
            # Show all attributes
            self.stdout.write(f'  Attributes:')
            for field in layer.fields:
                value = feature.get(field)
                self.stdout.write(f'    - {field}: {value}')
        
        self.stdout.write(f'\nTotal features in shapefile: {len(layer)}')
        self.stdout.write(f'Will be assigned to user: {owner_user.username}')
        self.stdout.write(f'\nRun without --dry-run to import')
