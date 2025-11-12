"""
Test script for the Parcel GeoJSON API endpoint.
Run this after importing shapefiles to verify the API works.

Usage:
    python test_parcel_api.py
"""

import requests
import json
from pprint import pprint

# Configuration
API_URL = "http://localhost:8000"
USERNAME = "admin"  # Change to your username
PASSWORD = "Admin123!"  # Change to your password

def get_access_token():
    """Get JWT access token"""
    print("🔐 Getting access token...")
    
    response = requests.post(
        f"{API_URL}/api/token/",
        json={
            "username": USERNAME,
            "password": PASSWORD
        }
    )
    
    if response.status_code == 200:
        data = response.json()
        print("✅ Authentication successful!")
        return data['access']
    else:
        print(f"❌ Authentication failed: {response.status_code}")
        print(response.text)
        return None

def test_geojson_endpoint(access_token):
    """Test the GeoJSON endpoint"""
    print("\n📍 Testing GeoJSON endpoint...")
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    # Test 1: Get all parcels
    print("\n1️⃣ Fetching all parcels...")
    response = requests.get(f"{API_URL}/api/parcels/geojson/", headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Success! Found {data['count']} parcels")
        
        if data['features']:
            print("\n📦 Sample feature:")
            sample = data['features'][0]
            print(f"   ID: {sample['id']}")
            print(f"   Parcel Ref: {sample['properties']['parcel_ref']}")
            print(f"   Owner: {sample['properties']['owner_username']}")
            print(f"   Area: {sample['properties']['area_m2']} m²")
            print(f"   Status: {sample['properties']['status']}")
            
            if sample['properties']['custom_props']:
                print(f"   Custom Properties: {list(sample['properties']['custom_props'].keys())}")
    else:
        print(f"❌ Failed: {response.status_code}")
        print(response.text)
        return False
    
    # Test 2: Filter by status
    print("\n2️⃣ Testing status filter (active only)...")
    response = requests.get(
        f"{API_URL}/api/parcels/geojson/?status=active",
        headers=headers
    )
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Found {data['count']} active parcels")
    else:
        print(f"❌ Failed: {response.status_code}")
    
    # Test 3: Test simplification
    print("\n3️⃣ Testing geometry simplification...")
    response = requests.get(
        f"{API_URL}/api/parcels/geojson/?simplify=0.0001",
        headers=headers
    )
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Simplified geometries for {data['count']} parcels")
    else:
        print(f"❌ Failed: {response.status_code}")
    
    # Test 4: List parcels (without geometry)
    print("\n4️⃣ Testing regular list endpoint (no geometry)...")
    response = requests.get(f"{API_URL}/api/parcels/", headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Found {data['count']} parcels (list view)")
    else:
        print(f"❌ Failed: {response.status_code}")
    
    return True

def save_sample_geojson(access_token):
    """Save a sample GeoJSON file for testing in frontend"""
    print("\n💾 Saving sample GeoJSON file...")
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    response = requests.get(
        f"{API_URL}/api/parcels/geojson/?simplify=0.0001",
        headers=headers
    )
    
    if response.status_code == 200:
        data = response.json()
        
        with open('sample_parcels.geojson', 'w') as f:
            json.dump(data, f, indent=2)
        
        print(f"✅ Saved {data['count']} parcels to 'sample_parcels.geojson'")
        print("   You can use this file to test your frontend without API calls")
    else:
        print(f"❌ Failed to save: {response.status_code}")

def main():
    print("=" * 60)
    print("  Parcel GeoJSON API Test Script")
    print("=" * 60)
    
    # Get access token
    access_token = get_access_token()
    if not access_token:
        print("\n❌ Cannot proceed without access token")
        return
    
    # Test endpoints
    success = test_geojson_endpoint(access_token)
    
    if success:
        # Save sample file
        save_sample_geojson(access_token)
        
        print("\n" + "=" * 60)
        print("✅ All tests completed successfully!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Check 'sample_parcels.geojson' to see the data structure")
        print("2. Use this GeoJSON in your Next.js Leaflet map")
        print("3. Implement filters and search in your frontend")
    else:
        print("\n❌ Some tests failed. Check the output above.")

if __name__ == "__main__":
    main()
