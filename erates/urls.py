from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    UserViewSet,
    AccountViewSet,
    ParcelViewSet,
    ParcelHistoryViewSet,
    LedgerEntryViewSet,
    PaymentViewSet,
    AuditLogViewSet,
)

router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'accounts', AccountViewSet)
router.register(r'parcels', ParcelViewSet)
router.register(r'parcel-history', ParcelHistoryViewSet)
router.register(r'ledger-entries', LedgerEntryViewSet)
router.register(r'payments', PaymentViewSet)
router.register(r'audit-logs', AuditLogViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
