"""
Utility functions for shapefile upload and import.
"""
from django.contrib.gis.gdal import DataSource
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.db import transaction
from erates.models import User, Parcel
from erates.shapefile_validator import ShapefileValidator
import os
import zipfile
import tempfile
import shutil
import uuid
from typing import Dict, List, Tuple, Optional


class ShapefileImporter:
    """Handle shapefile extraction and import"""
    
    def __init__(self, zip_file, ref_field='PARCEL_ID', status='active', 
                 owner_user=None, clear_existing=False, location_metadata=None,
                 auto_generate_ref=False, source_epsg=None):
        self.zip_file = zip_file
        self.ref_field = ref_field
        self.status = status
        self.owner_user = owner_user
        self.clear_existing = clear_existing
        self.location_metadata = location_metadata or {}
        self.auto_generate_ref = auto_generate_ref  # Auto-generate parcel refs if missing
        self.source_epsg = source_epsg  # Manual EPSG override if .prj missing
        
        self.temp_dir = None
        self.shapefile_path = None
        
        self.imported = 0
        self.skipped = 0
        self.errors = []
        self.warnings = []
    
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
            
            # Get extent and analyze coordinate system
            extent = layer.extent.tuple if hasattr(layer, 'extent') else None
            srid = layer.srs.srid if layer.srs else None
            
            # Detect coordinate system type
            cs_type = "Unknown"
            if extent:
                xmin, ymin, xmax, ymax = extent
                if -180 <= xmin <= 180 and -180 <= xmax <= 180 and -90 <= ymin <= 90 and -90 <= ymax <= 90:
                    cs_type = "Geographic (WGS84 compatible)"
                elif 100000 <= abs(xmin) <= 1000000 or 100000 <= abs(ymin) <= 10000000:
                    cs_type = "Projected (UTM or local grid)"
                elif 1000000 <= abs(xmin):
                    cs_type = "Projected (Web Mercator or similar)"
            
            return {
                'layer_name': layer.name,
                'feature_count': len(layer),
                'geometry_type': layer.geom_type.name,
                'srid': srid,
                'coordinate_system_type': cs_type,
                'fields': list(layer.fields),
                'extent': extent,
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
        
        # Get shapefile info for pre-import checks
        try:
            ds = DataSource(self.shapefile_path)
            layer = ds[0]
            
            # Check if we can get extent
            if hasattr(layer, 'extent'):
                extent = layer.extent.tuple
                xmin, ymin, xmax, ymax = extent
                
                # Check if coordinates look like they're in a projected system
                if not (-180 <= xmin <= 180 and -180 <= xmax <= 180 and -90 <= ymin <= 90 and -90 <= ymax <= 90):
                    source_srid = layer.srs.srid if layer.srs else None
                    if not source_srid or source_srid == 4326:
                        raise ValueError(
                            f"Shapefile coordinates appear to be in a projected coordinate system "
                            f"(extent: {xmin:.2f}, {ymin:.2f}, {xmax:.2f}, {ymax:.2f}), "
                            f"but no valid coordinate system is defined in the .prj file. "
                            f"Please ensure your shapefile includes a proper .prj file with coordinate system information."
                        )
        except ValueError:
            raise
        except Exception:
            pass  # Continue with import if pre-check fails
        
        # Clear existing if requested
        if self.clear_existing:
            count = Parcel.objects.count()
            Parcel.objects.all().delete()
            self.warnings.append(f"Cleared {count} existing parcels")
        
        try:
            ds = DataSource(self.shapefile_path)
            layer = ds[0]
            
            # Get coordinate system info
            source_srid = layer.srs.srid if layer.srs else None
            
            # Use manual EPSG override if provided
            if self.source_epsg:
                source_srid = self.source_epsg
                self.warnings.append(f"Using manual EPSG:{source_srid} (overriding shapefile .prj)")
            
            if source_srid:
                if source_srid == 4326:
                    self.warnings.append(f"Shapefile is in WGS84 (EPSG:4326) - no transformation needed")
                else:
                    self.warnings.append(f"Shapefile is in EPSG:{source_srid} - will transform to WGS84")
            else:
                self.warnings.append("WARNING: Shapefile has no coordinate system (.prj file). Assuming WGS84. If import fails, provide source_epsg parameter.")
            
            # Suppress GDAL warnings and errors in output
            os.environ['CPL_LOG'] = 'OFF'
            os.environ['CPL_DEBUG'] = 'OFF'
            
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
                for idx, feature in enumerate(layer, start=1):
                    try:
                        # Get parcel reference
                        parcel_ref = feature.get(self.ref_field)
                        
                        # Handle missing parcel reference
                        if not parcel_ref or str(parcel_ref).strip() in ['', 'NONE', 'NULL']:
                            if self.auto_generate_ref:
                                # Auto-generate a unique parcel reference
                                parcel_ref = f"AUTO-{uuid.uuid4().hex[:8].upper()}"
                                self.warnings.append(f'Feature {idx}: Missing {self.ref_field}, generated: {parcel_ref}')
                            else:
                                self.skipped += 1
                                self.errors.append(f'• Feature {idx}: Missing {self.ref_field}')
                                continue
                        else:
                            # Normalize parcel_ref (trim, uppercase for consistency)
                            parcel_ref = str(parcel_ref).strip().upper()
                        
                        # Check for duplicates (case-insensitive)
                        if Parcel.objects.filter(parcel_ref__iexact=parcel_ref).exists():
                            self.skipped += 1
                            self.errors.append(f'Duplicate: {parcel_ref}')
                            continue
                        
                        # Get geometry
                        geom = feature.geom
                        if not geom:
                            self.skipped += 1
                            self.errors.append(f'{parcel_ref}: No geometry')
                            continue
                        
                        # Get source SRID from layer or use manual override
                        source_srid = self.source_epsg if self.source_epsg else (layer.srs.srid if layer.srs else None)
                        
                        # Convert to GEOS geometry with proper SRID handling
                        try:
                            if source_srid and source_srid != 4326:
                                # Has SRID and it's not WGS84, need transformation
                                geos_geom = GEOSGeometry(geom.wkt, srid=source_srid)
                                try:
                                    geos_geom.transform(4326)
                                except Exception as transform_error:
                                    self.skipped += 1
                                    self.errors.append(f'• {idx}: Cannot transform from SRID {source_srid} to WGS84 (Parcel: {parcel_ref})')
                                    continue
                            else:
                                # No SRID or already WGS84
                                geos_geom = GEOSGeometry(geom.wkt, srid=4326)
                                
                                # If no SRID was declared, check if coordinates look like they need transformation
                                if not source_srid:
                                    extent = geos_geom.extent
                                    # Check if coordinates are outside WGS84 range
                                    if not (-180 <= extent[0] <= 180 and -180 <= extent[2] <= 180 and
                                           -90 <= extent[1] <= 90 and -90 <= extent[3] <= 90):
                                        self.skipped += 1
                                        self.errors.append(
                                            f'• {idx}: Coordinates out of valid WGS84 range (Parcel: {parcel_ref}). '
                                            f'Shapefile missing .prj file or has invalid coordinate system.'
                                        )
                                        continue
                        except Exception as e:
                            self.skipped += 1
                            self.errors.append(f'• {idx}: Geometry conversion error - {str(e)} (Parcel: {parcel_ref})')
                            continue
                        
                        # Validate geometry type (accept both Polygon and MultiPolygon)
                        if geos_geom.geom_type not in ['Polygon', 'MultiPolygon']:
                            self.skipped += 1
                            self.errors.append(f'{parcel_ref}: Invalid geometry type ({geos_geom.geom_type})')
                            continue
                        
                        # Validate geometry is valid
                        if not geos_geom.valid:
                            # Try to fix invalid geometries with buffer(0) trick
                            try:
                                geos_geom = geos_geom.buffer(0)
                                if not geos_geom.valid:
                                    self.skipped += 1
                                    self.errors.append(f'{parcel_ref}: Invalid geometry (cannot fix)')
                                    continue
                            except Exception:
                                self.skipped += 1
                                self.errors.append(f'{parcel_ref}: Invalid geometry')
                                continue
                        
                        # Final validation: ensure coordinates are in valid WGS84 range after transformation
                        try:
                            extent = geos_geom.extent  # (xmin, ymin, xmax, ymax)
                            if not (-180 <= extent[0] <= 180 and -180 <= extent[2] <= 180 and
                                    -90 <= extent[1] <= 90 and -90 <= extent[3] <= 90):
                                self.skipped += 1
                                self.errors.append(
                                    f'• {idx}: Coordinates still out of valid WGS84 range after transformation (Parcel: {parcel_ref})'
                                )
                                continue
                        except Exception:
                            pass  # Skip validation if extent fails
                        
                        # Get area from shapefile if available
                        area_m2 = None
                        try:
                            if area_field and feature.get(area_field):
                                area_val = feature.get(area_field)
                                if area_val and float(area_val) > 0:
                                    area_m2 = float(area_val)
                            elif area_ha_field and feature.get(area_ha_field):
                                area_val = feature.get(area_ha_field)
                                if area_val and float(area_val) > 0:
                                    area_m2 = float(area_val) * 10000
                            elif area_acres_field and feature.get(area_acres_field):
                                area_val = feature.get(area_acres_field)
                                if area_val and float(area_val) > 0:
                                    area_m2 = float(area_val) * 4046.86
                        except (ValueError, TypeError):
                            area_m2 = None  # Let Django calculate it
                        
                        # Extract all properties with encoding handling
                        props = {}
                        for field in layer.fields:
                            try:
                                value = feature.get(field)
                                if value is not None:
                                    if isinstance(value, (int, float, bool)):
                                        props[field] = value
                                    elif isinstance(value, bytes):
                                        # Handle byte strings (common in shapefiles)
                                        props[field] = value.decode('utf-8', errors='replace')
                                    else:
                                        # Convert to string safely
                                        props[field] = str(value)
                            except Exception as e:
                                # Skip fields that cause errors
                                props[field] = f'<encoding error: {str(e)[:50]}>'
                        
                        # Merge location metadata into props
                        props.update(self.location_metadata)
                        
                        # Create parcel
                        parcel = Parcel(
                            owner_user=self.owner_user,
                            parcel_ref=parcel_ref,
                            geom=geos_geom,
                            status=self.status,
                            county=self.location_metadata.get('county', ''),
                            sub_county=self.location_metadata.get('sub_county', ''),
                            ward=self.location_metadata.get('ward', ''),
                            props=props
                        )
                        
                        # Set area if we got it from shapefile
                        if area_m2:
                            parcel.area_m2 = area_m2
                        
                        parcel.save()
                        self.imported += 1
                        
                    except Exception as e:
                        self.skipped += 1
                        self.errors.append(f'Feature {idx}: {str(e)}')
            
            return {
                'success': True,
                'message': f'Successfully imported {self.imported} parcels',
                'imported_count': self.imported,
                'skipped_count': self.skipped,
                'error_count': len(self.errors),
                'warning_count': len(self.warnings),
                'errors': self.errors[:50] if self.errors else [],  # Show up to 50 errors
                'warnings': self.warnings[:20] if self.warnings else [],
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'Import failed: {str(e)}',
                'imported_count': self.imported,
                'skipped_count': self.skipped,
                'error_count': len(self.errors),
                'warning_count': len(self.warnings),
                'errors': self.errors[:50] if self.errors else [],
                'warnings': self.warnings[:20] if self.warnings else [],
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
