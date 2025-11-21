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
    ReportsViewSet,
    LLMQueryView,
)

router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'accounts', AccountViewSet)
router.register(r'parcels', ParcelViewSet)
router.register(r'parcel-history', ParcelHistoryViewSet)
router.register(r'ledger-entries', LedgerEntryViewSet)
router.register(r'payments', PaymentViewSet)
router.register(r'audit-logs', AuditLogViewSet)
router.register(r'reports', ReportsViewSet, basename='reports')

# Admin router (same viewsets, just different URL prefix for frontend compatibility)
admin_router = DefaultRouter()
admin_router.register(r'parcels', ParcelViewSet)
admin_router.register(r'users', UserViewSet)
admin_router.register(r'accounts', AccountViewSet)
admin_router.register(r'payments', PaymentViewSet)
admin_router.register(r'reports', ReportsViewSet, basename='admin-reports')

urlpatterns = [
    path('', include(router.urls)),
    path('admin/', include(admin_router.urls)),  # Add /admin/ prefix for frontend
    path('llm/analyze/', LLMQueryView.as_view(), name='llm-analyze'),
]
