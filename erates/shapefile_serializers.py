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
    errors = serializers.ListField(
        child=serializers.CharField(),
        required=False
    )
    shapefile_info = serializers.DictField(required=False)
