"""
Shapefile validation utilities for detecting and reporting common issues.
"""
from django.contrib.gis.geos import GEOSGeometry
from django.contrib.gis.gdal import SpatialReference, CoordTransform
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class ShapefileValidator:
    """Validate and provide detailed feedback on shapefile data"""
    
    @staticmethod
    def validate_projection_accuracy(geom: GEOSGeometry, source_epsg: int, target_epsg: int = 4326) -> Dict:
        """
        Validate that a geometry's projection transformation appears accurate.
        Returns dict with validation status and details.
        """
        try:
            from pyproj import CRS, Transformer
            
            # Get CRS information
            source_crs = CRS.from_epsg(source_epsg)
            target_crs = CRS.from_epsg(target_epsg)
            
            # Check if transformation is appropriate
            is_geographic_to_projected = source_crs.is_geographic and target_crs.is_projected
            is_projected_to_geographic = source_crs.is_projected and target_crs.is_geographic
            
            # Get geometry extent
            extent = geom.extent  # (xmin, ymin, xmax, ymax)
            
            # Validate based on CRS types
            issues = []
            
            if target_epsg == 4326:  # Target is WGS84
                # Check bounds
                if extent[0] < -180 or extent[2] > 180:
                    issues.append(f"Longitude out of bounds: {extent[0]:.6f} to {extent[2]:.6f}")
                if extent[1] < -90 or extent[3] > 90:
                    issues.append(f"Latitude out of bounds: {extent[1]:.6f} to {extent[3]:.6f}")
                
                # Check if coordinates look reasonable for Kenya
                kenya_bounds = (33.0, -5.0, 42.0, 6.0)  # (west, south, east, north)
                if not (kenya_bounds[0] <= extent[0] <= kenya_bounds[2] and
                        kenya_bounds[1] <= extent[1] <= kenya_bounds[3]):
                    issues.append(f"Coordinates outside Kenya bounds. Extent: {extent}")
            
            return {
                'is_valid': len(issues) == 0,
                'source_crs': source_crs.name,
                'target_crs': target_crs.name,
                'extent': extent,
                'issues': issues
            }
            
        except Exception as e:
            return {
                'is_valid': False,
                'error': str(e)
            }
    
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
    
    @staticmethod
    def suggest_epsg_from_extent(extent: Tuple[float, float, float, float], country: str = 'Kenya') -> List[Dict[str, any]]:
        """
        Suggest likely EPSG codes based on coordinate extent and country
        Returns list of suggestions with confidence scores
        """
        xmin, ymin, xmax, ymax = extent
        suggestions = []
        
        # Check if already in WGS84 range
        if -180 <= xmin <= 180 and -180 <= xmax <= 180 and -90 <= ymin <= 90 and -90 <= ymax <= 90:
            suggestions.append({
                'epsg': 4326,
                'name': 'WGS 84 (Geographic)',
                'confidence': 'high',
                'reason': 'Coordinates within valid WGS84 range'
            })
            return suggestions
        
        # Kenya-specific projections
        if country.lower() == 'kenya':
            # Check if coordinates look like Kenya Arc 1960 UTM zones
            if 100000 <= xmin <= 900000 and 9800000 <= ymin <= 10500000:
                suggestions.append({
                    'epsg': 21037,
                    'name': 'Arc 1960 / UTM zone 37S (Kenya)',
                    'confidence': 'high',
                    'reason': 'Coordinates match Kenya Arc 1960 UTM 37S range'
                })
                suggestions.append({
                    'epsg': 32737,
                    'name': 'WGS 84 / UTM zone 37S',
                    'confidence': 'medium',
                    'reason': 'Alternative UTM zone 37S projection'
                })
            elif 100000 <= xmin <= 900000 and 9000000 <= ymin <= 10500000:
                suggestions.append({
                    'epsg': 21036,
                    'name': 'Arc 1960 / UTM zone 36S (Kenya)',
                    'confidence': 'high',
                    'reason': 'Coordinates match Kenya Arc 1960 UTM 36S range'
                })
                suggestions.append({
                    'epsg': 32736,
                    'name': 'WGS 84 / UTM zone 36S',
                    'confidence': 'medium',
                    'reason': 'Alternative UTM zone 36S projection'
                })
        
        # General UTM detection (Southern Hemisphere)
        if 100000 <= xmin <= 900000 and 1000000 <= ymin <= 10000000:
            suggestions.append({
                'epsg': None,
                'name': 'Likely UTM Southern Hemisphere',
                'confidence': 'medium',
                'reason': 'Coordinates match UTM projection pattern. Specify exact zone.'
            })
        
        # Web Mercator
        if 1000000 <= abs(xmin) <= 20000000 and 1000000 <= abs(ymin) <= 20000000:
            suggestions.append({
                'epsg': 3857,
                'name': 'WGS 84 / Pseudo-Mercator (Web Mercator)',
                'confidence': 'low',
                'reason': 'Coordinates in Web Mercator range (used by Google Maps, OpenStreetMap)'
            })
        
        return suggestions if suggestions else [{
            'epsg': None,
            'name': 'Unknown',
            'confidence': 'none',
            'reason': 'Could not determine coordinate system from extent'
        }]
    
    @staticmethod
    def reproject_geometry(geom, source_epsg: int, target_epsg: int = 4326) -> Optional[GEOSGeometry]:
        """
        Reproject geometry from source EPSG to target EPSG using pyproj ONLY
        Avoids GDAL/PROJ database version conflicts by using pyproj directly
        Returns reprojected GEOSGeometry or None if transformation fails
        """
        try:
            from pyproj import Transformer, CRS
            from shapely.geometry import shape
            from shapely.ops import transform as shapely_transform
            import json
            
            # Convert to GEOS geometry first if needed
            if isinstance(geom, GEOSGeometry):
                geos_geom = geom
            else:
                # Convert from GDAL geometry
                geos_geom = GEOSGeometry(geom.wkt, srid=source_epsg)
            
            # If already in target EPSG, no transformation needed
            if source_epsg == target_epsg:
                return geos_geom
            
            # Validate that source and target CRS are valid
            try:
                source_crs = CRS.from_epsg(source_epsg)
                target_crs = CRS.from_epsg(target_epsg)
            except Exception as e:
                logger.error(f"Invalid EPSG codes: source={source_epsg}, target={target_epsg}: {e}")
                return None
            
            # Use pyproj for transformation (avoids PROJ database version issues)
            # Create transformer with proper datum shift handling
            # always_xy=True ensures lon,lat order (x=lon, y=lat)
            # Set area_of_interest for Kenya to improve accuracy (Kenya bounds approximately)
            transformer = Transformer.from_crs(
                source_crs,
                target_crs,
                always_xy=True,
                area_of_interest=(33.0, -5.0, 42.0, 6.0)  # Kenya bounding box (west, south, east, north)
            )
            
            # Convert GEOS to shapely for transformation
            geom_json = json.loads(geos_geom.json)
            shapely_geom = shape(geom_json)
            
            # Transform using pyproj
            transformed_geom = shapely_transform(transformer.transform, shapely_geom)
            
            # Validate transformed geometry
            if not transformed_geom.is_valid:
                # Try to fix with buffer(0)
                transformed_geom = transformed_geom.buffer(0)
                if not transformed_geom.is_valid:
                    logger.error(f"Transformed geometry is invalid after repair")
                    return None
            
            # Convert back to GEOS
            transformed_wkt = transformed_geom.wkt
            result_geom = GEOSGeometry(transformed_wkt, srid=target_epsg)
            
            # Final validation: check if coordinates are within expected bounds for target CRS
            if target_epsg == 4326:
                extent = result_geom.extent
                if not (-180 <= extent[0] <= 180 and -180 <= extent[2] <= 180 and
                        -90 <= extent[1] <= 90 and -90 <= extent[3] <= 90):
                    logger.error(f"Transformed coordinates out of WGS84 bounds: {extent}")
                    return None
            
            return result_geom
            
        except ImportError as ie:
            logger.error(f"pyproj or shapely not available: {ie}")
            return None
        except Exception as e:
            logger.error(f"Reprojection failed from EPSG:{source_epsg} to EPSG:{target_epsg}: {e}")
            return None
