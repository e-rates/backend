from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Sum, Count, Avg, Q, F, Max, Min
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiExample,
    OpenApiResponse,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.openapi import AutoSchema
from rest_framework import serializers as drf_serializers

from .models import (
    User,
    Account,
    Parcel,
    ParcelHistory,
    LedgerEntry,
    Payment,
    AuditLog,
)
from .serializers import (
    
    UserListSerializer,
    UserDetailSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
    
    AccountSerializer,
    AccountListSerializer,
   
    ParcelSerializer,
    ParcelListSerializer,
    
    ParcelHistorySerializer,
    
    LedgerEntrySerializer,
    LedgerEntryCreateSerializer,
    
    PaymentSerializer,
    PaymentCreateSerializer,
   
    AuditLogSerializer,
    
    AccountSummarySerializer,
    UserActivitySerializer,

    #reports serializers
    DateRangeSerializer,
    UserReportSerializer,
    AccountReportSerializer,
    PaymentReportSerializer,
    ParcelReportSerializer,
    LedgerReportSerializer,
    ComprehensiveReportSerializer,
    TransactionVolumeSerializer,
    TopUserSerializer,
    RevenueReportSerializer,
    
    #defaulters serializers
    DefaulterSerializer,
    DefaultersSummarySerializer,
)

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
        
        if request.user.role == 'admin':
            return True

        if hasattr(obj, 'owner_user'):
            return obj.owner_user == request.user
        if hasattr(obj, 'user'):
            return obj.user == request.user
        
        return False


class IsAdminOrAuditor(permissions.BasePermission):
    """
    Only admins and auditors can access.
    """
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            hasattr(request.user, 'role') and
            request.user.role in ['admin', 'auditor']
        )


class IsOwnerOrAdmin(permissions.BasePermission):
    """
    Owner or admin only.
    """
    def has_object_permission(self, request, view, obj):
        if not hasattr(request.user, 'role'):
            return False
        
        if request.user.role == 'admin':
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
    queryset = User.objects.all().order_by('-created_at')
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_verified', 'is_active', 'role']
    search_fields = ['username', 'email']
    ordering_fields = ['created_at', 'updated_at', 'username']
    
    def get_serializer_class(self):
        """Select appropriate serializer based on action"""
        if self.action == 'list':
            return UserListSerializer
        elif self.action == 'create':
            return UserCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return UserUpdateSerializer
        else:  
            return UserDetailSerializer
    
    def get_permissions(self):
        """Dynamic permissions based on action"""
        if self.action == 'create':
            permission_classes = [permissions.AllowAny]
        elif self.action in ['update', 'partial_update', 'destroy']:
            permission_classes = [permissions.IsAuthenticated, IsOwnerOrAdmin]
        else:
            permission_classes = [permissions.IsAuthenticated]
        
        return [permission() for permission in permission_classes]
    
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
    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        """Get current authenticated user profile"""
        serializer = UserDetailSerializer(request.user, context={'request': request})
        return Response(serializer.data)
    
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
    queryset = Parcel.objects.select_related('owner_user').all()
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
        """Filter parcels based on user role and query params"""
        queryset = self.queryset
        user = self.request.user
        user_role = getattr(user, 'role', 'user')
        
        # Admins and auditors see all
        if user_role in ['admin', 'auditor']:
            return queryset
        
        return queryset
    
    def get_serializer_context(self):
        """Pass request context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def perform_create(self, serializer):
        """Auto-assign owner and create history record"""
        parcel = serializer.save(owner_user=self.request.user)
        
    
    def perform_update(self, serializer):
        """Update parcel and create history record"""
        parcel = serializer.save()
        
    
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
    @action(detail=True, methods=['post'], permission_classes=[IsAdminOrAuditor])
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
    @action(detail=False, methods=['post'], permission_classes=[IsAdminOrAuditor])
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
        users = User.objects.filter(is_active=True, is_deleted=False)
        
        search = request.query_params.get('search')
        if search:
            users = users.filter(
                Q(username__icontains=search) |
                Q(email__icontains=search) |
                Q(phone__icontains=search)
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
    @action(detail=False, methods=['post'], permission_classes=[IsAdminOrAuditor])
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
    def unassigned(self, request):
        """Get parcels without owners"""
        parcels = Parcel.objects.filter(owner_user__isnull=True, is_deleted=False)
        
        # Apply filters
        county = request.query_params.get('county')
        if county:
            parcels = parcels.filter(props__county=county)
        
        sub_county = request.query_params.get('sub_county')
        if sub_county:
            parcels = parcels.filter(props__sub_county=sub_county)
        
        ward = request.query_params.get('ward')
        if ward:
            parcels = parcels.filter(props__ward=ward)
        
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
    @action(detail=True, methods=['post'])
    def transfer(self, request, pk=None):
        """Transfer parcel ownership (use assign_owner instead)"""
        # Redirect to assign_owner
        return self.assign_owner(request, pk)
    
    @extend_schema(
        summary="Get all parcels as GeoJSON FeatureCollection",
        description="Retrieve all parcels as a single GeoJSON FeatureCollection optimized for Leaflet visualization. Supports filtering and bbox queries.",
        tags=['Parcels'],
        parameters=[
            OpenApiParameter('status', OpenApiTypes.STR, description='Filter by status (active, disputed, transferred, archived)'),
            OpenApiParameter('owner', OpenApiTypes.UUID, description='Filter by owner user ID'),
            OpenApiParameter('bbox', OpenApiTypes.STR, description='Bounding box filter: min_lon,min_lat,max_lon,max_lat'),
            OpenApiParameter('simplify', OpenApiTypes.FLOAT, description='Simplify geometries (tolerance in degrees, e.g., 0.0001)', default=0),
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
        Return all parcels as a single GeoJSON FeatureCollection.
        Optimized for frontend map visualization with Leaflet.
        """
        import json
        from django.contrib.gis.geos import Polygon as GEOSPolygon
        
        # Start with base queryset
        queryset = self.get_queryset().filter(is_deleted=False)
        
        # Apply filters
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        owner_filter = request.query_params.get('owner')
        if owner_filter:
            queryset = queryset.filter(owner_user_id=owner_filter)
        
        # Location filters (county, sub_county, ward)
        county_filter = request.query_params.get('county')
        if county_filter:
            queryset = queryset.filter(props__county=county_filter)
        
        sub_county_filter = request.query_params.get('sub_county')
        if sub_county_filter:
            queryset = queryset.filter(props__sub_county=sub_county_filter)
        
        ward_filter = request.query_params.get('ward')
        if ward_filter:
            queryset = queryset.filter(props__ward=ward_filter)
        
        # Bounding box filter (format: min_lon,min_lat,max_lon,max_lat)
        bbox = request.query_params.get('bbox')
        if bbox:
            try:
                min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(','))
                bbox_polygon = GEOSPolygon.from_bbox((min_lon, min_lat, max_lon, max_lat))
                queryset = queryset.filter(geom__intersects=bbox_polygon)
            except (ValueError, TypeError):
                return Response(
                    {'error': 'Invalid bbox format. Use: min_lon,min_lat,max_lon,max_lat'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Simplification tolerance (for performance with complex geometries)
        simplify_tolerance = request.query_params.get('simplify', 0)
        try:
            simplify_tolerance = float(simplify_tolerance)
        except (ValueError, TypeError):
            simplify_tolerance = 0
        
        # Select only needed fields for performance
        queryset = queryset.select_related('owner_user').only(
            'parcel_id', 'parcel_ref', 'geom', 'centroid', 
            'area_m2', 'status', 'props', 'owner_user__username',
            'created_at', 'updated_at'
        )
        
        # Build GeoJSON FeatureCollection
        features = []
        for parcel in queryset:
            # Get geometry
            geom = parcel.geom
            
            # Apply simplification if requested
            if simplify_tolerance > 0 and geom:
                geom = geom.simplify(tolerance=simplify_tolerance, preserve_topology=True)
            
            # Convert to GeoJSON dict
            # Use geom.json to properly serialize geometry to GeoJSON
            geometry_dict = None
            if geom:
                try:
                    geometry_dict = json.loads(geom.json)
                except Exception:
                    geometry_dict = None
            
            feature = {
                'type': 'Feature',
                'id': str(parcel.parcel_id),
                'geometry': geometry_dict,
                'properties': {
                    'parcel_ref': parcel.parcel_ref,
                    'owner_username': parcel.owner_user.username if parcel.owner_user else None,
                    'owner_id': str(parcel.owner_user.user_id) if parcel.owner_user else None,
                    'area_m2': float(parcel.area_m2) if parcel.area_m2 else None,
                    'area_acres': round(float(parcel.area_m2) / 4046.86, 2) if parcel.area_m2 else None,
                    'status': parcel.status,
                    'centroid': {
                        'lat': float(parcel.centroid.y) if parcel.centroid else None,
                        'lng': float(parcel.centroid.x) if parcel.centroid else None,
                    } if parcel.centroid else None,
                    'created_at': parcel.created_at.isoformat() if parcel.created_at else None,
                    'updated_at': parcel.updated_at.isoformat() if parcel.updated_at else None,
                    # Include custom properties from shapefile
                    'custom_props': parcel.props or {},
                }
            }
            features.append(feature)
        
        # Return FeatureCollection
        geojson = {
            'type': 'FeatureCollection',
            'features': features,
            'count': len(features),
        }
        
        return Response(geojson)
    
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
    @action(detail=False, methods=['post'], permission_classes=[IsAdminOrAuditor])
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
    queryset = Payment.objects.select_related('user', 'account').all()
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'processor', 'currency', 'user', 'account']
    ordering_fields = ['created_at', 'updated_at', 'amount']
    permission_classes = [permissions.IsAuthenticated]
    
    def get_serializer_class(self):
        """Select appropriate serializer"""
        if self.action == 'create':
            return PaymentCreateSerializer
        return PaymentSerializer
    
    def get_queryset(self):
        """Filter payments based on user role"""
        user = self.request.user
        user_role = getattr(user, 'role', 'user')
        
        # Admins and auditors see all
        if user_role in ['admin', 'auditor']:
            return self.queryset
        
        # Regular users see only their payments
        return self.queryset.filter(user=user)
    
    def get_serializer_context(self):
        """Pass request context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def perform_create(self, serializer):
        """Create payment with user auto-assignment"""
        serializer.save(user=self.request.user, status='pending')
    
    @extend_schema(
        summary="Confirm payment",
        description="Confirm a pending payment. Admin/Auditor only.",
        tags=['Payments'],
        responses={
            200: OpenApiResponse(description="Payment confirmed successfully"),
            400: OpenApiResponse(description="Payment cannot be confirmed"),
        },
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAdminOrAuditor])
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
    @action(detail=True, methods=['post'], permission_classes=[IsAdminOrAuditor])
    serializer_class = AuditLogSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['who', 'action', 'object_type']
    search_fields = ['action', 'object_type', 'details']
    ordering_fields = ['created_at']
    permission_classes = [permissions.IsAuthenticated, IsAdminOrAuditor]
    
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
            start_date = request.query_params.get('start_date')
            end_date = request.query_params.get('end_date')
            if start_date:
                start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            if end_date:
                end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
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
        limit = int(request.query_params.get('limit', 10))
        
        # Get users with aggregated stats
        users = User.objects.filter(
            is_deleted=False
        ).annotate(
            transaction_count=Count('accounts__ledger_entries', filter=Q(
                accounts__ledger_entries__created_at__date__gte=start_date,
                accounts__ledger_entries__created_at__date__lte=end_date
            )),
            total_amount=Sum('payments__amount', filter=Q(
                payments__created_at__date__gte=start_date,
                payments__created_at__date__lte=end_date,
                payments__status='completed'
            )),
            parcel_count=Count('parcels', filter=Q(parcels__is_deleted=False)),
            account_count=Count('accounts', filter=Q(accounts__is_deleted=False))
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