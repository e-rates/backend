from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q

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
)


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


class UserViewSet(viewsets.ModelViewSet):
    
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
    
    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        """Get current authenticated user profile"""
        serializer = UserDetailSerializer(request.user, context={'request': request})
        return Response(serializer.data)
    
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



class AccountViewSet(viewsets.ModelViewSet):
    
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
    
    @action(detail=True, methods=['get'])
    def transactions(self, request, pk=None):
        """Get account transactions"""
        account = self.get_object()
        entries = LedgerEntry.objects.filter(account=account).order_by('-created_at')
        serializer = LedgerEntrySerializer(entries, many=True, context={'request': request})
        return Response(serializer.data)


class ParcelViewSet(viewsets.ModelViewSet):
    
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
        
    
    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        """Get parcel change history"""
        parcel = self.get_object()
        history = ParcelHistory.objects.filter(parcel=parcel).order_by('-change_ts')
        serializer = ParcelHistorySerializer(history, many=True, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def transfer(self, request, pk=None):
        """Transfer parcel ownership"""
        parcel = self.get_object()
        new_owner_id = request.data.get('new_owner_id')
        
        if not new_owner_id:
            return Response(
                {'error': 'new_owner_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            new_owner = User.objects.get(user_id=new_owner_id)
            parcel.owner_user = new_owner
            parcel.status = 'transferred'
            parcel.save()
            
            ParcelHistory.objects.create(
                parcel=parcel,
                owner_user=new_owner,
                geom=parcel.geom,
                area_m2=parcel.area_m2,
                changed_by=request.user,
                change_reason=f'Ownership transferred to {new_owner.username}'
            )
            
            return Response({
                'message': 'Parcel transferred successfully',
                'parcel_id': parcel.parcel_id,
                'new_owner': new_owner.username
            })
        except User.DoesNotExist:
            return Response(
                {'error': 'New owner not found'},
                status=status.HTTP_404_NOT_FOUND
            )


class ParcelHistoryViewSet(viewsets.ReadOnlyModelViewSet):
  
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



class LedgerEntryViewSet(viewsets.ReadOnlyModelViewSet):
   
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
    
    @action(detail=False, methods=['get'], permission_classes=[IsAdminOrAuditor])
    def verify_chain(self, request):
        """Verify ledger entry chain integrity (admin/auditor only)"""
        # This would implement chain verification logic
        return Response({
            'message': 'Chain verification not yet implemented'
        })



class PaymentViewSet(viewsets.ModelViewSet):
    
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
    
    @action(detail=True, methods=['post'], permission_classes=[IsAdminOrAuditor])
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
        
        return Response({
            'message': 'Payment refunded successfully',
            'payment_id': payment.payment_id,
            'status': payment.status
        })


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    
    queryset = AuditLog.objects.select_related('who').all()
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
