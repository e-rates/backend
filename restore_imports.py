import os

file_path = 'erates/views.py'

imports_block = [
    'from rest_framework import viewsets, permissions, status, filters\n',
    'from rest_framework.views import APIView\n',
    'from rest_framework.decorators import action\n',
    'from rest_framework.response import Response\n',
    'from django_filters.rest_framework import DjangoFilterBackend\n',
    'from django.db.models import Sum, Count, Avg, Q, F, Max, Min\n',
    'from django.db.models.functions import TruncDate, TruncMonth, TruncWeek\n'
]

with open(file_path, 'r') as f:
    lines = f.readlines()

# Check if imports are missing
if 'from rest_framework import viewsets' not in ''.join(lines[:20]):
    # Insert at the top
    lines = imports_block + lines
    print("Inserted imports.")
else:
    print("Imports already present.")

with open(file_path, 'w') as f:
    f.writelines(lines)
