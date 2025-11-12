"""
Test script for the defaulters endpoint
Run this after creating test data to verify the endpoint works correctly
"""
import requests
from datetime import datetime, timedelta

# Configuration - update these values for your setup
BASE_URL = "http://localhost:8000/api"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

def get_auth_token(username, password):
    """Get authentication token"""
    # This assumes you have a token authentication endpoint
    # Adjust based on your authentication setup
    response = requests.post(
        f"{BASE_URL}/auth/login/",
        json={"username": username, "password": password}
    )
    if response.status_code == 200:
        return response.json().get('token')
    else:
        print(f"Authentication failed: {response.status_code}")
        print(response.text)
        return None

def test_defaulters_endpoint():
    """Test the defaulters endpoint"""
    print("=" * 60)
    print("TESTING DEFAULTERS ENDPOINT")
    print("=" * 60)
    
    # Get auth token (adjust based on your auth system)
    # For now, assuming you'll provide the token manually
    token = input("Enter admin auth token (or press Enter to skip auth): ").strip()
    
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    
    # Test 1: Get all defaulters
    print("\n1. Testing: Get all defaulters")
    print("-" * 60)
    response = requests.get(f"{BASE_URL}/payments/defaulters/", headers=headers)
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Total Defaulters: {data.get('total_defaulters', 0)}")
        print(f"Total Overdue Amount: {data.get('total_overdue_amount', 0)}")
        print(f"Average Days Overdue: {data.get('average_days_overdue', 0)}")
        
        if data.get('defaulters'):
            print(f"\nFirst 3 defaulters:")
            for i, defaulter in enumerate(data['defaulters'][:3], 1):
                print(f"  {i}. {defaulter['username']}: "
                      f"{defaulter['currency']} {defaulter['amount']} - "
                      f"{defaulter['days_overdue']} days overdue")
        else:
            print("No defaulters found (this is good!)")
            
        print(f"\nCurrency Breakdown:")
        for currency, info in data.get('defaulters_by_currency', {}).items():
            print(f"  {currency}: {info['count']} defaulters, "
                  f"Total: {info['total_amount']}")
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
    
    # Test 2: Filter by minimum days overdue
    print("\n2. Testing: Filter by minimum days overdue (30 days)")
    print("-" * 60)
    response = requests.get(
        f"{BASE_URL}/payments/defaulters/?min_days_overdue=30",
        headers=headers
    )
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Defaulters with 30+ days overdue: {data.get('total_defaulters', 0)}")
    else:
        print(f"Error: {response.status_code}")
    
    # Test 3: Filter by currency
    print("\n3. Testing: Filter by currency (KES)")
    print("-" * 60)
    response = requests.get(
        f"{BASE_URL}/payments/defaulters/?currency=KES",
        headers=headers
    )
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"KES Defaulters: {data.get('total_defaulters', 0)}")
        print(f"Total KES Overdue: {data.get('total_overdue_amount', 0)}")
    else:
        print(f"Error: {response.status_code}")
    
    # Test 4: Filter by minimum amount
    print("\n4. Testing: Filter by minimum amount (5000)")
    print("-" * 60)
    response = requests.get(
        f"{BASE_URL}/payments/defaulters/?min_amount=5000",
        headers=headers
    )
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Defaulters with amount >= 5000: {data.get('total_defaulters', 0)}")
    else:
        print(f"Error: {response.status_code}")
    
    # Test 5: Combined filters
    print("\n5. Testing: Combined filters (30+ days, KES, 5000+ amount)")
    print("-" * 60)
    response = requests.get(
        f"{BASE_URL}/payments/defaulters/?min_days_overdue=30&currency=KES&min_amount=5000",
        headers=headers
    )
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Matching Defaulters: {data.get('total_defaulters', 0)}")
    else:
        print(f"Error: {response.status_code}")
    
    print("\n" + "=" * 60)
    print("TESTING COMPLETE")
    print("=" * 60)

def create_test_payment_data():
    """
    Helper function to create test payment data
    NOTE: This requires admin access and proper authentication
    """
    print("\n" + "=" * 60)
    print("CREATE TEST DATA")
    print("=" * 60)
    print("\nTo create test data with overdue payments, use Django shell:")
    print("\npython manage.py shell")
    print("\nThen run:")
    print("""
from erates.models import Payment, User, Account
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import uuid

# Get or create a test user and account
user = User.objects.filter(role='user').first()
account = Account.objects.filter(owner_user=user).first()

if user and account:
    # Create a payment with deadline 10 days ago
    Payment.objects.create(
        user=user,
        account=account,
        amount=Decimal('5000.00'),
        currency='KES',
        deadline=timezone.now() - timedelta(days=10),
        status='pending',
        idempotency_key=str(uuid.uuid4()),
        metadata={'test': True, 'description': 'Test overdue payment'}
    )
    
    # Create another payment with deadline 30 days ago
    Payment.objects.create(
        user=user,
        account=account,
        amount=Decimal('7500.00'),
        currency='KES',
        deadline=timezone.now() - timedelta(days=30),
        status='pending',
        idempotency_key=str(uuid.uuid4()),
        metadata={'test': True, 'description': 'Test very overdue payment'}
    )
    
    print("Test payments created successfully!")
else:
    print("Error: Could not find user and account for testing")
    """)

if __name__ == "__main__":
    import sys
    
    print("Defaulters Endpoint Test Suite")
    print("=" * 60)
    print("\nOptions:")
    print("1. Run tests")
    print("2. Show test data creation instructions")
    print("3. Exit")
    
    choice = input("\nEnter your choice (1-3): ").strip()
    
    if choice == "1":
        test_defaulters_endpoint()
    elif choice == "2":
        create_test_payment_data()
    else:
        print("Exiting...")
        sys.exit(0)
