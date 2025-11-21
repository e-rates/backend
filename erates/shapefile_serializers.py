"""
Serializers for shapefile upload and import functionality.
"""
from rest_framework import serializers
from django.core.files.uploadedfile import UploadedFile
import os


class ShapefileUploadSerializer(serializers.Serializer):
    """Serializer for uploading and importing shapefiles"""
    
    zip_file = serializers.FileField(
        required=True,
        help_text="ZIP file containing shapefile (.shp, .shx, .dbf, .prj, etc.)"
    )
    
    county = serializers.CharField(
        required=False,
        max_length=100,
        help_text="County name"
    )
    
    sub_county = serializers.CharField(
        required=False,
        max_length=100,
        help_text="Sub-county name"
    )
    
    ward = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        help_text="Ward name (optional)"
    )
    
    ref_field = serializers.CharField(
        required=False,
        default='PARCEL_ID',
        max_length=100,
        help_text="Shapefile field to use as parcel_ref"
    )
    
    status = serializers.ChoiceField(
        required=False,
        default='active',
        choices=['active', 'disputed', 'transferred', 'archived'],
        help_text="Status for imported parcels"
    )
    
    owner_username = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=150,
        help_text="Username of the owner (if blank, uses current user)"
    )
    
    clear_existing = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Clear existing parcels before importing"
    )
    
    auto_generate_ref = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Auto-generate parcel reference for features missing parcel number. If false, features without parcel numbers will be skipped."
    )
    
    source_epsg = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Source EPSG code if shapefile is missing .prj file (e.g., 21037 for Kenya UTM Zone 37S, 32737 for WGS84 UTM Zone 37S)"
    )
    
    def validate_zip_file(self, value):
        """Validate that uploaded file is a ZIP"""
        if not value.name.endswith('.zip'):
            raise serializers.ValidationError("File must be a ZIP archive")
        
        # Check file size (max 100MB)
        if value.size > 100 * 1024 * 1024:
            raise serializers.ValidationError("ZIP file too large (max 100MB)")
        
        return value


class ShapefileImportResultSerializer(serializers.Serializer):
    """Serializer for import results"""
    
    success = serializers.BooleanField()
    message = serializers.CharField()
    imported_count = serializers.IntegerField()
    skipped_count = serializers.IntegerField()
    error_count = serializers.IntegerField()
    warning_count = serializers.IntegerField(required=False)
    errors = serializers.ListField(
        child=serializers.CharField(),
        required=False
    )
    warnings = serializers.ListField(
        child=serializers.CharField(),
        required=False
    )
    shapefile_info = serializers.DictField(required=False)
