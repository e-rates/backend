from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .health import health
from .views import (
    UserViewSet,
    AccountViewSet,
    ParcelViewSet,
    ParcelHistoryViewSet,
    ParcelDeletionRequestViewSet,
    LedgerEntryViewSet,
    PaymentViewSet,
    AuditLogViewSet,
    CountyViewSet,
    ReportsViewSet,
    LLMQueryView,
    RateScheduleViewSet,
    ConversationViewSet,
    WaiverViewSet,
)

router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'accounts', AccountViewSet)
router.register(r'parcels', ParcelViewSet)
router.register(r'parcel-history', ParcelHistoryViewSet)
router.register(r'parcel-deletion-requests', ParcelDeletionRequestViewSet)
router.register(r'ledger-entries', LedgerEntryViewSet)
router.register(r'payments', PaymentViewSet)
router.register(r'audit-logs', AuditLogViewSet)
router.register(r'counties', CountyViewSet)
router.register(r'reports', ReportsViewSet, basename='reports')
router.register(r'rate-schedules', RateScheduleViewSet, basename='rate-schedules')
router.register(r'conversations', ConversationViewSet, basename='conversations')
router.register(r'waivers', WaiverViewSet, basename='waivers')

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
    path('health/', health, name='health'),
    path('llm/analyze/', LLMQueryView.as_view(), name='llm-analyze'),
]
