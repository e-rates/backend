"""Test transformation without PROJ errors"""
import os
import sys
import warnings

# Suppress warnings
warnings.filterwarnings('ignore')
os.environ['CPL_DEBUG'] = 'OFF'
os.environ['CPL_LOG'] = 'OFF'

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

# Now test transformation
from erates.shapefile_validator import ShapefileValidator
from django.contrib.gis.geos import Point

print("Testing Arc 1960 UTM 37S to WGS84 transformation...")
print("-" * 50)

# Test point in Arc 1960 UTM 37S
test_point = Point(280000, 9945000, srid=21037)
print(f"Input (EPSG:21037): {test_point.x}, {test_point.y}")

# Transform to WGS84
result = ShapefileValidator.reproject_geometry(test_point, 21037, 4326)

if result:
    print(f"Output (EPSG:4326): {result.x}, {result.y}")
    print(f"✅ Transformation successful!")
    print(f"Location: {result.y}°S, {result.x}°E (Kenya)")
else:
    print("❌ Transformation failed")
