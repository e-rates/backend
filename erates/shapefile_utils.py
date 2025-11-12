"""
Utility functions for shapefile upload and import.
"""
from django.contrib.gis.gdal import DataSource
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.db import transaction
from erates.models import User, Parcel
import os
import zipfile
import tempfile
import shutil
from typing import Dict, List, Tuple, Optional


class ShapefileImporter:
    """Handle shapefile extraction and import"""
    
    def __init__(self, zip_file, ref_field='PARCEL_ID', status='active', 
                 owner_user=None, clear_existing=False):
        self.zip_file = zip_file
        self.ref_field = ref_field
        self.status = status
        self.owner_user = owner_user
        self.clear_existing = clear_existing
        
        self.temp_dir = None
        self.shapefile_path = None
        
        self.imported = 0
        self.skipped = 0
        self.errors = []
    
    def extract_zip(self) -> str:
        """Extract ZIP file to temporary directory and find .shp file"""
        # Create temporary directory
        self.temp_dir = tempfile.mkdtemp(prefix='shapefile_import_')
        
        try:
            # Extract ZIP
            with zipfile.ZipFile(self.zip_file, 'r') as zip_ref:
                zip_ref.extractall(self.temp_dir)
            
            # Find .shp file
            shp_files = []
            for root, dirs, files in os.walk(self.temp_dir):
                for file in files:
                    if file.lower().endswith('.shp'):
                        shp_files.append(os.path.join(root, file))
            
            if not shp_files:
                raise ValueError("No .shp file found in ZIP archive")
            
            if len(shp_files) > 1:
                raise ValueError(f"Multiple .shp files found. Please upload only one shapefile.")
            
            self.shapefile_path = shp_files[0]
            return self.shapefile_path
            
        except zipfile.BadZipFile:
            raise ValueError("Invalid ZIP file")
        except Exception as e:
            self.cleanup()
            raise ValueError(f"Error extracting ZIP: {str(e)}")
    
    def get_shapefile_info(self) -> Dict:
        """Get information about the shapefile"""
        if not self.shapefile_path:
            raise ValueError("Shapefile not extracted yet")
        
        try:
            ds = DataSource(self.shapefile_path)
            layer = ds[0]
            
            return {
                'layer_name': layer.name,
                'feature_count': len(layer),
                'geometry_type': layer.geom_type.name,
                'srid': layer.srs.srid if layer.srs else None,
                'fields': list(layer.fields),
                'extent': layer.extent.tuple if hasattr(layer, 'extent') else None,
            }
        except Exception as e:
            raise ValueError(f"Error reading shapefile: {str(e)}")
    
    def validate_shapefile(self) -> Tuple[bool, Optional[str]]:
        """Validate shapefile structure"""
        try:
            ds = DataSource(self.shapefile_path)
            layer = ds[0]
            
            # Check if ref_field exists
            if self.ref_field not in layer.fields:
                available_fields = ', '.join(layer.fields)
                return False, f"Field '{self.ref_field}' not found. Available: {available_fields}"
            
            # Check geometry type
            if layer.geom_type.name not in ['Polygon', 'MultiPolygon', 'Polygon25D']:
                return False, f"Invalid geometry type: {layer.geom_type.name}. Only Polygon/MultiPolygon supported."
            
            return True, None
            
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    def import_parcels(self) -> Dict:
        """Import parcels from shapefile"""
        if not self.shapefile_path:
            raise ValueError("Shapefile not extracted yet")
        
        # Validate first
        is_valid, error_msg = self.validate_shapefile()
        if not is_valid:
            raise ValueError(error_msg)
        
        # Clear existing if requested
        if self.clear_existing:
            count = Parcel.objects.count()
            Parcel.objects.all().delete()
            self.errors.append(f"Cleared {count} existing parcels")
        
        try:
            ds = DataSource(self.shapefile_path)
            layer = ds[0]
            
            # Suppress GDAL warnings
            os.environ['CPL_LOG'] = 'OFF'
            
            # Check for area fields
            area_field = None
            for field in ['AREA_M2', 'AREA_SQM', 'AREA', 'Shape_Area']:
                if field in layer.fields:
                    area_field = field
                    break
            
            # Convert hectares if available
            area_ha_field = None
            area_acres_field = None
            for field in ['AREA_HA', 'AREA_HECTARES', 'HA']:
                if field in layer.fields:
                    area_ha_field = field
                    break
            for field in ['AREA_ACRES', 'ACRES']:
                if field in layer.fields:
                    area_acres_field = field
                    break
            
            with transaction.atomic():
                for feature in layer:
                    try:
                        # Get parcel reference
                        parcel_ref = feature.get(self.ref_field)
                        if not parcel_ref:
                            self.skipped += 1
                            self.errors.append(f'Feature {feature.fid}: Missing {self.ref_field}')
                            continue
                        
                        parcel_ref = str(parcel_ref).strip()
                        
                        # Check for duplicates
                        if Parcel.objects.filter(parcel_ref=parcel_ref).exists():
                            self.skipped += 1
                            self.errors.append(f'Duplicate: {parcel_ref}')
                            continue
                        
                        # Get geometry
                        geom = feature.geom
                        if not geom:
                            self.skipped += 1
                            self.errors.append(f'{parcel_ref}: No geometry')
                            continue
                        
                        # Convert to GEOS geometry
                        geos_geom = GEOSGeometry(geom.wkt, srid=4326)
                        if not geos_geom.srid:
                            geos_geom.srid = 4326
                        
                        # Ensure polygon type
                        if geos_geom.geom_type == 'Polygon':
                            geos_geom = MultiPolygon(geos_geom)
                        elif geos_geom.geom_type not in ['Polygon', 'MultiPolygon']:
                            self.skipped += 1
                            self.errors.append(f'{parcel_ref}: Invalid geometry type')
                            continue
                        
                        # Get area from shapefile if available
                        area_m2 = None
                        if area_field:
                            area_m2 = float(feature.get(area_field))
                        elif area_ha_field:
                            area_m2 = float(feature.get(area_ha_field)) * 10000
                        elif area_acres_field:
                            area_m2 = float(feature.get(area_acres_field)) * 4046.86
                        
                        # Extract all properties
                        props = {}
                        for field in layer.fields:
                            value = feature.get(field)
                            if value is not None:
                                props[field] = str(value) if not isinstance(value, (int, float, bool)) else value
                        
                        # Create parcel
                        parcel = Parcel(
                            owner_user=self.owner_user,
                            parcel_ref=parcel_ref,
                            geom=geos_geom,
                            status=self.status,
                            props=props
                        )
                        
                        # Set area if we got it from shapefile
                        if area_m2:
                            parcel.area_m2 = area_m2
                        
                        parcel.save()
                        self.imported += 1
                        
                    except Exception as e:
                        self.skipped += 1
                        self.errors.append(f'Feature {feature.fid}: {str(e)}')
            
            return {
                'success': True,
                'message': f'Successfully imported {self.imported} parcels',
                'imported_count': self.imported,
                'skipped_count': self.skipped,
                'error_count': len(self.errors),
                'errors': self.errors[:20] if self.errors else [],  # Limit errors shown
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'Import failed: {str(e)}',
                'imported_count': self.imported,
                'skipped_count': self.skipped,
                'error_count': len(self.errors),
                'errors': self.errors[:20] if self.errors else [],
            }
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Clean up temporary files"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass  # Ignore cleanup errors
    
    def __del__(self):
        """Ensure cleanup on deletion"""
        self.cleanup()
