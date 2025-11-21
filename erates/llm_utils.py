import requests
import json
from django.db.models import Sum, Q
from django.utils import timezone
from .models import User, Parcel, Payment

def build_system_context():
    """
    Aggregates data from the database to provide context for the LLM.
    Returns a string summary of Users, Parcels, and Defaulters.
    """
    # 1. Users Context
    total_users = User.objects.count()
    active_users = User.objects.filter(is_active=True).count()
    
    # Get a few recent users as examples
    recent_users = User.objects.order_by('-created_at')[:5]
    user_list_str = ", ".join([f"{u.username} ({u.role})" for u in recent_users])
    
    user_context = (
        f"USERS SUMMARY:\n"
        f"- Total Users: {total_users}\n"
        f"- Active Users: {active_users}\n"
        f"- Recent Users: {user_list_str}\n"
    )

    # 2. Parcels Context
    total_parcels = Parcel.objects.count()
    available_parcels = Parcel.objects.filter(owner_user__isnull=True).count()
    
    # Get some parcel owners
    parcels_with_owners = Parcel.objects.filter(owner_user__isnull=False).select_related('owner_user')[:5]
    parcel_owners_str = ", ".join([f"Parcel {p.parcel_ref} owned by {p.owner_user.username}" for p in parcels_with_owners])
    
    parcel_context = (
        f"PARCELS SUMMARY:\n"
        f"- Total Parcels: {total_parcels}\n"
        f"- Available (Unassigned): {available_parcels}\n"
        f"- Sample Ownership: {parcel_owners_str}\n"
    )

    # 3. Defaulters Context
    # Logic: Payment is past deadline and not completed
    now = timezone.now()
    defaulters_query = Payment.objects.filter(
        deadline__lt=now
    ).exclude(
        status__in=['completed', 'refunded']
    ).select_related('user')
    
    defaulter_count = defaulters_query.count()
    total_default_amount = defaulters_query.aggregate(Sum('amount'))['amount__sum'] or 0
    
    # List specific defaulters
    defaulter_list = []
    for p in defaulters_query[:10]:  # Limit to 10 for context window
        days_overdue = (now - p.deadline).days
        defaulter_list.append(
            f"- User: {p.user.username}, Amount: {p.amount} {p.currency}, Overdue by: {days_overdue} days"
        )
    
    defaulter_details = "\n".join(defaulter_list) if defaulter_list else "None"

    defaulter_context = (
        f"DEFAULTERS SUMMARY:\n"
        f"- Total Defaulters: {defaulter_count}\n"
        f"- Total Outstanding Amount: {total_default_amount}\n"
        f"- Top Defaulters:\n{defaulter_details}\n"
    )

    full_context = (
        f"SYSTEM DATA CONTEXT:\n"
        f"====================\n"
        f"{user_context}\n"
        f"{parcel_context}\n"
        f"{defaulter_context}\n"
        f"====================\n"
    )
    
    return full_context

def query_qwen(user_query, api_url):
    """
    Sends the user query + system context to the Qwen API on Colab.
    """
    if not api_url:
        return {"error": "No API URL provided. Please provide the 'api_url' from Colab."}

    context = build_system_context()
    full_prompt = (
        f"You are an intelligent assistant for the e-Rates system.\n"
        f"Here is the current system data:\n\n"
        f"{context}\n\n"
        f"User Query: {user_query}\n\n"
        f"Answer the user's query based on the system data provided above."
    )

    try:
        payload = {"text": full_prompt}
        response = requests.post(f"{api_url}/analyze", json=payload, timeout=60) # Increased timeout for LLM
        
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"LLM API Error: {response.status_code}", "details": response.text}
            
    except requests.exceptions.RequestException as e:
        return {"error": f"Connection failed: {str(e)}"}
