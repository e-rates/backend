"""
Shapefile validation utilities for detecting and reporting common issues.
"""
from django.contrib.gis.geos import GEOSGeometry
from typing import Dict, Tuple
import logging

logger = logging.getLogger(__name__)


class ShapefileValidator:
    """Validate and provide detailed feedback on shapefile data"""
    
    @staticmethod
    def validate_coordinates(geom: GEOSGeometry, feature_index: int, parcel_ref: str = None) -> Tuple[bool, str]:
        """
        Check if geometry coordinates are within valid WGS84 bounds
        Returns (is_valid, error_message)
        """
        try:
            extent = geom.extent  # (xmin, ymin, xmax, ymax)
            
            # WGS84 bounds: Longitude [-180, 180], Latitude [-90, 90]
            if extent[0] < -180 or extent[2] > 180:
                msg = f"• {feature_index}: Coordinates out of valid WGS84 range"
                if parcel_ref:
                    msg += f" (Parcel: {parcel_ref})"
                return False, msg
            
            if extent[1] < -90 or extent[3] > 90:
                msg = f"• {feature_index}: Coordinates out of valid WGS84 range"
                if parcel_ref:
                    msg += f" (Parcel: {parcel_ref})"
                return False, msg
            
            return True, ""
        except Exception as e:
            return False, f"• {feature_index}: Cannot validate coordinates - {str(e)}"
    
    @staticmethod
    def fix_geometry(geom: GEOSGeometry) -> Tuple[GEOSGeometry, bool]:
        """
        Attempt to fix invalid geometry
        Returns (fixed_geometry, was_fixed)
        """
        if geom.valid:
            return geom, False
        
        try:
            # Buffer(0) is a common fix for invalid geometries
            fixed = geom.buffer(0)
            if fixed.valid:
                logger.info(f"Fixed invalid geometry using buffer(0)")
                return fixed, True
        except Exception as e:
            logger.error(f"Could not fix geometry: {e}")
        
        return geom, False
    
    @staticmethod
    def detect_coordinate_system(extent: Tuple[float, float, float, float]) -> str:
        """
        Attempt to detect the coordinate system based on extent values
        Returns a descriptive string
        """
        xmin, ymin, xmax, ymax = extent
        
        # Check if it's in valid WGS84 range
        if -180 <= xmin <= 180 and -180 <= xmax <= 180 and -90 <= ymin <= 90 and -90 <= ymax <= 90:
            return "WGS84 (EPSG:4326)"
        
        # Check for common projected coordinate systems
        if 100000 <= abs(xmin) <= 1000000 or 100000 <= abs(ymin) <= 10000000:
            return "Projected (possibly UTM or local grid)"
        
        if 1000000 <= abs(xmin) <= 100000000:
            return "Projected (possibly Web Mercator EPSG:3857)"
        
        return "Unknown coordinate system"
    
    @staticmethod
    def validate_feature(feature: Dict, feature_index: int, ref_field: str = 'PARCEL_ID') -> Dict:
        """
        Validate a single feature from shapefile
        Returns dict with validation results
        """
        errors = []
        warnings = []
        
        # Check for required parcel number
        parcel_no = feature.get('properties', {}).get(ref_field)
        if not parcel_no or str(parcel_no).strip() == '':
            errors.append(f"• Feature {feature_index}: Missing {ref_field}")
            parcel_no = None
        
        # Check geometry
        geom = feature.get('geometry')
        if not geom:
            errors.append(f"• Feature {feature_index}: Missing geometry")
        else:
            try:
                geos_geom = GEOSGeometry(str(geom))
                
                # Validate coordinates
                is_valid, error_msg = ShapefileValidator.validate_coordinates(
                    geos_geom, feature_index, parcel_no
                )
                if not is_valid:
                    errors.append(error_msg)
                
                # Check if geometry is valid
                if not geos_geom.valid:
                    warnings.append(f"• Feature {feature_index}: Invalid geometry (will attempt repair)")
                
            except Exception as e:
                errors.append(f"• Feature {feature_index}: Cannot parse geometry - {e}")
        
        return {
            'feature_index': feature_index,
            'parcel_no': parcel_no,
            'errors': errors,
            'warnings': warnings,
            'is_valid': len(errors) == 0
        }
    
    @staticmethod
    def analyze_shapefile_srid(layer) -> Dict:
        """
        Analyze the coordinate system of a shapefile layer
        """
        try:
            srid = layer.srs.srid if layer.srs else None
            extent = layer.extent.tuple if hasattr(layer, 'extent') else None
            
            if extent:
                detected_cs = ShapefileValidator.detect_coordinate_system(extent)
            else:
                detected_cs = "Cannot determine"
            
            return {
                'declared_srid': srid,
                'detected_system': detected_cs,
                'extent': extent,
                'needs_transformation': srid and srid != 4326
            }
        except Exception as e:
            return {
                'error': str(e)
            }
