"""
Test script for shapefile upload endpoint.
Creates a ZIP file from your shapefile and tests the upload.
"""

import requests
import zipfile
import os
from pathlib import Path

# Configuration
API_URL = "http://localhost:8000"
USERNAME = "admin"
PASSWORD = "your_password"  # Change this

# Shapefile location
SHAPEFILE_DIR = r"D:\WGS Shapefiles\WGS Shapefiles"
SHAPEFILE_NAME = "Endarasha_Settlement_Scheme"

def create_zip():
    """Create ZIP file from shapefile components"""
    print("📦 Creating ZIP file...")
    
    shapefile_path = Path(SHAPEFILE_DIR)
    zip_path = shapefile_path / f"{SHAPEFILE_NAME}.zip"
    
    # Find all related files
    files_to_zip = []
    for ext in ['.shp', '.shx', '.dbf', '.prj', '.cpg', '.sbn', '.sbx']:
        file_path = shapefile_path / f"{SHAPEFILE_NAME}{ext}"
        if file_path.exists():
            files_to_zip.append(file_path)
    
    if not files_to_zip:
        print(f"❌ No shapefile files found at {SHAPEFILE_DIR}")
        return None
    
    # Create ZIP
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in files_to_zip:
            zipf.write(file_path, file_path.name)
            print(f"   Added: {file_path.name}")
    
    print(f"✅ ZIP created: {zip_path}")
    return str(zip_path)

def get_token():
    """Get JWT access token"""
    print("\n🔐 Logging in...")
    
    response = requests.post(
        f"{API_URL}/api/token/",
        json={"username": USERNAME, "password": PASSWORD}
    )
    
    if response.status_code == 200:
        token = response.json()['access']
        print("✅ Login successful!")
        return token
    else:
        print(f"❌ Login failed: {response.status_code}")
        print(response.text)
        return None

def upload_shapefile(token, zip_path):
    """Upload shapefile ZIP"""
    print("\n📤 Uploading shapefile...")
    
    headers = {"Authorization": f"Bearer {token}"}
    
    with open(zip_path, 'rb') as f:
        files = {'zip_file': (os.path.basename(zip_path), f, 'application/zip')}
        data = {
            'ref_field': 'PARCEL_NO',
            'status': 'active',
            'clear_existing': False,  # Set to True to replace all parcels
        }
        
        response = requests.post(
            f"{API_URL}/api/parcels/upload_shapefile/",
            headers=headers,
            files=files,
            data=data
        )
    
    if response.status_code == 200:
        result = response.json()
        
        print("\n" + "="*60)
        if result['success']:
            print("✅ UPLOAD SUCCESSFUL!")
            print("="*60)
            print(f"Message: {result['message']}")
            print(f"Imported: {result['imported_count']} parcels")
            print(f"Skipped: {result['skipped_count']} parcels")
            print(f"Errors: {result['error_count']}")
            
            if result.get('shapefile_info'):
                info = result['shapefile_info']
                print(f"\n📊 Shapefile Info:")
                print(f"   Layer: {info['layer_name']}")
                print(f"   Features: {info['feature_count']}")
                print(f"   Geometry: {info['geometry_type']}")
                print(f"   Fields: {', '.join(info['fields'])}")
            
            if result.get('errors'):
                print(f"\n⚠️ Errors (first 10):")
                for error in result['errors'][:10]:
                    print(f"   - {error}")
        else:
            print("❌ UPLOAD FAILED")
            print("="*60)
            print(f"Message: {result['message']}")
            if result.get('errors'):
                for error in result['errors']:
                    print(f"   - {error}")
        
        return result['success']
    else:
        print(f"\n❌ Upload failed: {response.status_code}")
        print(response.text)
        return False

def verify_import(token):
    """Verify parcels were imported"""
    print("\n🔍 Verifying import...")
    
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.get(
        f"{API_URL}/api/parcels/",
        headers=headers
    )
    
    if response.status_code == 200:
        data = response.json()
        count = data.get('count', 0)
        print(f"✅ Found {count} parcels in database")
        
        if count > 0:
            print("\nSample parcel:")
            parcel = data['results'][0]
            print(f"   Parcel Ref: {parcel.get('parcel_ref')}")
            print(f"   Owner: {parcel.get('owner_username')}")
            print(f"   Area: {parcel.get('area_m2')} m²")
            print(f"   Status: {parcel.get('status')}")
        
        return True
    else:
        print(f"❌ Verification failed: {response.status_code}")
        return False

def main():
    print("="*60)
    print("  Shapefile Upload Test Script")
    print("="*60)
    
    # Step 1: Create ZIP
    zip_path = create_zip()
    if not zip_path:
        return
    
    # Step 2: Get token
    token = get_token()
    if not token:
        return
    
    # Step 3: Upload
    success = upload_shapefile(token, zip_path)
    
    if success:
        # Step 4: Verify
        verify_import(token)
        
        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED!")
        print("="*60)
        print("\nNext steps:")
        print("1. Open http://localhost:8000/api/parcels/geojson/ to see GeoJSON")
        print("2. Open parcel_map_demo.html to visualize on map")
        print("3. Use shapefile_upload.html for future uploads")
    else:
        print("\n" + "="*60)
        print("❌ TESTS FAILED")
        print("="*60)
    
    # Cleanup
    if os.path.exists(zip_path):
        try:
            os.remove(zip_path)
            print(f"\n🗑️  Cleaned up: {zip_path}")
        except:
            pass

if __name__ == "__main__":
    main()
