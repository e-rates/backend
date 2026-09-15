from rest_framework import viewsets, permissions, status, filters, mixins
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Sum, Count, Avg, Q, F, OuterRef, Subquery, DecimalField
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import re
import secrets
from django.conf import settings
from django.http import StreamingHttpResponse
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiResponse,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers as drf_serializers

from .models import (
    User,
    Account,
    County,
    Parcel,
    ParcelDeletionRequest,
    ParcelHistory,
    LedgerEntry,
    Payment,
    AuditLog,
)
from .serializers import (
    CountySerializer,
    
    UserListSerializer,
    UserDetailSerializer,
    StaffCreatedUserSerializer,
    UserUpdateSerializer,
    
    AccountSerializer,
    AccountListSerializer,
   
    ParcelSerializer,
    ParcelListSerializer,
    
    ParcelHistorySerializer,
    ParcelDeletionRequestSerializer,
    ParcelDeletionRequestCreateSerializer,
    ParcelDeletionDecisionSerializer,

    LedgerEntrySerializer,
    PaymentSerializer,
    PaymentCreateSerializer,
   
    AuditLogSerializer,
    
    UserReportSerializer,
    AccountReportSerializer,
    PaymentReportSerializer,
    ParcelReportSerializer,
    LedgerReportSerializer,
    ComprehensiveReportSerializer,
    TransactionVolumeSerializer,
    TopUserSerializer,
    DefaulterSerializer,
    LLMQuerySerializer,
)
from . import audit, mpesa, parcel_deletion, payment_flow, rate_reports
from .rates import annual_rate, rate_explanation
import hmac

from .shapefile_serializers import (
    ShapefileUploadSerializer,
    ShapefileImportResultSerializer,
)
from .shapefile_utils import ShapefileImporter



class IsOwnerOrAdminOrReadOnly(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        # Read permissions for authenticated users
        if request.method in permissions.SAFE_METHODS:
            return request.user.is_authenticated
        
        if not hasattr(request.user, 'role'):
            return False
        
        if request.user.role in ('admin', 'owner'):
            return True

        if hasattr(obj, 'owner_user'):
            return obj.owner_user == request.user
        if hasattr(obj, 'user'):
            return obj.user == request.user
        
        return False


class IsPlatformOwner(permissions.BasePermission):
    """Only the people running the platform, above all counties."""
    message = 'Only the platform owner can do this.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'owner')


class IsStaff(permissions.BasePermission):
    """Platform owners and county officials."""
    message = 'Only county officials can do this.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role in ('owner', 'admin'))


def scope_county(request):
    """The county a request is limited to, or None for platform owners."""
    user = request.user
    if not user.is_authenticated or user.role == 'owner':
        return None
    return user.county or None


def normalize_county(val):
    if not val:
        return ''
    import re
    return re.sub(r'\s+(city\s+)?county$', '', str(val).strip(), flags=re.I).lower()


def county_q(field_name: str, county_name):
    """Build a Q object matching county with or without 'County' suffix, case-insensitively."""
    from django.db.models import Q
    if not county_name:
        return Q()
    base = re.sub(r'\s+(city\s+)?county$', '', str(county_name).strip(), flags=re.I)
    return (
        Q(**{f'{field_name}__iexact': base}) |
        Q(**{f'{field_name}__iexact': f'{base} County'}) |
        Q(**{f'{field_name}__iexact': f'{base} City County'})
    )


class IsAdminOrAuditor(permissions.BasePermission):
    """
    Only admins, auditors, and platform owners/superusers can access.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and (
                request.user.is_superuser or
                getattr(request.user, 'role', None) in ('admin', 'auditor', 'owner')
            )
        )


class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and (
                request.user.is_superuser or
                getattr(request.user, 'role', None) in ('admin', 'owner')
            )
        )


def user_ids_matching_phone(search):
    needle = re.sub(r'[\s\-\(\)]', '', search)
    if not needle:
        return []
    if len(re.sub(r'\D', '', needle)) >= 9:
        exact = User.find_by_phone(needle)
        if exact:
            return [exact.user_id]
    return [
        u.user_id
        for u in User.objects.filter(is_deleted=False, phone__isnull=False).only('user_id', 'phone')
        if u.phone and needle in re.sub(r'[\s\-\(\)]', '', u.phone)
    ]


class IsOwnerOrAdmin(permissions.BasePermission):
    """
    Owner or admin only.
    """
    def has_object_permission(self, request, view, obj):
        if not hasattr(request.user, 'role'):
            return False
        
        if request.user.role in ('admin', 'owner'):
            return True
        
        if hasattr(obj, 'owner_user'):
            return obj.owner_user == request.user
        if hasattr(obj, 'user'):
            return obj.user == request.user
        
        return request.user == obj


@extend_schema_view(
    list=extend_schema(
        summary="List all users",
        description="Get a paginated list of all users. Supports filtering by verification status, active status, and role.",
        tags=['Users'],
    ),
    retrieve=extend_schema(
        summary="Get user details",
        description="Retrieve detailed information about a specific user.",
        tags=['Users'],
    ),
    create=extend_schema(
        summary="Create new user",
        description="Register a new user account. This endpoint is publicly accessible.",
        tags=['Users'],
    ),
    update=extend_schema(
        summary="Update user",
        description="Update user information. Only the user themselves or an admin can update.",
        tags=['Users'],
    ),
    partial_update=extend_schema(
        summary="Partially update user",
        description="Partially update user information. Only the user themselves or an admin can update.",
        tags=['Users'],
    ),
    destroy=extend_schema(
        summary="Delete user",
        description="Delete a user account. Only the user themselves or an admin can delete.",
        tags=['Users'],
    ),
)
class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user accounts with role-based access control.
    """
    queryset = User.objects.filter(is_deleted=False).order_by('-created_at')
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_verified', 'is_active', 'role']
    search_fields = ['username', 'email']
    ordering_fields = ['created_at', 'updated_at', 'username']
    
    def get_serializer_class(self):
        """Select appropriate serializer based on action"""
        if self.action == 'list':
            staff = getattr(self.request.user, 'role', None) in ('admin', 'auditor', 'owner')
            return UserDetailSerializer if staff else UserListSerializer
        elif self.action == 'create':
            return StaffCreatedUserSerializer
        elif self.action in ['update', 'partial_update']:
            return UserUpdateSerializer
        else:  
            return UserDetailSerializer
    
    def get_permissions(self):
        """Dynamic permissions based on action"""
        if self.action in ('create', 'reset_password'):
            permission_classes = [permissions.IsAuthenticated, IsStaff]
        elif self.action in ['update', 'partial_update', 'destroy']:
            permission_classes = [permissions.IsAuthenticated, IsOwnerOrAdmin]
        else:
            permission_classes = [permissions.IsAuthenticated]
        
        return [permission() for permission in permission_classes]
    
    def get_queryset(self):
        queryset = self.queryset
        county = scope_county(self.request)
        return queryset.filter(county__iexact=county) if county else queryset

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active'])
        instance.soft_delete()
        audit.record('auth.account_deactivated', obj=instance, username=instance.username, role=instance.role)

    def _issue_password(self, user):
        password = secrets.token_urlsafe(9)
        user.set_password(password)
        user.must_change_password = True
        user.save()
        return password

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        password = self._issue_password(user)
        audit.record('auth.account_created', obj=user, username=user.username, role=user.role, county=user.county)
        return Response(
            {**UserDetailSerializer(user, context={'request': request}).data, 'temporary_password': password},
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Issue a new one-time password",
        description="Staff only. Returns a temporary password the account must change at next sign-in.",
        tags=['Users'],
        responses={200: OpenApiResponse(description='{temporary_password}')},
    )
    @action(detail=True, methods=['post'])
    def reset_password(self, request, pk=None):
        user = self.get_object()
        if user.role != 'user' and not request.user.is_platform_owner:
            return Response({'error': "Only the platform owner can reset an official's password"},
                            status=status.HTTP_403_FORBIDDEN)
        password = self._issue_password(user)
        audit.record('auth.password_reset_by_staff', obj=user, username=user.username)
        return Response({'username': user.username, 'temporary_password': password})


    def get_serializer_context(self):
        """Pass request context to serializer for conditional field visibility"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    @extend_schema(
        summary="Get current user profile",
        description="Retrieve the profile information of the currently authenticated user.",
        tags=['Users'],
        responses={200: UserDetailSerializer},
    )
    @action(detail=False, methods=['get', 'patch'], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        """Get or update the current authenticated user's profile"""
        if request.method == 'PATCH':
            data = {k: v for k, v in request.data.items() if k in ('email', 'phone')}
            serializer = UserUpdateSerializer(request.user, data=data, partial=True, context={'request': request})
            serializer.is_valid(raise_exception=True)
            changed = [k for k, val in serializer.validated_data.items() if getattr(request.user, k) != val]
            serializer.save()
            if changed:
                audit.record('auth.profile_updated', obj=request.user, fields=changed)
        return Response(UserDetailSerializer(request.user, context={'request': request}).data)
    
    @extend_schema(
        summary="Change user password",
        description="Change the password for the currently authenticated user.",
        tags=['Users'],
        request=UserUpdateSerializer,
        responses={
            200: OpenApiResponse(description="Password changed successfully"),
            400: OpenApiResponse(description="Invalid data provided"),
        },
    )
    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def change_password(self, request):
        """Change password endpoint"""
        serializer = UserUpdateSerializer(
            request.user,
            data=request.data,
            partial=True,
            context={'request': request}
        )
        if serializer.is_valid():
            serializer.save()
            if 'new_password' in serializer.validated_data:
                audit.record('auth.password_changed', obj=request.user)
            return Response({'message': 'Password changed successfully'})
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



@extend_schema_view(
    list=extend_schema(
        summary="List accounts",
        description="Get a paginated list of accounts. Regular users see only their accounts; admins see all.",
        tags=['Accounts'],
    ),
    retrieve=extend_schema(
        summary="Get account details",
        description="Retrieve detailed information about a specific account.",
        tags=['Accounts'],
    ),
    create=extend_schema(
        summary="Create new account",
        description="Create a new financial account for the authenticated user.",
        tags=['Accounts'],
    ),
    update=extend_schema(
        summary="Update account",
        description="Update account information. Only the owner or admin can update.",
        tags=['Accounts'],
    ),
    partial_update=extend_schema(
        summary="Partially update account",
        description="Partially update account information.",
        tags=['Accounts'],
    ),
    destroy=extend_schema(
        summary="Delete account",
        description="Delete an account. Only the owner or admin can delete.",
        tags=['Accounts'],
    ),
)
class AccountViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing financial accounts with balance tracking.
    """
    queryset = Account.objects.select_related('owner_user').all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['account_type', 'currency', 'status', 'owner_user']
    search_fields = ['owner_user__username']
    ordering_fields = ['created_at', 'current_balance']
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrAdminOrReadOnly]
    
    def get_serializer_class(self):
        """Select appropriate serializer"""
        if self.action == 'list':
            return AccountListSerializer
        return AccountSerializer
    
    def get_queryset(self):
        """Filter accounts based on user role"""
        user = self.request.user
        user_role = getattr(user, 'role', 'user')
        
        # Admins and auditors see all
        if user_role in ['admin', 'auditor']:
            return self.queryset
        
        # Regular users see only their accounts
        return self.queryset.filter(owner_user=user)
    
    def get_serializer_context(self):
        """Pass request context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def perform_create(self, serializer):
        """Auto-assign owner to current user"""
        serializer.save(owner_user=self.request.user)
    
    @extend_schema(
        summary="Get account balance",
        description="Get the current balance of an account with verification status.",
        tags=['Accounts'],
        responses={200: OpenApiResponse(description="Account balance with verification status")},
    )
    @action(detail=True, methods=['get'])
    def balance(self, request, pk=None):
        """Get verified balance"""
        account = self.get_object()
        is_verified = account.verify_balance_integrity()
        return Response({
            'account_id': account.account_id,
            'current_balance': account.current_balance,
            'currency': account.currency,
            'balance_verified': is_verified,
            'last_updated': account.updated_at
        })
    
    @extend_schema(
        summary="Get account transactions",
        description="Retrieve all ledger entries (transactions) for a specific account.",
        tags=['Accounts'],
        responses={200: LedgerEntrySerializer(many=True)},
    )
    @action(detail=True, methods=['get'])
    def transactions(self, request, pk=None):
        """Get account transactions"""
        account = self.get_object()
        entries = LedgerEntry.objects.filter(account=account).order_by('-created_at')
        serializer = LedgerEntrySerializer(entries, many=True, context={'request': request})
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        summary="List parcels",
        description="Get a paginated list of land parcels with GIS data. List view excludes geometry for performance.",
        tags=['Parcels'],
        responses={200: ParcelListSerializer(many=True)},
    ),
    retrieve=extend_schema(
        summary="Get parcel details",
        description="Retrieve detailed information about a specific parcel including full GeoJSON geometry data.",
        tags=['Parcels'],
        responses={
            200: inline_serializer(
                name='ParcelGeoJSON',
                fields={
                    'type': drf_serializers.CharField(default='Feature'),
                    'id': drf_serializers.UUIDField(),
                    'geometry': drf_serializers.JSONField(help_text='GeoJSON geometry object'),
                    'properties': drf_serializers.JSONField(help_text='Parcel properties'),
                }
            )
        },
    ),
    create=extend_schema(
        summary="Create new parcel",
        description="Create a new land parcel with GIS geometry data in GeoJSON format.",
        tags=['Parcels'],
        request=inline_serializer(
            name='ParcelCreate',
            fields={
                'owner_user': drf_serializers.UUIDField(),
                'parcel_ref': drf_serializers.CharField(max_length=100),
                'geom': drf_serializers.JSONField(help_text='GeoJSON geometry'),
                'status': drf_serializers.ChoiceField(choices=['active', 'disputed', 'transferred', 'archived']),
                'props': drf_serializers.JSONField(required=False),
            }
        ),
        responses={
            201: inline_serializer(
                name='ParcelCreateResponse',
                fields={
                    'type': drf_serializers.CharField(default='Feature'),
                    'id': drf_serializers.UUIDField(),
                    'geometry': drf_serializers.JSONField(help_text='GeoJSON geometry object'),
                    'properties': drf_serializers.JSONField(help_text='Parcel properties'),
                }
            )
        },
    ),
    update=extend_schema(
        summary="Update parcel",
        description="Update parcel information. Only the owner or admin can update.",
        tags=['Parcels'],
        request=inline_serializer(
            name='ParcelUpdate',
            fields={
                'owner_user': drf_serializers.UUIDField(required=False),
                'parcel_ref': drf_serializers.CharField(max_length=100, required=False),
                'geom': drf_serializers.JSONField(help_text='GeoJSON geometry', required=False),
                'status': drf_serializers.ChoiceField(choices=['active', 'disputed', 'transferred', 'archived'], required=False),
                'props': drf_serializers.JSONField(required=False),
            }
        ),
        responses={
            200: inline_serializer(
                name='ParcelUpdateResponse',
                fields={
                    'type': drf_serializers.CharField(default='Feature'),
                    'id': drf_serializers.UUIDField(),
                    'geometry': drf_serializers.JSONField(help_text='GeoJSON geometry object'),
                    'properties': drf_serializers.JSONField(help_text='Parcel properties'),
                }
            )
        },
    ),
    partial_update=extend_schema(
        summary="Partially update parcel",
        description="Partially update parcel information.",
        tags=['Parcels'],
        request=inline_serializer(
            name='ParcelPartialUpdate',
            fields={
                'owner_user': drf_serializers.UUIDField(required=False),
                'parcel_ref': drf_serializers.CharField(max_length=100, required=False),
                'geom': drf_serializers.JSONField(help_text='GeoJSON geometry', required=False),
                'status': drf_serializers.ChoiceField(choices=['active', 'disputed', 'transferred', 'archived'], required=False),
                'props': drf_serializers.JSONField(required=False),
            }
        ),
        responses={
            200: inline_serializer(
                name='ParcelPartialUpdateResponse',
                fields={
                    'type': drf_serializers.CharField(default='Feature'),
                    'id': drf_serializers.UUIDField(),
                    'geometry': drf_serializers.JSONField(help_text='GeoJSON geometry object'),
                    'properties': drf_serializers.JSONField(help_text='Parcel properties'),
                }
            )
        },
    ),
    destroy=extend_schema(
        summary="Delete parcel",
        description="Delete a parcel. Only the owner or admin can delete.",
        tags=['Parcels'],
    ),
)
class ParcelViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing land parcels with GIS functionality.
    """
    queryset = Parcel.objects.select_related('owner_user').order_by('parcel_ref')
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'owner_user']
    search_fields = ['parcel_ref', 'owner_user__username']
    ordering_fields = ['created_at', 'updated_at', 'area_m2']
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrAdminOrReadOnly]
    
    def get_serializer_class(self):
        """Select serializer - list returns no geometry"""
        if self.action == 'list':
            return ParcelListSerializer
        return ParcelSerializer
    
    def get_queryset(self):
        """Parcels are limited to the signed-in user's county; platform owners see every county."""
        county = scope_county(self.request)
        return self.queryset.filter(county_q('county', county)) if county else self.queryset
    
    def get_serializer_context(self):
        """Pass request context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsAdmin()]
        return super().get_permissions()

    def perform_create(self, serializer):
        """Create parcel; owner comes from the payload and may be empty"""
        serializer.save()
        
    
    def perform_update(self, serializer):
        """Update parcel and create history record"""
        serializer.save()

    @extend_schema(
        summary="Delete a parcel that was never allocated or billed",
        description=(
            "County officials may remove a plot only while it is clean — never allocated, never "
            "billed. Anything else returns 409 and must go through request-deletion."
        ),
        tags=['Parcels'],
        responses={
            204: OpenApiResponse(description="Parcel removed"),
            409: OpenApiResponse(description="Parcel is allocated, billed or paid; escalate instead"),
        },
    )
    def destroy(self, request, *args, **kwargs):
        parcel = self.get_object()
        tier, explanation = parcel_deletion.classify(parcel)
        if tier != parcel_deletion.CLEAN:
            return Response(
                {
                    'error': explanation,
                    'tier': tier,
                    'can_request': tier == parcel_deletion.ESCALATE,
                },
                status=status.HTTP_409_CONFLICT,
            )
        ref = parcel.parcel_ref
        parcel.soft_delete()
        audit.record('parcel.deleted', obj=parcel, parcel_ref=ref, reason='unallocated and unbilled')
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Ask the platform owner to delete a parcel",
        description="For plots that are allocated or billed but not paid. Creates a request for review.",
        tags=['Parcels'],
        request=ParcelDeletionRequestCreateSerializer,
        responses={
            201: ParcelDeletionRequestSerializer,
            409: OpenApiResponse(description="Parcel is deletable directly, paid, or already has an open request"),
        },
    )
    @action(detail=True, methods=['post'], url_path='request-deletion', permission_classes=[IsAdmin])
    def request_deletion(self, request, pk=None):
        parcel = self.get_object()
        tier, explanation = parcel_deletion.classify(parcel)

        if tier == parcel_deletion.CLEAN:
            return Response(
                {'error': 'This plot can be deleted directly; no approval needed.'},
                status=status.HTTP_409_CONFLICT,
            )
        if tier == parcel_deletion.PROTECTED:
            return Response({'error': explanation}, status=status.HTTP_409_CONFLICT)

        if ParcelDeletionRequest.objects.filter(
            parcel=parcel, status='pending', is_deleted=False
        ).exists():
            return Response(
                {'error': 'A deletion request for this plot is already awaiting review.'},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = ParcelDeletionRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deletion_request = ParcelDeletionRequest.objects.create(
            parcel=parcel,
            requested_by=request.user,
            reason=serializer.validated_data['reason'],
        )
        audit.record(
            'parcel.deletion_requested', obj=parcel,
            parcel_ref=parcel.parcel_ref, reason=deletion_request.reason, why_escalated=explanation,
        )
        return Response(
            ParcelDeletionRequestSerializer(deletion_request, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Get parcel history",
        description="Retrieve the complete change history for a specific parcel.",
        tags=['Parcels'],
        responses={200: ParcelHistorySerializer(many=True)},
    )
    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        """Get parcel change history"""
        parcel = self.get_object()
        history = ParcelHistory.objects.filter(parcel=parcel).order_by('-change_ts')
        serializer = ParcelHistorySerializer(history, many=True, context={'request': request})
        return Response(serializer.data)
    
    @extend_schema(
        summary="Assign or transfer parcel ownership",
        description="Assign an owner to an unassigned parcel or transfer ownership to another user. Creates a history record.",
        tags=['Parcels'],
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'new_owner_id': {'type': 'string', 'format': 'uuid', 'description': 'UUID of the new owner'}
                },
                'required': ['new_owner_id']
            }
        },
        responses={
            200: OpenApiResponse(description="Parcel ownership assigned/transferred successfully"),
            400: OpenApiResponse(description="Invalid request data"),
            404: OpenApiResponse(description="New owner not found"),
        },
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def assign_owner(self, request, pk=None):
        """Assign owner to unassigned parcel or transfer ownership"""
        parcel = self.get_object()
        new_owner_id = request.data.get('new_owner_id')
        
        if not new_owner_id:
            return Response(
                {'error': 'new_owner_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            new_owner = User.objects.get(user_id=new_owner_id)
            old_owner = parcel.owner_user
            
            parcel.owner_user = new_owner
            # Only set to 'transferred' if there was a previous owner
            if old_owner:
                parcel.status = 'transferred'
            else:
                parcel.status = 'active'
            parcel.save()
            
            # Create history record
            ParcelHistory.objects.create(
                parcel=parcel,
                owner_user=new_owner,
                geom=parcel.geom,
                area_m2=parcel.area_m2,
                changed_by=request.user,
                change_reason=f'Ownership {"transferred to" if old_owner else "assigned to"} {new_owner.username}'
            )
            audit.record(
                'parcel.transferred' if old_owner else 'parcel.assigned', obj=parcel,
                parcel_ref=parcel.parcel_ref, new_owner=new_owner.username,
                previous_owner=old_owner.username if old_owner else None,
            )
            
            return Response({
                'message': f'Parcel {"transferred" if old_owner else "assigned"} successfully',
                'parcel_id': parcel.parcel_id,
                'parcel_ref': parcel.parcel_ref,
                'previous_owner': old_owner.username if old_owner else None,
                'new_owner': new_owner.username
            })
        except User.DoesNotExist:
            return Response(
                {'error': 'New owner not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @extend_schema(
        summary="Bulk assign owners to parcels",
        description="Assign owners to multiple parcels at once. Useful after bulk upload.",
        tags=['Parcels'],
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'assignments': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'parcel_id': {'type': 'string', 'format': 'uuid'},
                                'owner_id': {'type': 'string', 'format': 'uuid'}
                            }
                        }
                    }
                },
                'required': ['assignments']
            }
        },
        responses={
            200: OpenApiResponse(description="Bulk assignment completed"),
            400: OpenApiResponse(description="Invalid request data"),
        },
    )
    @action(detail=False, methods=['post'], permission_classes=[IsAdmin])
    def bulk_assign_owners(self, request):
        """Bulk assign owners to multiple parcels"""
        assignments = request.data.get('assignments', [])
        
        if not assignments:
            return Response(
                {'error': 'assignments array is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        success_count = 0
        failed_count = 0
        errors = []
        
        for assignment in assignments:
            parcel_id = assignment.get('parcel_id')
            owner_id = assignment.get('owner_id')
            
            if not parcel_id or not owner_id:
                failed_count += 1
                errors.append({'parcel_id': parcel_id, 'error': 'Missing parcel_id or owner_id'})
                continue
            
            try:
                parcel = Parcel.objects.get(parcel_id=parcel_id)
                owner = User.objects.get(user_id=owner_id)
                
                old_owner = parcel.owner_user
                parcel.owner_user = owner
                if old_owner:
                    parcel.status = 'transferred'
                else:
                    parcel.status = 'active'
                parcel.save()
                
                # Create history
                ParcelHistory.objects.create(
                    parcel=parcel,
                    owner_user=owner,
                    geom=parcel.geom,
                    area_m2=parcel.area_m2,
                    changed_by=request.user,
                    change_reason=f'Bulk assignment to {owner.username}'
                )
                audit.record(
                    'parcel.transferred' if old_owner else 'parcel.assigned', obj=parcel, bulk=True,
                    parcel_ref=parcel.parcel_ref, new_owner=owner.username,
                    previous_owner=old_owner.username if old_owner else None,
                )
                
                success_count += 1
                
            except Parcel.DoesNotExist:
                failed_count += 1
                errors.append({'parcel_id': parcel_id, 'error': 'Parcel not found'})
            except User.DoesNotExist:
                failed_count += 1
                errors.append({'parcel_id': parcel_id, 'error': 'Owner not found'})
            except Exception as e:
                failed_count += 1
                errors.append({'parcel_id': parcel_id, 'error': str(e)})
        
        return Response({
            'success': failed_count == 0,
            'message': f'Assigned {success_count} parcels, {failed_count} failed',
            'success_count': success_count,
            'failed_count': failed_count,
            'errors': errors[:20]  # Limit errors shown
        })
    
    @extend_schema(
        summary="Get available parcels for allocation",
        description="Retrieve list of unassigned parcels with their parcel numbers. Used for parcel allocation UI.",
        tags=['Parcels'],
        parameters=[
            OpenApiParameter(name='county', type=str, description='Filter by county'),
            OpenApiParameter(name='sub_county', type=str, description='Filter by sub-county'),
            OpenApiParameter(name='ward', type=str, description='Filter by ward'),
            OpenApiParameter(name='search', type=str, description='Search by parcel reference'),
        ],
        responses={
            200: inline_serializer(
                name='AvailableParcelsResponse',
                fields={
                    'parcels': drf_serializers.ListField(
                        child=drf_serializers.DictField()
                    )
                }
            )
        },
    )
    @action(detail=False, methods=['get'], permission_classes=[IsAdminOrAuditor])
    def available_for_allocation(self, request):
        """Get list of parcels available for allocation (unassigned)"""
        parcels = Parcel.objects.filter(owner_user__isnull=True, is_deleted=False)
        allocation_scope = scope_county(request)
        if allocation_scope:
            parcels = parcels.filter(county_q('county', allocation_scope))
        
        # Apply filters
        county = request.query_params.get('county')
        if county:
            parcels = parcels.filter(county=county)
        
        sub_county = request.query_params.get('sub_county')
        if sub_county:
            parcels = parcels.filter(sub_county=sub_county)
        
        ward = request.query_params.get('ward')
        if ward:
            parcels = parcels.filter(ward=ward)
        
        search = request.query_params.get('search')
        if search:
            parcels = parcels.filter(parcel_ref__icontains=search)
        
        # Limit to 100 results for performance
        parcels = parcels.order_by('parcel_ref')[:100]
        
        parcel_list = [
            {
                'parcel_id': str(p.parcel_id),
                'parcel_ref': p.parcel_ref,
                'county': p.county,
                'sub_county': p.sub_county,
                'ward': p.ward,
                'area_m2': p.area_m2,
            }
            for p in parcels
        ]
        
        return Response({'parcels': parcel_list})
    
    @extend_schema(
        summary="Get available users for parcel allocation",
        description="Retrieve list of active users that can be assigned parcels.",
        tags=['Parcels'],
        parameters=[
            OpenApiParameter(name='search', type=str, description='Search by username, email, or phone'),
        ],
        responses={
            200: inline_serializer(
                name='AvailableUsersResponse',
                fields={
                    'users': drf_serializers.ListField(
                        child=drf_serializers.DictField()
                    )
                }
            )
        },
    )
    @action(detail=False, methods=['get'], permission_classes=[IsAdminOrAuditor])
    def available_users(self, request):
        """Get list of users available for parcel allocation"""
        users = User.objects.filter(is_active=True, is_deleted=False, role='user')
        allocation_scope = scope_county(request)
        if allocation_scope:
            users = users.filter(county_q('county', allocation_scope))
        
        search = request.query_params.get('search')
        if search:
            users = users.filter(
                Q(username__icontains=search) |
                Q(email__icontains=search) |
                Q(user_id__in=user_ids_matching_phone(search))
            )
        
        # Limit to 50 results for performance
        users = users.order_by('username')[:50]
        
        user_list = [
            {
                'user_id': str(u.user_id),
                'username': u.username,
                'email': u.email,
                'phone': u.phone,
                'national_id': u.national_id,
            }
            for u in users
        ]
        
        return Response({'users': user_list})
    
    @extend_schema(
        summary="Allocate parcel to user",
        description="Assign a parcel to a user. Ensures parcel is not already assigned.",
        tags=['Parcels'],
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'parcel_id': {'type': 'string', 'format': 'uuid', 'description': 'UUID of the parcel'},
                    'user_id': {'type': 'string', 'format': 'uuid', 'description': 'UUID of the user'}
                },
                'required': ['parcel_id', 'user_id']
            }
        },
        responses={
            200: OpenApiResponse(description="Parcel allocated successfully"),
            400: OpenApiResponse(description="Parcel already assigned or invalid data"),
            404: OpenApiResponse(description="Parcel or user not found"),
        },
    )
    @action(detail=False, methods=['post'], permission_classes=[IsAdmin])
    def allocate_parcel(self, request):
        """Allocate a parcel to a user"""
        parcel_id = request.data.get('parcel_id')
        user_id = request.data.get('user_id')
        
        if not parcel_id or not user_id:
            return Response(
                {'error': 'Both parcel_id and user_id are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            parcel = Parcel.objects.get(parcel_id=parcel_id, is_deleted=False)
            allocation_scope = scope_county(request)
            if allocation_scope and normalize_county(parcel.county) != normalize_county(allocation_scope):
                return Response({'error': f'Plot {parcel.parcel_ref} is in another county'},
                                status=status.HTTP_403_FORBIDDEN)
            
            # Check if parcel is already assigned
            if parcel.owner_user is not None:
                return Response(
                    {
                        'error': 'Parcel is already assigned',
                        'assigned_to': parcel.owner_user.username,
                        'parcel_ref': parcel.parcel_ref
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            user = User.objects.get(user_id=user_id, is_active=True, is_deleted=False)
            if allocation_scope and normalize_county(user.county) != normalize_county(allocation_scope):
                return Response({'error': f'{user.username} belongs to another county'},
                                status=status.HTTP_403_FORBIDDEN)
            
            # Assign the parcel
            parcel.owner_user = user
            parcel.status = 'active'
            parcel.save()
            
            # Create history record
            ParcelHistory.objects.create(
                parcel=parcel,
                owner_user=user,
                geom=parcel.geom,
                area_m2=parcel.area_m2,
                changed_by=request.user,
                change_reason=f'Parcel allocated to {user.username} by {request.user.username}'
            )
            audit.record('parcel.assigned', obj=parcel, parcel_ref=parcel.parcel_ref, new_owner=user.username)
            
            return Response({
                'success': True,
                'message': 'Parcel allocated successfully',
                'parcel_id': str(parcel.parcel_id),
                'parcel_ref': parcel.parcel_ref,
                'allocated_to': {
                    'user_id': str(user.user_id),
                    'username': user.username,
                    'email': user.email
                }
            })
            
        except Parcel.DoesNotExist:
            return Response(
                {'error': 'Parcel not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found or inactive'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {'error': f'Failed to allocate parcel: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @extend_schema(
        summary="Get unassigned parcels",
        description="Retrieve all parcels that don't have an owner assigned yet.",
        tags=['Parcels'],
        responses={200: ParcelListSerializer(many=True)},
    )
    @action(detail=False, methods=['get'], permission_classes=[IsAdminOrAuditor])
    def allocated(self, request):
        """Get parcels that already have an owner assigned"""
        parcels = Parcel.objects.filter(owner_user__isnull=False, is_deleted=False).select_related('owner_user')
        allocation_scope = scope_county(request)
        if allocation_scope:
            parcels = parcels.filter(county_q('county', allocation_scope))
        
        county = request.query_params.get('county')
        if county:
            parcels = parcels.filter(county=county)
        sub_county = request.query_params.get('sub_county')
        if sub_county:
            parcels = parcels.filter(sub_county=sub_county)
        ward = request.query_params.get('ward')
        if ward:
            parcels = parcels.filter(ward=ward)
        search = request.query_params.get('search')
        if search:
            parcels = parcels.filter(
                Q(parcel_ref__icontains=search) | Q(owner_user__username__icontains=search)
            )
        
        page = self.paginate_queryset(parcels)
        if page is not None:
            serializer = ParcelListSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(serializer.data)
        
        serializer = ParcelListSerializer(parcels, many=True, context={'request': request})
        return Response({'parcels': serializer.data})

    @action(detail=False, methods=['get'], permission_classes=[IsAdminOrAuditor])
    def unassigned(self, request):
        """Get parcels without owners"""
        parcels = Parcel.objects.filter(owner_user__isnull=True, is_deleted=False)
        allocation_scope = scope_county(request)
        if allocation_scope:
            parcels = parcels.filter(county_q('county', allocation_scope))
        
        # Apply filters
        county = request.query_params.get('county')
        if county:
            parcels = parcels.filter(county=county)
        
        sub_county = request.query_params.get('sub_county')
        if sub_county:
            parcels = parcels.filter(sub_county=sub_county)
        
        ward = request.query_params.get('ward')
        if ward:
            parcels = parcels.filter(ward=ward)
        
        page = self.paginate_queryset(parcels)
        if page is not None:
            serializer = ParcelListSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(serializer.data)
        
        serializer = ParcelListSerializer(parcels, many=True, context={'request': request})
        return Response(serializer.data)
    
    @extend_schema(
        summary="Transfer parcel ownership (Legacy)",
        description="Transfer ownership of a parcel to another user. Creates a history record. Use assign_owner instead.",
        tags=['Parcels'],
        deprecated=True,
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'new_owner_id': {'type': 'string', 'format': 'uuid', 'description': 'UUID of the new owner'}
                },
                'required': ['new_owner_id']
            }
        },
        responses={
            200: OpenApiResponse(description="Parcel transferred successfully"),
            400: OpenApiResponse(description="Invalid request data"),
            404: OpenApiResponse(description="New owner not found"),
        },
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def transfer(self, request, pk=None):
        """Transfer parcel ownership (use assign_owner instead)"""
        # Redirect to assign_owner
        return self.assign_owner(request, pk)
    
    @extend_schema(summary="Payment status of every billed parcel for a year", tags=['Parcels'])
    @action(detail=False, methods=['get'], permission_classes=[IsAdminOrAuditor], url_path='payment-statuses')
    def payment_statuses(self, request):
        try:
            year = int(request.query_params.get('year') or timezone.now().year)
        except ValueError:
            return Response({'error': 'year must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'year': year, 'statuses': rate_reports.payment_statuses(year, scope_county(request))})

    @staticmethod
    def _bill_properties(bill):
        if bill is None:
            return {'payment_status': 'not_billed', 'bill': None}
        return {
            'payment_status': rate_reports.bill_state(bill),
            'bill': {
                'payment_id': str(bill.payment_id),
                'amount': str(bill.amount),
                'currency': bill.currency,
                'status': bill.status,
                'deadline': bill.deadline.isoformat() if bill.deadline else None,
                'days_overdue': bill.days_overdue(),
                'receipt': bill.processor_ref if bill.status == 'completed' else None,
                'failure_reason': bill.failure_reason,
                'basis': (bill.metadata or {}).get('basis'),
                'explanation': rate_explanation(bill.parcel) if bill.parcel_id else None,
                'standard_amount': str(annual_rate(bill.parcel)) if bill.parcel_id else None,
                'paid_at': bill.updated_at.isoformat() if bill.status == 'completed' else None,
            },
        }

    @extend_schema(
        summary="Get all parcels as GeoJSON FeatureCollection",
        description="Retrieve all parcels as a single GeoJSON FeatureCollection optimized for Leaflet visualization. Supports filtering and bbox queries.",
        tags=['Parcels'],
        parameters=[
            OpenApiParameter('status', OpenApiTypes.STR, description='Filter by status (active, disputed, transferred, archived)'),
            OpenApiParameter('owner', OpenApiTypes.UUID, description='Filter by owner user ID'),
            OpenApiParameter('bbox', OpenApiTypes.STR, description='Bounding box filter: min_lon,min_lat,max_lon,max_lat'),
            OpenApiParameter('simplify', OpenApiTypes.FLOAT, description='Simplify geometries (tolerance in degrees, e.g., 0.0001)', default=0),
            OpenApiParameter('year', OpenApiTypes.INT, description='Rating year for payment_status (default: current year)'),
        ],
        responses={
            200: inline_serializer(
                name='ParcelGeoJSONCollection',
                fields={
                    'type': drf_serializers.CharField(default='FeatureCollection'),
                    'features': drf_serializers.ListField(
                        child=drf_serializers.JSONField(),
                        help_text='Array of GeoJSON features'
                    ),
                }
            )
        },
    )
    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def geojson(self, request):
        """
        Return parcels as one GeoJSON FeatureCollection. Geometry is serialised by PostGIS
        and spliced into the response unparsed.
        """
        import json
        from django.contrib.gis.db.models import GeometryField
        from django.contrib.gis.db.models.functions import AsGeoJSON
        from django.contrib.gis.geos import Polygon as GEOSPolygon
        from django.db.models import FloatField, Func
        from django.http import HttpResponse

        params = request.query_params
        queryset = self.get_queryset().filter(is_deleted=False)
        for param, lookup in (('status', 'status'), ('owner', 'owner_user_id'), ('county', 'county'),
                              ('sub_county', 'sub_county'), ('ward', 'ward')):
            if params.get(param):
                if param == 'county':
                    queryset = queryset.filter(county_q('county', params[param]))
                else:
                    queryset = queryset.filter(**{lookup: params[param]})
        search = (params.get('search') or '').strip()
        if search:
            queryset = queryset.filter(Q(parcel_ref__iexact=search) | Q(owner_user__username__iexact=search))

        bbox = params.get('bbox')
        if bbox:
            try:
                min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(','))
                queryset = queryset.filter(geom__intersects=GEOSPolygon.from_bbox((min_lon, min_lat, max_lon, max_lat)))
            except (ValueError, TypeError):
                return Response(
                    {'error': 'Invalid bbox format. Use: min_lon,min_lat,max_lon,max_lat'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        try:
            simplify_tolerance = max(float(params.get('simplify', 0)), 0)
        except (ValueError, TypeError):
            simplify_tolerance = 0
        try:
            year = int(params.get('year') or timezone.now().year)
        except ValueError:
            return Response({'error': 'year must be an integer'}, status=status.HTTP_400_BAD_REQUEST)

        geometry = F('geom')
        if simplify_tolerance:
            geometry = Func(geometry, simplify_tolerance, function='ST_SimplifyPreserveTopology',
                            output_field=GeometryField(srid=4326))
        rows = queryset.annotate(
            geometry_json=AsGeoJSON(geometry, precision=7),
            centroid_lat=Func('centroid', function='ST_Y', output_field=FloatField()),
            centroid_lng=Func('centroid', function='ST_X', output_field=FloatField()),
        ).values_list(
            'parcel_id', 'geometry_json', 'parcel_ref', 'owner_user__username', 'owner_user_id', 'area_m2',
            'status', 'county', 'sub_county', 'ward', 'centroid_lat', 'centroid_lng',
            'created_at', 'updated_at', 'props', 'land_use',
        )

        bills = Payment.objects.filter(payment_year=year, parcel__in=queryset).select_related('parcel')
        if getattr(request.user, 'role', 'user') not in ['admin', 'auditor']:
            bills = bills.filter(user=request.user)
        bills_by_parcel = {bill.parcel_id: bill for bill in bills}

        dumps = json.JSONEncoder(separators=(',', ':'), default=str).encode
        parts = []
        for (pid, geometry_json, ref, owner, owner_id, area, parcel_status, county, sub_county, ward,
             lat, lng, created, updated, props, land_use) in rows:
            props = props or {}
            properties = {
                'parcel_ref': ref,
                'owner_username': owner,
                'owner_id': str(owner_id) if owner_id else None,
                'area_m2': float(area) if area else None,
                'area_acres': round(float(area) / 4046.86, 2) if area else None,
                'status': parcel_status,
                'county': county,
                'sub_county': sub_county,
                'ward': ward,
                'centroid': {'lat': lat, 'lng': lng} if lat is not None else None,
                'created_at': created.isoformat() if created else None,
                'updated_at': updated.isoformat() if updated else None,
                'custom_props': props,
                'land_use': land_use,
                'registration_section': props.get('REG_SECTIO'),
                'map_sheet': props.get('SHEET_NO'),
                'payment_year': year,
                **self._bill_properties(bills_by_parcel.get(pid)),
            }
            parts.append(
                f'{{"type":"Feature","id":"{pid}","geometry":{geometry_json or "null"},"properties":{dumps(properties)}}}'
            )

        body = f'{{"type":"FeatureCollection","count":{len(parts)},"year":{year},"features":[{",".join(parts)}]}}'
        return HttpResponse(body, content_type='application/json')
    
    @extend_schema(
        summary="Upload shapefile ZIP for import",
        description="Upload a ZIP file containing shapefile components (.shp, .shx, .dbf, .prj) to import parcels. Admin only.",
        tags=['Parcels'],
        request=ShapefileUploadSerializer,
        responses={
            200: ShapefileImportResultSerializer,
            400: OpenApiResponse(description="Invalid request or file"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    @action(detail=False, methods=['post'], permission_classes=[IsAdmin])
    def upload_shapefile(self, request):
        """
        Upload and import parcels from a ZIP file containing shapefile.
        
        The ZIP file should contain at minimum:
        - .shp file (required)
        - .shx file (required)
        - .dbf file (required)
        - .prj file (recommended for coordinate system info)
        
        If shapefile is missing .prj file or has coordinate system issues:
        - Provide source_epsg parameter (e.g., 21037 for Kenya Arc 1960 UTM 37S)
        - Common Kenya EPSG codes: 21037, 32737 (UTM), 4326 (WGS84 Lat/Long)
        - Set auto_generate_ref=true to auto-generate missing parcel numbers
        """
        serializer = ShapefileUploadSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Invalid data', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get validated data
        zip_file = serializer.validated_data['zip_file']
        county = serializer.validated_data.get('county')
        sub_county = serializer.validated_data.get('sub_county')
        ward = serializer.validated_data.get('ward')
        ref_field = serializer.validated_data.get('ref_field', 'PARCEL_ID')
        parcel_status = serializer.validated_data.get('status', 'active')
        owner_username = serializer.validated_data.get('owner_username')
        clear_existing = serializer.validated_data.get('clear_existing', False)
        auto_generate_ref = serializer.validated_data.get('auto_generate_ref', False)
        source_epsg = serializer.validated_data.get('source_epsg')
        
        # Determine owner (optional - can be assigned later)
        owner_user = None
        if owner_username:
            try:
                owner_user = User.objects.get(username=owner_username)
            except User.DoesNotExist:
                return Response(
                    {'success': False, 'message': f'User not found: {owner_username}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        # Note: owner_user can be None - admin will assign owners later via UI
        
        # Build location metadata
        location_metadata = {}
        if county:
            location_metadata['county'] = county
        if sub_county:
            location_metadata['sub_county'] = sub_county
        if ward:
            location_metadata['ward'] = ward
        
        # Create importer
        importer = ShapefileImporter(
            zip_file=zip_file,
            ref_field=ref_field,
            status=parcel_status,
            owner_user=owner_user,
            clear_existing=clear_existing,
            location_metadata=location_metadata,
            auto_generate_ref=auto_generate_ref,
            source_epsg=source_epsg
        )
        
        try:
            # Extract ZIP
            importer.extract_zip()
            
            # Get shapefile info
            shapefile_info = importer.get_shapefile_info()
            
            # Import parcels
            result = importer.import_parcels()
            result['shapefile_info'] = shapefile_info
            
            result_serializer = ShapefileImportResultSerializer(result)
            
            return Response(
                result_serializer.data,
                status=status.HTTP_200_OK if result['success'] else status.HTTP_400_BAD_REQUEST
            )
            
        except ValueError as e:
            return Response(
                {
                    'success': False,
                    'message': str(e),
                    'imported_count': 0,
                    'skipped_count': 0,
                    'error_count': 1,
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {
                    'success': False,
                    'message': f'Unexpected error: {str(e)}',
                    'imported_count': 0,
                    'skipped_count': 0,
                    'error_count': 1,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        finally:
            importer.cleanup()


@extend_schema_view(
    list=extend_schema(
        summary="List parcel history",
        description="Get a paginated list of all parcel change history records in GeoJSON format.",
        tags=['Audit'],
        responses={
            200: inline_serializer(
                name='ParcelHistoryGeoJSON',
                fields={
                    'type': drf_serializers.CharField(default='Feature'),
                    'id': drf_serializers.UUIDField(),
                    'geometry': drf_serializers.JSONField(help_text='GeoJSON geometry object'),
                    'properties': drf_serializers.JSONField(help_text='History record properties'),
                }
            )
        },
    ),
    retrieve=extend_schema(
        summary="Get parcel history entry",
        description="Retrieve details of a specific parcel history record in GeoJSON format.",
        tags=['Audit'],
        responses={
            200: inline_serializer(
                name='ParcelHistoryDetail',
                fields={
                    'type': drf_serializers.CharField(default='Feature'),
                    'id': drf_serializers.UUIDField(),
                    'geometry': drf_serializers.JSONField(help_text='GeoJSON geometry object'),
                    'properties': drf_serializers.JSONField(help_text='History record properties'),
                }
            )
        },
    ),
)
    
class ParcelHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for viewing parcel change history.
    """
    queryset = ParcelHistory.objects.select_related('parcel', 'owner_user', 'changed_by').all()
    serializer_class = ParcelHistorySerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['parcel', 'owner_user', 'changed_by']
    ordering_fields = ['change_ts']
    permission_classes = [permissions.IsAuthenticated]
    
    def get_serializer_context(self):
        """Pass request context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context



@extend_schema_view(
    list=extend_schema(
        summary="List ledger entries",
        description="Get a paginated list of ledger entries. Regular users see only entries from their accounts.",
        tags=['Ledger'],
    ),
    retrieve=extend_schema(
        summary="Get ledger entry details",
        description="Retrieve details of a specific ledger entry.",
        tags=['Ledger'],
    ),
)
class LedgerEntryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for viewing ledger entries and transactions.
    """
    queryset = LedgerEntry.objects.select_related('account').all()
    serializer_class = LedgerEntrySerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['account', 'entry_type', 'related_account_id']
    ordering_fields = ['created_at']
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Filter entries based on user role"""
        user = self.request.user
        user_role = getattr(user, 'role', 'user')
        
        if user_role in ['admin', 'auditor']:
            return self.queryset

        user_accounts = Account.objects.filter(owner_user=user).values_list('account_id', flat=True)
        return self.queryset.filter(account_id__in=user_accounts)
    
    def get_serializer_context(self):
        """Pass request context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    @extend_schema(
        summary="Verify ledger chain",
        description="Verify the integrity of the ledger entry chain. Admin/Auditor only.",
        tags=['Ledger'],
        responses={200: OpenApiResponse(description="Chain verification result")},
    )
    @action(detail=False, methods=['get'], permission_classes=[IsAdminOrAuditor])
    def verify_chain(self, request):
        """Verify ledger entry chain integrity (admin/auditor only)"""
        # This would implement chain verification logic
        return Response({
            'message': 'Chain verification not yet implemented'
        })



@extend_schema_view(
    list=extend_schema(
        summary="List payments",
        description="Get a paginated list of payments. Regular users see only their payments.",
        tags=['Payments'],
    ),
    retrieve=extend_schema(
        summary="Get payment details",
        description="Retrieve details of a specific payment.",
        tags=['Payments'],
    ),
    create=extend_schema(
        summary="Create new payment",
        description="Create a new payment transaction.",
        tags=['Payments'],
    ),
    update=extend_schema(
        summary="Update payment",
        description="Update payment information.",
        tags=['Payments'],
    ),
    partial_update=extend_schema(
        summary="Partially update payment",
        description="Partially update payment information.",
        tags=['Payments'],
    ),
    destroy=extend_schema(
        summary="Delete payment",
        description="Delete a payment record.",
        tags=['Payments'],
    ),
)
class PaymentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing payment transactions.
    """
    queryset = Payment.objects.select_related('user', 'account', 'parcel').prefetch_related('user__parcels')
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'processor', 'currency', 'user', 'account', 'parcel', 'payment_year']
    ordering_fields = ['created_at', 'updated_at', 'amount']
    permission_classes = [permissions.IsAuthenticated]
    
    def get_serializer_class(self):
        """Select appropriate serializer"""
        if self.action == 'create':
            return PaymentCreateSerializer
        return PaymentSerializer

    def get_permissions(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return [IsAdmin()]
        return super().get_permissions()
    
    def get_queryset(self):
        """Payments are limited to the signed-in user's county; land owners see only their own."""
        user = self.request.user
        user_role = getattr(user, 'role', 'user')
        county = scope_county(self.request)
        queryset = self.queryset
        if county:
            queryset = queryset.filter(
                Q(parcel__county__iexact=county) | Q(parcel__isnull=True, user__county__iexact=county)
            )
        if user_role in ['admin', 'auditor', 'owner']:
            return queryset
        return queryset.filter(user=user)
    
    def get_serializer_context(self):
        """Pass request context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def perform_create(self, serializer):
        """Create payment with user auto-assignment"""
        serializer.save(status='pending')
    
    @extend_schema(
        summary="Confirm payment",
        description="Confirm a pending payment. Admin/Auditor only.",
        tags=['Payments'],
        responses={
            200: OpenApiResponse(description="Payment confirmed successfully"),
            400: OpenApiResponse(description="Payment cannot be confirmed"),
        },
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def confirm(self, request, pk=None):
        """Confirm payment (admin/processor only)"""
        payment = self.get_object()
        
        if payment.status != 'pending':
            return Response(
                {'error': f'Cannot confirm payment with status: {payment.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        payment.status = 'completed'
        payment.save()
        audit.record('payment.confirmed_manually', obj=payment, amount=str(payment.amount),
                     parcel_ref=payment.parcel.parcel_ref if payment.parcel_id else None)
        
        return Response({
            'message': 'Payment confirmed successfully',
            'payment_id': payment.payment_id,
            'status': payment.status
        })
    
    @extend_schema(
        summary="Refund payment",
        description="Refund a completed payment. Admin only.",
        tags=['Payments'],
        responses={
            200: OpenApiResponse(description="Payment refunded successfully"),
            400: OpenApiResponse(description="Payment cannot be refunded"),
        },
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def refund(self, request, pk=None):
        """Refund payment (admin only)"""
        payment = self.get_object()
        
        if payment.status != 'completed':
            return Response(
                {'error': 'Can only refund completed payments'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        payment.status = 'refunded'
        payment.save()
        audit.record('payment.refunded', obj=payment, amount=str(payment.amount),
                     parcel_ref=payment.parcel.parcel_ref if payment.parcel_id else None)

        return Response({
            'message': 'Payment refunded successfully',
            'payment_id': payment.payment_id,
            'status': payment.status
        })

    def _report_year(self, request):
        try:
            return int(request.query_params.get('year') or timezone.now().year)
        except ValueError:
            raise ValidationError({'year': 'year must be an integer'})

    @extend_schema(summary="Money collected from completed rate payments", tags=['Payments'])
    @action(detail=False, methods=['get'], permission_classes=[IsAdminOrAuditor])
    def collections(self, request):
        return Response(rate_reports.collections(self._report_year(request), scope_county(request)))

    @extend_schema(summary="Every county on the platform with its rates position", tags=['Payments'])
    @action(detail=False, methods=['get'], permission_classes=[IsPlatformOwner])
    def counties(self, request):
        year = self._report_year(request)
        return Response({'year': year, 'counties': rate_reports.counties(year)})

    @extend_schema(summary="Rates collection and defaulters per ward", tags=['Payments'])
    @action(detail=False, methods=['get'], permission_classes=[IsAdminOrAuditor])
    def wards(self, request):
        year = self._report_year(request)
        return Response({'year': year, 'wards': rate_reports.wards(year, scope_county(request))})

    @extend_schema(summary="Owned parcels in a ward with their rates bill", tags=['Payments'])
    @action(detail=False, methods=['get'], permission_classes=[IsAdminOrAuditor], url_path='ward-parcels')
    def ward_parcels(self, request):
        ward = (request.query_params.get('ward') or '').strip()
        if not ward:
            raise ValidationError({'ward': 'ward is required'})
        year = self._report_year(request)
        return Response({'year': year, 'ward': ward, 'parcels': rate_reports.ward_parcels(ward, year, scope_county(request))})

    def _mpesa_state(self, payment):
        tx = payment_flow.latest_push(payment)
        return {
            'payment_id': payment.payment_id,
            'status': payment.status,
            'amount': payment.amount,
            'receipt': payment.processor_ref if payment.status == 'completed' else None,
            'failure_reason': payment.failure_reason,
            'checkout_request_id': tx.checkout_request_id if tx else None,
            'result_desc': tx.result_desc if tx else None,
        }

    @extend_schema(
        summary="Pay via M-Pesa STK push",
        description="Send an M-Pesa payment prompt to the given phone for this bill's exact amount.",
        tags=['Payments'],
        request=inline_serializer(name='MpesaPayRequest', fields={'phone': drf_serializers.CharField()}),
        responses={202: OpenApiResponse(description="Prompt sent"), 400: OpenApiResponse(description="Rejected")},
    )
    @action(detail=True, methods=['post'], url_path='mpesa')
    def mpesa_pay(self, request, pk=None):
        payment = self.get_object()
        try:
            payment_flow.start_stk_push(payment.pk, request.data.get('phone', ''))
        except mpesa.MpesaError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        payment.refresh_from_db()
        return Response(self._mpesa_state(payment), status=status.HTTP_202_ACCEPTED)

    @extend_schema(summary="M-Pesa payment status", tags=['Payments'])
    @action(detail=True, methods=['get'], url_path='mpesa/status')
    def mpesa_status(self, request, pk=None):
        payment = self.get_object()
        tx = payment_flow.latest_push(payment)
        if tx and payment.status == 'processing':
            payment_flow.refresh_from_daraja(tx)
            payment.refresh_from_db()
        return Response(self._mpesa_state(payment))

    @extend_schema(exclude=True)
    @action(
        detail=False, methods=['post'], url_path=r'stk-callback/(?P<secret>[^/]+)',
        permission_classes=[permissions.AllowAny], authentication_classes=[],
    )
    def mpesa_callback(self, request, secret=None):
        expected = settings.MPESA_CALLBACK_SECRET
        if not expected or not hmac.compare_digest(secret, expected):
            return Response(status=status.HTTP_404_NOT_FOUND)
        cb = (request.data.get('Body') or {}).get('stkCallback') or {}
        if cb.get('CheckoutRequestID') and cb.get('ResultCode') is not None:
            payment_flow.apply_result(
                cb['CheckoutRequestID'], cb['ResultCode'], cb.get('ResultDesc', ''),
                mpesa.parse_callback_items(cb), raw=request.data,
            )
        return Response({'ResultCode': 0, 'ResultDesc': 'Accepted'})
    
    @extend_schema(
        summary="Get list of defaulters",
        description="Retrieve users with overdue payments. Supports filtering by location, amount, and days overdue.",
        tags=['Payments'],
        parameters=[
            OpenApiParameter('min_days_overdue', OpenApiTypes.INT, description='Minimum days overdue (default: 1)'),
            OpenApiParameter('max_days_overdue', OpenApiTypes.INT, description='Maximum days overdue'),
            OpenApiParameter('currency', OpenApiTypes.STR, description='Filter by currency'),
            OpenApiParameter('min_amount', OpenApiTypes.FLOAT, description='Minimum payment amount'),
            OpenApiParameter('ward', OpenApiTypes.STR, description='Filter by ward'),
            OpenApiParameter('sub_county', OpenApiTypes.STR, description='Filter by sub-county'),
            OpenApiParameter('county', OpenApiTypes.STR, description='Filter by county'),
            OpenApiParameter('search', OpenApiTypes.STR, description='Search by username, email, phone, or parcel ref'),
        ],
        responses={200: DefaulterSerializer(many=True)},
    )
    @action(detail=False, methods=['get'], permission_classes=[IsAdminOrAuditor])
    def defaulters(self, request):
        """Get list of users with overdue payments"""
        from .serializers import DefaulterSerializer
        from django.db.models import Q
        
        # Get all overdue payments (past deadline and not completed/refunded)
        now = timezone.now()
        
        # Base query: overdue payments
        overdue_payments = Payment.objects.filter(
            deadline__lt=now,
            status__in=['pending', 'processing', 'failed']
        ).select_related('user', 'account').prefetch_related('user__parcels')
        
        # 1. Filter by Days Overdue
        min_days = request.query_params.get('min_days_overdue', 1)
        try:
            min_days = int(min_days)
            cutoff_date_min = now - timedelta(days=min_days)
            overdue_payments = overdue_payments.filter(deadline__lte=cutoff_date_min)
        except ValueError:
            pass
            
        max_days = request.query_params.get('max_days_overdue')
        if max_days:
            try:
                max_days = int(max_days)
                cutoff_date_max = now - timedelta(days=max_days)
                overdue_payments = overdue_payments.filter(deadline__gte=cutoff_date_max)
            except ValueError:
                pass
        
        # 2. Filter by Amount & Currency
        currency_filter = request.query_params.get('currency')
        if currency_filter:
            overdue_payments = overdue_payments.filter(currency=currency_filter)
        
        min_amount = request.query_params.get('min_amount')
        if min_amount:
            try:
                min_amount = Decimal(min_amount)
                overdue_payments = overdue_payments.filter(amount__gte=min_amount)
            except (ValueError, InvalidOperation):
                pass

        # 3. Filter by Location (Ward, Sub-county, County)
        ward = request.query_params.get('ward')
        if ward:
            overdue_payments = overdue_payments.filter(user__parcels__ward__iexact=ward).distinct()
            
        sub_county = request.query_params.get('sub_county')
        if sub_county:
            overdue_payments = overdue_payments.filter(user__parcels__sub_county__iexact=sub_county).distinct()
            
        county = request.query_params.get('county')
        if county:
            overdue_payments = overdue_payments.filter(user__parcels__county__iexact=county).distinct()

        # 4. Search (User details or Parcel Ref)
        search_query = request.query_params.get('search')
        if search_query:
            overdue_payments = overdue_payments.filter(
                Q(user__username__icontains=search_query) |
                Q(user__email__icontains=search_query) |
                Q(user_id__in=user_ids_matching_phone(search_query)) |
                Q(user__parcels__parcel_ref__icontains=search_query)
            ).distinct()

        # Order by most overdue first
        overdue_payments = overdue_payments.order_by('deadline')
        
        # Pagination
        page = self.paginate_queryset(overdue_payments)
        if page is not None:
            results = []
            for payment in page:
                days_overdue = (now - payment.deadline).days
                
                # Get parcels for this user
                user_parcels = payment.user.parcels.all()
                parcels_data = [
                    {
                        'parcel_ref': p.parcel_ref,
                        'ward': p.ward,
                        'sub_county': p.sub_county,
                        'county': p.county,
                        'centroid': {
                            'lat': p.centroid.y,
                            'lon': p.centroid.x
                        } if p.centroid else None
                    }
                    for p in user_parcels
                ]

                results.append({
                    'user_id': payment.user.user_id,
                    'username': payment.user.username,
                    'email': payment.user.email,
                    'phone': payment.user.phone,
                    'payment_id': payment.payment_id,
                    'amount': payment.amount,
                    'currency': payment.currency,
                    'deadline': payment.deadline,
                    'days_overdue': days_overdue,
                    'status': payment.status,
                    'created_at': payment.created_at,
                    'metadata': payment.metadata,
                    'parcels': parcels_data
                })
            
            serializer = DefaulterSerializer(results, many=True)
            return self.get_paginated_response(serializer.data)

        # Fallback if pagination is disabled
        results = []
        for payment in overdue_payments:
            days_overdue = (now - payment.deadline).days
            user_parcels = payment.user.parcels.all()
            parcels_data = [
                {
                    'parcel_ref': p.parcel_ref,
                    'ward': p.ward,
                    'sub_county': p.sub_county,
                    'county': p.county,
                    'centroid': {
                        'lat': p.centroid.y,
                        'lon': p.centroid.x
                    } if p.centroid else None
                }
                for p in user_parcels
            ]
            
            results.append({
                'user_id': payment.user.user_id,
                'username': payment.user.username,
                'email': payment.user.email,
                'phone': payment.user.phone,
                'payment_id': payment.payment_id,
                'amount': payment.amount,
                'currency': payment.currency,
                'deadline': payment.deadline,
                'days_overdue': days_overdue,
                'status': payment.status,
                'created_at': payment.created_at,
                'metadata': payment.metadata,
                'parcels': parcels_data
            })
            
        serializer = DefaulterSerializer(results, many=True)
        return Response(serializer.data)



def find_county(name: str):
    """Fuzzy-matches a county name regardless of whether 'County' is appended or omitted."""
    if not name:
        return None
    raw = str(name).strip()
    match = County.objects.filter(name__iexact=raw).first()
    if match:
        return match
    base = re.sub(r'\s+(city\s+)?county$', '', raw, flags=re.IGNORECASE).strip()
    if base:
        match = (
            County.objects.filter(name__iexact=base).first()
            or County.objects.filter(name__iexact=f'{base} County').first()
            or County.objects.filter(name__iexact=f'{base} City County').first()
            or County.objects.filter(name__icontains=base).first()
        )
        if match:
            return match
    return None


@extend_schema_view(
    list=extend_schema(
        summary="List audit logs",
        description="Get a paginated list of audit log entries. Admin/Auditor only.",
        tags=['Audit'],
    ),
    retrieve=extend_schema(
        summary="Get audit log entry",
        description="Retrieve details of a specific audit log entry. Admin/Auditor only.",
        tags=['Audit'],
    ),
)
class CountyViewSet(viewsets.ModelViewSet):
    """Counties on the platform. Owners manage them; staff read their own."""
    queryset = County.objects.all()
    serializer_class = CountySerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['is_active']
    search_fields = ['name']

    def get_permissions(self):
        if self.action in ('list', 'retrieve', 'mine'):
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsPlatformOwner()]

    def get_queryset(self):
        county_name = scope_county(self.request)
        if not county_name:
            return self.queryset
        c = find_county(county_name)
        if c:
            return self.queryset.filter(pk=c.pk)
        return self.queryset.filter(name__icontains=re.sub(r'\s+(city\s+)?county$', '', county_name, flags=re.IGNORECASE).strip())

    def perform_create(self, serializer):
        county = serializer.save()
        audit.record('county.added', obj=county, object_type='county', name=county.name)

    def perform_update(self, serializer):
        county = serializer.save()
        audit.record('county.updated', obj=county, object_type='county', name=county.name)

    @extend_schema(summary="The county the signed-in account belongs to", tags=['Counties'])
    @action(detail=False, methods=['get'])
    def mine(self, request):
        name = (request.user.county or '').strip()
        county = find_county(name)
        if not county:
            return Response({'county': None, 'role': request.user.role})
        return Response({'county': CountySerializer(county, context={'request': request}).data, 'role': request.user.role})


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for viewing audit logs. Admin/Auditor access only.
    """
    queryset = AuditLog.objects.select_related('who').order_by('-audit_id')
    serializer_class = AuditLogSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['who', 'action', 'object_type']
    search_fields = ['action', 'who__username', 'details']
    ordering_fields = ['created_at', 'audit_id']
    permission_classes = [permissions.IsAuthenticated, IsAdminOrAuditor]

    def get_queryset(self):
        queryset = super().get_queryset()
        category = self.request.query_params.get('category')
        if category in audit.CATEGORIES:
            queryset = queryset.filter(action__startswith=f'{category}.')
        since = self.request.query_params.get('since')
        if since:
            queryset = queryset.filter(created_at__date__gte=since)
        until = self.request.query_params.get('until')
        if until:
            queryset = queryset.filter(created_at__date__lte=until)
        return queryset

    @extend_schema(summary="Audit event counts per category", tags=['Audit'])
    @action(detail=False, methods=['get'])
    def summary(self, request):
        base = AuditLog.objects.all()
        counts = {key: base.filter(action__startswith=f'{key}.').count() for key in audit.CATEGORIES}
        return Response({
            'categories': [{'key': k, 'label': label, 'count': counts[k]} for k, label in audit.CATEGORIES.items()],
            'failed_logins_today': base.filter(action='auth.login_failed', created_at__date=timezone.now().date()).count(),
        })
    
    def get_serializer_context(self):
        """Pass request context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
class ReportsViewSet(viewsets.ViewSet):
    """
    ViewSet for generating various system reports.
    All endpoints require admin or auditor role.
    """

    @extend_schema(
        summary="Download a report as PDF or Excel",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('report', OpenApiTypes.STR, enum=['collections', 'arrears', 'register'], required=True),
            OpenApiParameter('file_format', OpenApiTypes.STR, enum=['pdf', 'xlsx'], required=True),
            OpenApiParameter('year', OpenApiTypes.INT, description='collections: rating year'),
            OpenApiParameter('as_of', OpenApiTypes.DATE, description='arrears: overdue as of this date'),
            OpenApiParameter('from', OpenApiTypes.DATE, description='register: first day'),
            OpenApiParameter('to', OpenApiTypes.DATE, description='register: last day'),
        ],
        responses={200: OpenApiResponse(description='The report file')},
    )
    @action(detail=False, methods=['get'], url_path='download', permission_classes=[IsAdminOrAuditor])
    def download(self, request):
        from django.http import HttpResponse
        from . import report_builders

        params = request.query_params
        kind, fmt = params.get('report'), params.get('file_format')
        if kind not in report_builders.BUILDERS or fmt not in ('pdf', 'xlsx'):
            return Response({'error': 'report must be collections, arrears or register; file_format pdf or xlsx'},
                            status=status.HTTP_400_BAD_REQUEST)
        today_local = timezone.now().astimezone(report_builders.NAIROBI).date()
        county = scope_county(request)
        try:
            if kind == 'collections':
                report = report_builders.collections_report(int(params.get('year') or today_local.year), county)
            elif kind == 'arrears':
                report = report_builders.arrears_report(datetime.strptime(params.get('as_of') or today_local.isoformat(), '%Y-%m-%d').date(), county)
            else:
                start = datetime.strptime(params.get('from') or today_local.replace(day=1).isoformat(), '%Y-%m-%d').date()
                end = datetime.strptime(params.get('to') or today_local.isoformat(), '%Y-%m-%d').date()
                if end < start:
                    return Response({'error': '"to" must be on or after "from"'}, status=status.HTTP_400_BAD_REQUEST)
                report = report_builders.register_report(start, end, county)
        except ValueError:
            return Response({'error': 'Dates must be YYYY-MM-DD and year a number'}, status=status.HTTP_400_BAD_REQUEST)

        who = request.user.username
        if fmt == 'pdf':
            body, content_type = report_builders.render_pdf(report, who), 'application/pdf'
        else:
            body = report_builders.render_xlsx(report, who)
            content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        response = HttpResponse(body, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{report.slug}.{fmt}"'
        return response

    @extend_schema(
        summary="Download an AI-generated PDF report or analysis",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('file', OpenApiTypes.STR, required=True, description='The generated PDF filename'),
        ],
        responses={200: OpenApiResponse(description='The PDF file')},
    )
    @action(detail=False, methods=['get'], url_path='ai-download', permission_classes=[IsAdminOrAuditor])
    def ai_download(self, request):
        from django.http import HttpResponse
        from pathlib import Path
        import re

        filename = (request.query_params.get('file') or '').strip()
        if not re.match(r'^[a-zA-Z0-9_\-]+\.pdf$', filename):
            return Response({'error': 'Invalid report filename'}, status=status.HTTP_400_BAD_REQUEST)

        file_path = Path(settings.MEDIA_ROOT) / 'ai_reports' / filename
        if not file_path.exists() or not file_path.is_file():
            return Response({'error': 'Report file not found'}, status=status.HTTP_404_NOT_FOUND)

        pdf_bytes = file_path.read_bytes()
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response

    permission_classes = [permissions.IsAuthenticated, IsAdminOrAuditor]
    
    def _get_date_range(self, request):
        """Helper to parse date range from request"""
        period = request.query_params.get('period', 'month')
        end_date = timezone.now().date()
        
        if period == 'today':
            start_date = end_date
        elif period == 'week':
            start_date = end_date - timedelta(days=7)
        elif period == 'month':
            start_date = end_date - timedelta(days=30)
        elif period == 'quarter':
            start_date = end_date - timedelta(days=90)
        elif period == 'year':
            start_date = end_date - timedelta(days=365)
        elif period == 'custom':
            try:
                start_date = datetime.strptime(request.query_params['start_date'], '%Y-%m-%d').date()
                end_date = datetime.strptime(request.query_params['end_date'], '%Y-%m-%d').date()
            except (KeyError, ValueError):
                raise ValidationError({'detail': 'period=custom needs start_date and end_date as YYYY-MM-DD'})
            if start_date > end_date:
                raise ValidationError({'detail': 'start_date must be on or before end_date'})
        else:
            start_date = end_date - timedelta(days=30)
        
        return start_date, end_date
    
    @extend_schema(
        summary="Get user statistics report",
        description="Generate comprehensive user statistics including counts by role, verification status, etc.",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('period', OpenApiTypes.STR, description='Time period: today, week, month, quarter, year, custom'),
            OpenApiParameter('start_date', OpenApiTypes.DATE, description='Start date for custom period'),
            OpenApiParameter('end_date', OpenApiTypes.DATE, description='End date for custom period'),
        ],
        responses={200: UserReportSerializer},
    )
    @action(detail=False, methods=['get'])
    def users(self, request):
        """Generate user statistics report"""
        start_date, end_date = self._get_date_range(request)
        
        users = User.objects.filter(is_deleted=False)
        new_users = users.filter(created_at__date__gte=start_date, created_at__date__lte=end_date)
        
        # Count users by role
        users_by_role = {}
        for role in ['user', 'admin', 'auditor']:
            users_by_role[role] = users.filter(role=role).count()
        
        report = {
            'total_users': users.count(),
            'active_users': users.filter(is_active=True).count(),
            'verified_users': users.filter(is_verified=True).count(),
            'users_by_role': users_by_role,
            'new_users_count': new_users.count(),
            'locked_accounts': users.filter(locked_until__isnull=False, locked_until__gt=timezone.now()).count(),
            'users_with_parcels': users.filter(parcels__isnull=False).distinct().count(),
            'users_with_accounts': users.filter(accounts__isnull=False).distinct().count(),
        }
        
        serializer = UserReportSerializer(report)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Get account statistics report",
        description="Generate comprehensive account statistics including balances and status breakdown.",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('period', OpenApiTypes.STR, description='Time period'),
            OpenApiParameter('start_date', OpenApiTypes.DATE),
            OpenApiParameter('end_date', OpenApiTypes.DATE),
        ],
        responses={200: AccountReportSerializer},
    )
    @action(detail=False, methods=['get'])
    def accounts(self, request):
        """Generate account statistics report"""
        start_date, end_date = self._get_date_range(request)
        
        accounts = Account.objects.filter(is_deleted=False)
        
        # Accounts by type
        accounts_by_type = {}
        for acc_type in ['main', 'escrow', 'reserve']:
            accounts_by_type[acc_type] = accounts.filter(account_type=acc_type).count()
        
        # Accounts by currency
        accounts_by_currency = accounts.values('currency').annotate(
            count=Count('account_id'),
            total_balance=Sum('current_balance')
        )
        currency_dict = {item['currency']: {
            'count': item['count'],
            'total_balance': str(item['total_balance'] or 0)
        } for item in accounts_by_currency}
        
        # Calculate totals
        aggregates = accounts.aggregate(
            total_balance=Sum('current_balance'),
            avg_balance=Avg('current_balance')
        )
        
        report = {
            'total_accounts': accounts.count(),
            'active_accounts': accounts.filter(status='active').count(),
            'frozen_accounts': accounts.filter(status='frozen').count(),
            'closed_accounts': accounts.filter(status='closed').count(),
            'total_balance': aggregates['total_balance'] or Decimal('0.00'),
            'average_balance': aggregates['avg_balance'] or Decimal('0.00'),
            'accounts_by_type': accounts_by_type,
            'accounts_by_currency': currency_dict,
        }
        
        serializer = AccountReportSerializer(report)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Get payment statistics report",
        description="Generate comprehensive payment statistics including success rates and processor breakdown.",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('period', OpenApiTypes.STR),
            OpenApiParameter('start_date', OpenApiTypes.DATE),
            OpenApiParameter('end_date', OpenApiTypes.DATE),
        ],
        responses={200: PaymentReportSerializer},
    )
    @action(detail=False, methods=['get'])
    def payments(self, request):
        """Generate payment statistics report"""
        start_date, end_date = self._get_date_range(request)
        
        payments = Payment.objects.filter(
            is_deleted=False,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        )
        
        # Payments by processor
        payments_by_processor = payments.values('processor').annotate(
            count=Count('payment_id'),
            total_amount=Sum('amount')
        )
        processor_dict = {item['processor'] or 'unknown': {
            'count': item['count'],
            'total_amount': str(item['total_amount'] or 0)
        } for item in payments_by_processor}
        
        # Payments by currency
        payments_by_currency = payments.values('currency').annotate(
            count=Count('payment_id'),
            total_amount=Sum('amount')
        )
        currency_dict = {item['currency']: {
            'count': item['count'],
            'total_amount': str(item['total_amount'] or 0)
        } for item in payments_by_currency}
        
        # Calculate success rate
        total = payments.count()
        completed = payments.filter(status='completed').count()
        success_rate = (completed / total * 100) if total > 0 else 0
        
        # Aggregates
        aggregates = payments.aggregate(
            total_amount=Sum('amount'),
            avg_amount=Avg('amount')
        )
        
        report = {
            'total_payments': total,
            'completed_payments': completed,
            'pending_payments': payments.filter(status='pending').count(),
            'failed_payments': payments.filter(status='failed').count(),
            'refunded_payments': payments.filter(status='refunded').count(),
            'total_amount': aggregates['total_amount'] or Decimal('0.00'),
            'average_payment': aggregates['avg_amount'] or Decimal('0.00'),
            'payments_by_processor': processor_dict,
            'payments_by_currency': currency_dict,
            'success_rate': round(success_rate, 2),
        }
        
        serializer = PaymentReportSerializer(report)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Get parcel statistics report",
        description="Generate comprehensive parcel statistics including area calculations and status breakdown.",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('period', OpenApiTypes.STR),
            OpenApiParameter('start_date', OpenApiTypes.DATE),
            OpenApiParameter('end_date', OpenApiTypes.DATE),
        ],
        responses={200: ParcelReportSerializer},
    )
    @action(detail=False, methods=['get'])
    def parcels(self, request):
        """Generate parcel statistics report"""
        start_date, end_date = self._get_date_range(request)
        
        parcels = Parcel.objects.filter(is_deleted=False)
        
        # Parcels by owner (top 10)
        parcels_by_owner = parcels.values(
            'owner_user__username'
        ).annotate(
            count=Count('parcel_id'),
            total_area=Sum('area_m2')
        ).order_by('-count')[:10]
        
        owner_list = [{
            'username': item['owner_user__username'],
            'count': item['count'],
            'total_area': item['total_area']
        } for item in parcels_by_owner]
        
        # Aggregates
        aggregates = parcels.aggregate(
            total_area=Sum('area_m2'),
            avg_area=Avg('area_m2')
        )
        
        report = {
            'total_parcels': parcels.count(),
            'active_parcels': parcels.filter(status='active').count(),
            'disputed_parcels': parcels.filter(status='disputed').count(),
            'transferred_parcels': parcels.filter(status='transferred').count(),
            'archived_parcels': parcels.filter(status='archived').count(),
            'total_area_m2': aggregates['total_area'] or 0,
            'average_area_m2': aggregates['avg_area'] or 0,
            'parcels_by_owner': owner_list,
        }
        
        serializer = ParcelReportSerializer(report)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Get ledger statistics report",
        description="Generate comprehensive ledger statistics including credits, debits, and entry type breakdown.",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('period', OpenApiTypes.STR),
            OpenApiParameter('start_date', OpenApiTypes.DATE),
            OpenApiParameter('end_date', OpenApiTypes.DATE),
        ],
        responses={200: LedgerReportSerializer},
    )
    @action(detail=False, methods=['get'])
    def ledger(self, request):
        """Generate ledger statistics report"""
        start_date, end_date = self._get_date_range(request)
        
        entries = LedgerEntry.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        )
        
        # Entries by type
        entries_by_type = entries.values('entry_type').annotate(
            count=Count('entry_id'),
            total_amount=Sum('amount')
        )
        type_dict = {item['entry_type']: {
            'count': item['count'],
            'total_amount': str(item['total_amount'] or 0)
        } for item in entries_by_type}
        
        # Entries by account type
        entries_by_account_type = entries.values('account__account_type').annotate(
            count=Count('entry_id'),
            total_amount=Sum('amount')
        )
        account_type_dict = {item['account__account_type']: {
            'count': item['count'],
            'total_amount': str(item['total_amount'] or 0)
        } for item in entries_by_account_type}
        
        # Calculate credits and debits
        total_credits = entries.filter(entry_type='credit').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        total_debits = entries.filter(entry_type='debit').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        total_transfers = entries.filter(entry_type__in=['transfer_in', 'transfer_out']).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        
        report = {
            'total_entries': entries.count(),
            'total_credits': total_credits,
            'total_debits': total_debits,
            'total_transfers': total_transfers,
            'entries_by_type': type_dict,
            'entries_by_account_type': account_type_dict,
        }
        
        serializer = LedgerReportSerializer(report)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Get transaction volume over time",
        description="Get transaction volume trends grouped by day, week, or month.",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('period', OpenApiTypes.STR),
            OpenApiParameter('start_date', OpenApiTypes.DATE),
            OpenApiParameter('end_date', OpenApiTypes.DATE),
            OpenApiParameter('group_by', OpenApiTypes.STR, description='Group by: day, week, month', default='day'),
        ],
        responses={200: TransactionVolumeSerializer(many=True)},
    )
    @action(detail=False, methods=['get'])
    def transaction_volume(self, request):
        """Get transaction volume over time"""
        start_date, end_date = self._get_date_range(request)
        group_by = request.query_params.get('group_by', 'day')
        
        entries = LedgerEntry.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        )
        
        # Group by period
        if group_by == 'week':
            entries = entries.annotate(period=TruncWeek('created_at'))
        elif group_by == 'month':
            entries = entries.annotate(period=TruncMonth('created_at'))
        else:  # day
            entries = entries.annotate(period=TruncDate('created_at'))
        
        volume_data = entries.values('period').annotate(
            count=Count('entry_id'),
            total_amount=Sum('amount'),
            avg_amount=Avg('amount')
        ).order_by('period')
        
        result = [{
            'date': item['period'],
            'count': item['count'],
            'total_amount': item['total_amount'] or Decimal('0.00'),
            'avg_amount': item['avg_amount'] or Decimal('0.00'),
        } for item in volume_data]
        
        serializer = TransactionVolumeSerializer(result, many=True)
        return Response(serializer.data)
    
    def _volume_series(self, request, queryset, date_field, value):
        start_date, end_date = self._get_date_range(request)
        trunc = {'week': TruncWeek, 'month': TruncMonth}.get(
            request.query_params.get('group_by'), TruncDate
        )
        rows = (
            queryset.filter(**{
                f'{date_field}__date__gte': start_date,
                f'{date_field}__date__lte': end_date,
            })
            .annotate(bucket=trunc(date_field))
            .values('bucket')
            .annotate(value=value)
            .order_by('bucket')
        )
        return Response([
            {
                'date': row['bucket'].date() if isinstance(row['bucket'], datetime) else row['bucket'],
                'value': row['value'] or 0,
            }
            for row in rows
        ])

    @extend_schema(
        summary="Completed payment volume over time",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('period', OpenApiTypes.STR),
            OpenApiParameter('start_date', OpenApiTypes.DATE),
            OpenApiParameter('end_date', OpenApiTypes.DATE),
            OpenApiParameter('group_by', OpenApiTypes.STR, description='day, week or month', default='day'),
        ],
        responses={200: OpenApiResponse(description='List of {date, value} with value = total amount')},
    )
    @action(detail=False, methods=['get'])
    def payment_volume(self, request):
        completed = Payment.objects.filter(is_deleted=False, status='completed')
        return self._volume_series(request, completed, 'created_at', Sum('amount'))

    @extend_schema(
        summary="Overdue payments by deadline",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('period', OpenApiTypes.STR),
            OpenApiParameter('start_date', OpenApiTypes.DATE),
            OpenApiParameter('end_date', OpenApiTypes.DATE),
            OpenApiParameter('group_by', OpenApiTypes.STR, description='day, week or month', default='day'),
        ],
        responses={200: OpenApiResponse(description='List of {date, value} with value = overdue payment count')},
    )
    @action(detail=False, methods=['get'])
    def defaulters_trend(self, request):
        overdue = Payment.objects.filter(
            is_deleted=False,
            deadline__lt=timezone.now(),
            status__in=['pending', 'processing', 'failed'],
        )
        return self._volume_series(request, overdue, 'deadline', Count('payment_id'))

    @extend_schema(
        summary="Get top users by activity",
        description="Get top users ranked by transaction count, payment amount, and parcel ownership.",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('period', OpenApiTypes.STR),
            OpenApiParameter('start_date', OpenApiTypes.DATE),
            OpenApiParameter('end_date', OpenApiTypes.DATE),
            OpenApiParameter('limit', OpenApiTypes.INT, description='Number of top users to return', default=10),
        ],
        responses={200: TopUserSerializer(many=True)},
    )
    @action(detail=False, methods=['get'])
    def top_users(self, request):
        """Get top users by activity"""
        start_date, end_date = self._get_date_range(request)
        try:
            limit = min(max(int(request.query_params.get('limit', 10)), 1), 100)
        except ValueError:
            raise ValidationError({'limit': 'Must be an integer'})
        
        # Get users with aggregated stats
        completed_total = Payment.objects.filter(
            user=OuterRef('pk'),
            status='completed',
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        ).values('user').annotate(total=Sum('amount')).values('total')

        users = User.objects.filter(
            is_deleted=False
        ).annotate(
            transaction_count=Count('accounts__ledger_entries', distinct=True, filter=Q(
                accounts__ledger_entries__created_at__date__gte=start_date,
                accounts__ledger_entries__created_at__date__lte=end_date
            )),
            total_amount=Subquery(completed_total, output_field=DecimalField(max_digits=20, decimal_places=2)),
            parcel_count=Count('parcels', distinct=True, filter=Q(parcels__is_deleted=False)),
            account_count=Count('accounts', distinct=True, filter=Q(accounts__is_deleted=False))
        ).order_by('-transaction_count')[:limit]
        
        result = [{
            'user_id': user.user_id,
            'username': user.username,
            'transaction_count': user.transaction_count,
            'total_amount': user.total_amount or Decimal('0.00'),
            'parcel_count': user.parcel_count,
            'account_count': user.account_count,
        } for user in users]
        
        serializer = TopUserSerializer(result, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Get comprehensive system report",
        description="Generate a comprehensive report combining all report types into a single response.",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('period', OpenApiTypes.STR),
            OpenApiParameter('start_date', OpenApiTypes.DATE),
            OpenApiParameter('end_date', OpenApiTypes.DATE),
        ],
        responses={200: ComprehensiveReportSerializer},
    )
    @action(detail=False, methods=['get'])
    def comprehensive(self, request):
        """Generate comprehensive system report"""
        # Reuse other report methods
        users_data = self.users(request).data
        accounts_data = self.accounts(request).data
        payments_data = self.payments(request).data
        parcels_data = self.parcels(request).data
        ledger_data = self.ledger(request).data
        volume_data = self.transaction_volume(request).data
        top_users_data = self.top_users(request).data
        
        start_date, end_date = self._get_date_range(request)
        period = request.query_params.get('period', 'month')
        
        report = {
            'period': f"{period} ({start_date} to {end_date})",
            'generated_at': timezone.now(),
            'users': users_data,
            'accounts': accounts_data,
            'payments': payments_data,
            'parcels': parcels_data,
            'ledger': ledger_data,
            'transaction_volume': volume_data,
            'top_users': top_users_data,
        }
        
        serializer = ComprehensiveReportSerializer(report)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Export report to CSV",
        description="Export any report type to CSV format.",
        tags=['Reports'],
        parameters=[
            OpenApiParameter('report_type', OpenApiTypes.STR, required=True, description='Report type: users, accounts, payments, parcels, ledger'),
            OpenApiParameter('period', OpenApiTypes.STR),
            OpenApiParameter('start_date', OpenApiTypes.DATE),
            OpenApiParameter('end_date', OpenApiTypes.DATE),
        ],
    )
    @action(detail=False, methods=['get'])
    def export(self, request):
        """Export report to CSV"""
        import csv
        from django.http import HttpResponse
        
        report_type = request.query_params.get('report_type', 'users')
        
        # Get the appropriate report data
        if report_type == 'users':
            data = self.users(request).data
        elif report_type == 'accounts':
            data = self.accounts(request).data
        elif report_type == 'payments':
            data = self.payments(request).data
        elif report_type == 'parcels':
            data = self.parcels(request).data
        elif report_type == 'ledger':
            data = self.ledger(request).data
        else:
            return Response({'error': 'Invalid report type'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{report_type}_report_{timezone.now().date()}.csv"'
        
        writer = csv.writer(response)
        
        # Write headers and data based on report type
        if isinstance(data, dict):
            writer.writerow(data.keys())
            writer.writerow(data.values())
        
        return response

COUNTY_NAMES = (
    'nyeri', 'nairobi', 'kiambu', 'nakuru', 'mombasa', 'kisumu', 'machakos',
    'kilifi', 'uasin gishu', 'meru', 'embu', "murang'a", 'kajiado', 'laikipia',
    'garissa', 'kakamega', 'kisii', 'narok', 'kericho', 'bungoma', 'turkana',
)
MULTI_COUNTY_PHRASES = ('all counties', 'every county', 'across counties', 'national', 'countrywide', 'all county', 'other counties')


def _ndjson(events):
    response = StreamingHttpResponse(
        (json.dumps(event, default=str) + '\n' for event in events),
        content_type='application/x-ndjson',
    )
    response['X-Accel-Buffering'] = 'no'
    response['Cache-Control'] = 'no-cache'
    return response


class LLMQueryView(APIView):
    """Answers from county-scoped data tools, then streams the model's commentary."""
    permission_classes = [IsAdminOrAuditor]
    serializer_class = LLMQuerySerializer

    @extend_schema(
        summary="Ask the E-Rates assistant",
        description="Streams newline-delimited JSON: {text, sources} with the data tables first, then {text} deltas, or {error}.",
        tags=['LLM'],
        request=LLMQuerySerializer,
        responses={
            200: OpenApiResponse(description="application/x-ndjson stream"),
            400: OpenApiResponse(description="Invalid request"),
        },
    )
    def post(self, request):
        from . import assistant

        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data['query']
        query_clean = query.strip().lower()

        is_superadmin = bool(request.user.is_superuser or getattr(request.user, 'role', None) == 'owner')
        user_county = (getattr(request.user, 'county', None) or '').strip().title() or None
        detected_county = next(
            (name.title() for name in COUNTY_NAMES if re.search(rf'\b{re.escape(name)}\b', query_clean)),
            None,
        )

        if is_superadmin:
            county = detected_county
        elif not user_county:
            return _ndjson([{'text': (
                "⚠️ **Access Restricted**: Your user account is not assigned to a county jurisdiction. "
                "Please contact the platform super administrator."
            )}])
        elif detected_county and detected_county.lower() != user_county.lower():
            return _ndjson([{'text': (
                f"⛔ **Access Denied: Cross-County Restriction**\n\n"
                f"You are authenticated as an official of **{user_county} County**. "
                f"Under statutory county data protection policies, you are not authorized to query or view records for **{detected_county} County**.\n\n"
                f"- **Your Authorized County:** {user_county}\n"
                f"- **Attempted County:** {detected_county}\n"
                f"- Cross-county and national queries can only be executed by platform super administrators."
            )}])
        elif any(phrase in query_clean for phrase in MULTI_COUNTY_PHRASES):
            return _ndjson([{'text': (
                f"⛔ **Access Denied: Multi-County Restriction**\n\n"
                f"You are authorized to access data for **{user_county} County** only. "
                f"Platform-wide aggregations across all counties are restricted to platform super administrators."
            )}])
        else:
            county = user_county

        return _ndjson(assistant.stream(
            query,
            history=serializer.validated_data.get('history'),
            county=county,
            user=request.user,
        ))


class ParcelDeletionRequestViewSet(mixins.ListModelMixin,
                                   mixins.RetrieveModelMixin,
                                   viewsets.GenericViewSet):
    """Deletion requests officials raised. Officials see their county's; owners see and decide all."""

    queryset = (
        ParcelDeletionRequest.objects.filter(is_deleted=False)
        .select_related('parcel', 'requested_by', 'reviewed_by')
    )
    serializer_class = ParcelDeletionRequestSerializer
    permission_classes = [IsAdminOrAuditor]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status']
    ordering_fields = ['created_at']

    def get_queryset(self):
        county = scope_county(self.request)
        qs = self.queryset
        return qs.filter(parcel__county__iexact=county) if county else qs

    def _decide(self, request, approve: bool):
        deletion_request = self.get_object()
        if deletion_request.status != 'pending':
            return Response(
                {'error': f'This request was already {deletion_request.status}.'},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = ParcelDeletionDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = serializer.validated_data.get('decision_note', '')

        parcel = deletion_request.parcel
        if approve:
            # Re-check: a payment may have landed while the request sat in the queue.
            tier, explanation = parcel_deletion.classify(parcel)
            if tier == parcel_deletion.PROTECTED:
                return Response({'error': explanation}, status=status.HTTP_409_CONFLICT)
            parcel.soft_delete()

        deletion_request.status = 'approved' if approve else 'rejected'
        deletion_request.reviewed_by = request.user
        deletion_request.reviewed_at = timezone.now()
        deletion_request.decision_note = note
        deletion_request.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'decision_note', 'updated_at'])

        audit.record(
            'parcel.deletion_approved' if approve else 'parcel.deletion_rejected',
            obj=parcel,
            parcel_ref=parcel.parcel_ref,
            requested_by=deletion_request.requested_by.username,
            reason=deletion_request.reason,
            decision_note=note,
        )
        return Response(self.get_serializer(deletion_request).data)

    @extend_schema(
        summary="Approve a deletion request and remove the parcel",
        tags=['Parcels'],
        request=ParcelDeletionDecisionSerializer,
        responses={200: ParcelDeletionRequestSerializer},
    )
    @action(detail=True, methods=['post'], permission_classes=[IsPlatformOwner])
    def approve(self, request, pk=None):
        return self._decide(request, approve=True)

    @extend_schema(
        summary="Reject a deletion request and keep the parcel",
        tags=['Parcels'],
        request=ParcelDeletionDecisionSerializer,
        responses={200: ParcelDeletionRequestSerializer},
    )
    @action(detail=True, methods=['post'], permission_classes=[IsPlatformOwner])
    def reject(self, request, pk=None):
        return self._decide(request, approve=False)
