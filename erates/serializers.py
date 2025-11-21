from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer
from django.contrib.auth.password_validation import validate_password
from django.core.validators import MinLengthValidator, EmailValidator
from django.utils import timezone
from datetime import datetime, timedelta
from django.db.models import Sum, Count, Avg, Q
from decimal import Decimal
import re
from drf_spectacular.utils import extend_schema_field

from .models import (
    User, Account, Parcel, ParcelHistory, 
    LedgerEntry, Payment, AuditLog
)

class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['user_id', 'username', 'is_verified']
        read_only_fields = fields


class UserDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'user_id', 'username', 'email', 'phone', 'national_id',
            'is_verified', 'is_active', 'role', 'last_login',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'user_id', 'is_verified', 'last_login', 
            'created_at', 'updated_at'
        ]
    
    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        
        if not request:
            return data
        
        user = request.user
        
        # Only show PII to self, admins, or auditors
        user_role = getattr(user, 'role', 'user')  # Default to 'user' if role doesn't exist
        if user != instance and user_role not in ['admin', 'auditor']:
            data.pop('national_id', None)
            data.pop('phone', None)
            data.pop('email', None)
        
        return data


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={'input_type': 'password'},
        help_text="Minimum 8 characters with uppercase, lowercase, and numbers"
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'},
        help_text="Must match password field"
    )
    email = serializers.EmailField(
        required=True,
        validators=[EmailValidator()]
    )
    
    class Meta:
        model = User
        fields = [
            'username', 'email', 'phone', 'national_id',
            'password', 'password_confirm'
        ]
    
    def validate_username(self, value):
        """Ensure username contains only safe characters"""
        if not re.match(r'^[a-zA-Z0-9_-]+$', value):
            raise serializers.ValidationError(
                "Username can only contain letters, numbers, hyphens, and underscores"
            )
        if len(value) < 3:
            raise serializers.ValidationError(
                "Username must be at least 3 characters long"
            )
        return value.lower()
    
    def validate_phone(self, value):
        if value:
            cleaned = re.sub(r'[\s\-\(\)]', '', value)
            if not re.match(r'^\+?[0-9]{10,15}$', cleaned):
                raise serializers.ValidationError(
                    "Enter a valid phone number (10-15 digits)"
                )
            return cleaned
        return value
    
    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({
                "password_confirm": "Password fields didn't match."
            })
        attrs.pop('password_confirm')
        return attrs
    
    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)  # Uses Argon2
        user.save()
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    current_password = serializers.CharField(
        write_only=True,
        required=False,
        style={'input_type': 'password'}
    )
    new_password = serializers.CharField(
        write_only=True,
        required=False,
        validators=[validate_password],
        style={'input_type': 'password'}
    )
    new_password_confirm = serializers.CharField(
        write_only=True,
        required=False,
        style={'input_type': 'password'}
    )
    
    class Meta:
        model = User
        fields = [
            'email', 'phone', 'current_password',
            'new_password', 'new_password_confirm'
        ]
    
    def validate(self, attrs):
        if 'new_password' in attrs:
            if 'current_password' not in attrs:
                raise serializers.ValidationError({
                    "current_password": "Current password required to set new password"
                })
            
            if attrs['new_password'] != attrs.get('new_password_confirm'):
                raise serializers.ValidationError({
                    "new_password_confirm": "New passwords didn't match"
                })
           
            if not self.instance.check_password(attrs['current_password']):
                raise serializers.ValidationError({
                    "current_password": "Current password is incorrect"
                })
        
        return attrs
    
    def update(self, instance, validated_data):
        """Update user with special handling for password and email"""
        current_password = validated_data.pop('current_password', None)
        new_password = validated_data.pop('new_password', None)
        validated_data.pop('new_password_confirm', None)
        
    
        if 'email' in validated_data and validated_data['email'] != instance.email:
            instance.is_verified = False
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        if new_password:
            instance.set_password(new_password)
        
        instance.save()
        return instance


class AccountSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source='owner_user.username', read_only=True)
    balance_verified = serializers.SerializerMethodField()
    
    class Meta:
        model = Account
        fields = [
            'account_id', 'owner_user', 'owner_username', 'account_type',
            'currency', 'current_balance', 'balance_verified', 'status',
            'metadata', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'account_id', 'current_balance', 'balance_verified',
            'created_at', 'updated_at'
        ]
    
    @extend_schema_field(serializers.BooleanField)
    def get_balance_verified(self, obj) -> bool:
        return obj.verify_balance_integrity()
    
    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        
        if request and request.user.role not in ['admin', 'auditor']:
            if request.user.user_id != instance.owner_user_id:
                data.pop('owner_user', None)
                data.pop('metadata', None)
        
        return data


class AccountListSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source='owner_user.username', read_only=True)
    
    class Meta:
        model = Account
        fields = [
            'account_id', 'owner_username', 'account_type',
            'currency', 'current_balance', 'status'
        ]
        read_only_fields = fields


class ParcelSerializer(GeoFeatureModelSerializer):
 
    owner_username = serializers.SerializerMethodField()
    owner_email = serializers.SerializerMethodField()
    
    def get_owner_username(self, obj):
        return obj.owner_user.username if obj.owner_user else None
    
    def get_owner_email(self, obj):
        return obj.owner_user.email if obj.owner_user else None
    
    class Meta:
        model = Parcel
        geo_field = 'geom'
        id_field = 'parcel_id'
        fields = [
            'parcel_id', 'owner_user', 'owner_username', 'owner_email', 'parcel_ref',
            'geom', 'centroid', 'area_m2', 'status', 'county', 'sub_county', 'ward', 'props',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'parcel_id', 'centroid', 'area_m2',
            'created_at', 'updated_at'
        ]
    
    def validate_status(self, value):
        if self.instance:
            old_status = self.instance.status
            allowed_transitions = {
                'active': ['disputed', 'transferred'],
                'disputed': ['active', 'archived'],
                'transferred': ['archived'],
            }
            
            if old_status in allowed_transitions:
                if value not in allowed_transitions[old_status] and value != old_status:
                    raise serializers.ValidationError(
                        f"Cannot transition from {old_status} to {value}"
                    )
        
        return value


class ParcelListSerializer(serializers.ModelSerializer):
    """List serializer without geometry for better performance"""
    owner_username = serializers.SerializerMethodField()
    owner_id = serializers.SerializerMethodField()
    
    def get_owner_username(self, obj):
        return obj.owner_user.username if obj.owner_user else None
    
    def get_owner_id(self, obj):
        return str(obj.owner_user.user_id) if obj.owner_user else None
    
    class Meta:
        model = Parcel
        fields = [
            'parcel_id', 'owner_id', 'owner_username', 'parcel_ref',
            'area_m2', 'status', 'county', 'sub_county', 'ward'
        ]
        read_only_fields = fields



class ParcelHistorySerializer(GeoFeatureModelSerializer):
    
    parcel_ref = serializers.CharField(source='parcel.parcel_ref', read_only=True)
    owner_username = serializers.CharField(source='owner_user.username', read_only=True)
    changed_by_username = serializers.CharField(source='changed_by.username', read_only=True)
    
    class Meta:
        model = ParcelHistory
        geo_field = 'geom'
        id_field = 'history_id'
        fields = [
            'history_id', 'parcel', 'parcel_ref', 'owner_user', 'owner_username',
            'geom', 'area_m2', 'changed_by', 'changed_by_username',
            'change_reason', 'change_ts'
        ]
        read_only_fields = fields  # Immutable


class LedgerEntrySerializer(serializers.ModelSerializer):
    account_type = serializers.CharField(source='account.account_type', read_only=True)
    integrity_verified = serializers.SerializerMethodField()
    
    class Meta:
        model = LedgerEntry
        fields = [
            'entry_id', 'account', 'account_type', 'related_account_id',
            'amount', 'balance_after', 'entry_type', 'external_ref',
            'integrity_verified', 'metadata', 'created_at'
        ]
        read_only_fields = fields  # Ledger entries are immutable
    
    @extend_schema_field(serializers.BooleanField)
    def get_integrity_verified(self, obj) -> bool:
        """Verify entry integrity"""
        return obj.verify_integrity()
    
    def to_representation(self, instance):
        """Remove internal fields from response"""
        data = super().to_representation(instance)
        request = self.context.get('request')

        if request and request.user.role not in ['admin', 'auditor']:
            data.pop('related_account_id', None)
            data.pop('external_ref', None)
        
        return data


class LedgerEntryCreateSerializer(serializers.Serializer):
    account = serializers.UUIDField()
    related_account = serializers.UUIDField(required=False)
    amount = serializers.DecimalField(max_digits=20, decimal_places=2)
    entry_type = serializers.ChoiceField(choices=[
        'credit', 'debit', 'transfer_in', 'transfer_out', 'fee', 'refund'
    ])
    external_ref = serializers.CharField(max_length=255, required=False)
    idempotency_key = serializers.CharField(max_length=255, required=True)
    metadata = serializers.JSONField(required=False)
    
    def validate_amount(self, value):
        """Ensure amount is positive"""
        if value <= 0:
            raise serializers.ValidationError("Amount must be positive")
        return value




class PaymentSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)
    account_type = serializers.CharField(source='account.account_type', read_only=True)
    integrity_verified = serializers.SerializerMethodField()
    is_defaulter = serializers.SerializerMethodField()
    days_overdue = serializers.SerializerMethodField()
    
    class Meta:
        model = Payment
        fields = [
            'payment_id', 'user', 'user_username', 'account', 'account_type',
            'amount', 'currency', 'processor', 'status', 'deadline', 
            'is_defaulter', 'days_overdue', 'integrity_verified',
            'failure_reason', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'payment_id', 'integrity_verified', 'is_defaulter', 
            'days_overdue', 'created_at', 'updated_at'
        ]
    
    @extend_schema_field(serializers.BooleanField)
    def get_integrity_verified(self, obj) -> bool:
        """Verify payment integrity"""
        return obj.verify_integrity()
    
    @extend_schema_field(serializers.BooleanField)
    def get_is_defaulter(self, obj) -> bool:
        """Check if payment is overdue"""
        return obj.is_defaulter()
    
    @extend_schema_field(serializers.IntegerField)
    def get_days_overdue(self, obj):
        """Get number of days overdue"""
        return obj.days_overdue()
    
    def to_representation(self, instance):
        """Hide sensitive processor details"""
        data = super().to_representation(instance)
        request = self.context.get('request')

        if request and request.user.role not in ['admin', 'auditor']:
            if request.user.user_id != instance.user_id:
                data.pop('processor', None)
        
        return data


class PaymentCreateSerializer(serializers.ModelSerializer):
    idempotency_key = serializers.CharField(max_length=255, required=True)
    
    class Meta:
        model = Payment
        fields = [
            'user', 'account', 'amount', 'currency',
            'processor', 'deadline', 'idempotency_key', 'metadata'
        ]
    
    def validate_amount(self, value):
        """Ensure amount is positive and within limits"""
        if value <= 0:
            raise serializers.ValidationError("Amount must be positive")
        if value > Decimal('10000000.00'):  # 10 million limit
            raise serializers.ValidationError("Amount exceeds maximum limit")
        return value
    
    def validate_currency(self, value):
        """Validate currency code"""
        allowed_currencies = ['KES', 'USD', 'EUR', 'GBP']
        if value not in allowed_currencies:
            raise serializers.ValidationError(
                f"Currency must be one of: {', '.join(allowed_currencies)}"
            )
        return value

class AuditLogSerializer(serializers.ModelSerializer):

    who_username = serializers.CharField(source='who.username', read_only=True)
    
    class Meta:
        model = AuditLog
        fields = [
            'audit_id', 'who', 'who_username', 'action',
            'object_type', 'object_id', 'ip_address',
            'details', 'created_at'
        ]
        read_only_fields = fields  # Completely immutable
    
    def to_representation(self, instance):
        """Hide sensitive details based on permissions"""
        data = super().to_representation(instance)
        request = self.context.get('request')
        
        # Only admins and auditors see full details
        if request and request.user.role not in ['admin', 'auditor']:
            data.pop('ip_address', None)
            data.pop('details', None)
        
        return data


class AccountSummarySerializer(serializers.Serializer):
    """Account summary for dashboard/reporting"""
    total_accounts = serializers.IntegerField()
    total_balance = serializers.DecimalField(max_digits=20, decimal_places=2)
    active_accounts = serializers.IntegerField()
    frozen_accounts = serializers.IntegerField()


class UserActivitySerializer(serializers.Serializer):
    """User activity summary"""
    user_id = serializers.UUIDField()
    username = serializers.CharField()
    total_transactions = serializers.IntegerField()
    total_parcels = serializers.IntegerField()
    last_activity = serializers.DateTimeField()

class DateRangeSerializer(serializers.Serializer):
    """Base serializer for date range filtering"""
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    period = serializers.ChoiceField(
        choices=['today', 'week', 'month', 'quarter', 'year', 'custom'],
        required=False,
        default='month'
    )
    
    def validate(self, attrs):
        if attrs.get('period') == 'custom':
            if not attrs.get('start_date') or not attrs.get('end_date'):
                raise serializers.ValidationError(
                    "start_date and end_date are required for custom period"
                )
        return attrs
    
class UserReportSerializer(serializers.Serializer):
    """User statistics report"""
    total_users = serializers.IntegerField()
    active_users = serializers.IntegerField()
    verified_users = serializers.IntegerField()
    users_by_role = serializers.DictField()
    new_users_count = serializers.IntegerField()
    locked_accounts = serializers.IntegerField()
    users_with_parcels = serializers.IntegerField()
    users_with_accounts = serializers.IntegerField()

class AccountReportSerializer(serializers.Serializer):
    """Account statistics report"""
    total_accounts = serializers.IntegerField()
    active_accounts = serializers.IntegerField()
    frozen_accounts = serializers.IntegerField()
    closed_accounts = serializers.IntegerField()
    total_balance = serializers.DecimalField(max_digits=20, decimal_places=2)
    average_balance = serializers.DecimalField(max_digits=20, decimal_places=2)
    accounts_by_type = serializers.DictField()
    accounts_by_currency = serializers.DictField()

class PaymentReportSerializer(serializers.Serializer):
    """Payment statistics report"""
    total_payments = serializers.IntegerField()
    completed_payments = serializers.IntegerField()
    pending_payments = serializers.IntegerField()
    failed_payments = serializers.IntegerField()
    refunded_payments = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=20, decimal_places=2)
    average_payment = serializers.DecimalField(max_digits=20, decimal_places=2)
    payments_by_processor = serializers.DictField()
    payments_by_currency = serializers.DictField()
    success_rate = serializers.FloatField()


class ParcelReportSerializer(serializers.Serializer):
    """Parcel statistics report"""
    total_parcels = serializers.IntegerField()
    active_parcels = serializers.IntegerField()
    disputed_parcels = serializers.IntegerField()
    transferred_parcels = serializers.IntegerField()
    archived_parcels = serializers.IntegerField()
    total_area_m2 = serializers.FloatField()
    average_area_m2 = serializers.FloatField()
    parcels_by_owner = serializers.ListField()

class LedgerReportSerializer(serializers.Serializer):
    """Ledger statistics report"""
    total_entries = serializers.IntegerField()
    total_credits = serializers.DecimalField(max_digits=20, decimal_places=2)
    total_debits = serializers.DecimalField(max_digits=20, decimal_places=2)
    total_transfers = serializers.DecimalField(max_digits=20, decimal_places=2)
    entries_by_type = serializers.DictField()
    entries_by_account_type = serializers.DictField()

class TransactionVolumeSerializer(serializers.Serializer):
    """Transaction volume over time"""
    date = serializers.DateField()
    count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=20, decimal_places=2)
    avg_amount = serializers.DecimalField(max_digits=20, decimal_places=2)


class TopUserSerializer(serializers.Serializer):
    """Top users by activity"""
    user_id = serializers.UUIDField()
    username = serializers.CharField()
    transaction_count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=20, decimal_places=2)
    parcel_count = serializers.IntegerField()
    account_count = serializers.IntegerField()

class RevenueReportSerializer(serializers.Serializer):
    """Revenue and fee report"""
    total_revenue = serializers.DecimalField(max_digits=20, decimal_places=2)
    total_fees = serializers.DecimalField(max_digits=20, decimal_places=2)
    revenue_by_period = serializers.ListField()
    revenue_by_source = serializers.DictField()


class ComprehensiveReportSerializer(serializers.Serializer):
    """Comprehensive system report"""
    period = serializers.CharField()
    generated_at = serializers.DateTimeField()
    users = UserReportSerializer()
    accounts = AccountReportSerializer()
    payments = PaymentReportSerializer()
    parcels = ParcelReportSerializer()
    ledger = LedgerReportSerializer()
    transaction_volume = serializers.ListField(child=TransactionVolumeSerializer())
    top_users = serializers.ListField(child=TopUserSerializer())


class DefaulterSerializer(serializers.Serializer):
    """Serializer for defaulters - users with overdue payments"""
    user_id = serializers.UUIDField()
    username = serializers.CharField()
    email = serializers.EmailField()
    phone = serializers.CharField(required=False, allow_null=True)
    payment_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=20, decimal_places=2)
    currency = serializers.CharField()
    deadline = serializers.DateTimeField()
    days_overdue = serializers.IntegerField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    metadata = serializers.JSONField(required=False, allow_null=True)
    parcels = serializers.ListField(child=serializers.DictField(), required=False)  # ADD THIS LINE



class DefaultersSummarySerializer(serializers.Serializer):
    """Summary statistics for defaulters"""
    total_defaulters = serializers.IntegerField()
    total_overdue_amount = serializers.DecimalField(max_digits=20, decimal_places=2)
    average_days_overdue = serializers.FloatField()
    defaulters_by_currency = serializers.DictField()
    defaulters = serializers.ListField(child=DefaulterSerializer())

