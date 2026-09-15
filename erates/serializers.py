from django.utils import timezone
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer
from django.contrib.auth.password_validation import validate_password
from django.core.validators import EmailValidator
from decimal import Decimal
import re
from drf_spectacular.utils import extend_schema_field

from .models import (
    Conversation,
    RateSchedule,
    Waiver,
    User, Account, County, Parcel, ParcelDeletionRequest, ParcelHistory, 
    LedgerEntry, Payment, AuditLog, phone_lookup_hash
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
            'is_verified', 'is_active', 'role', 'county', 'must_change_password',
            'last_login', 'created_at', 'updated_at'
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
        if user != instance and user_role not in ['admin', 'auditor', 'owner']:
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
            if User.objects.filter(phone_hash=phone_lookup_hash(cleaned)).exists():
                raise serializers.ValidationError("This phone number is already registered to another account")
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


class StaffCreatedUserSerializer(serializers.ModelSerializer):
    """A signed-in official creating an account for someone else."""

    class Meta:
        model = User
        fields = ['user_id', 'username', 'email', 'phone', 'national_id', 'role', 'county']
        read_only_fields = ['user_id']

    def validate_username(self, value):
        if not re.match(r'^[a-zA-Z0-9_-]+$', value or ''):
            raise serializers.ValidationError("Username can only contain letters, numbers, hyphens and underscores")
        if len(value) < 3:
            raise serializers.ValidationError("Username must be at least 3 characters long")
        value = value.lower()
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("That username is taken")
        return value

    def validate_phone(self, value):
        if not value:
            return value
        cleaned = re.sub(r'[\s\-\(\)]', '', value)
        if not re.match(r'^\+?[0-9]{10,15}$', cleaned):
            raise serializers.ValidationError("Enter a valid phone number (10-15 digits)")
        if User.objects.filter(phone_hash=phone_lookup_hash(cleaned)).exists():
            raise serializers.ValidationError("This phone number is already registered to another account")
        return cleaned

    def validate(self, attrs):
        creator = self.context['request'].user
        role = attrs.get('role') or 'user'
        if creator.is_platform_owner:
            if role not in ('admin', 'auditor'):
                raise serializers.ValidationError({'role': 'Platform owners create county officials (admin) or auditors'})
            if not (attrs.get('county') or '').strip():
                raise serializers.ValidationError({'county': 'Choose the county this official works for'})
        else:
            if role != 'user':
                raise serializers.ValidationError({'role': 'County officials can only create land owner accounts'})
            if not creator.county:
                raise serializers.ValidationError({'county': 'Your account has no county set; ask the platform owner to fix it'})
            attrs['county'] = creator.county
        attrs['role'] = role
        return attrs


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
    
    def validate_phone(self, value):
        if not value:
            return value
        cleaned = re.sub(r'[\s\-\(\)]', '', value)
        if not re.match(r'^\+?[0-9]{10,15}$', cleaned):
            raise serializers.ValidationError("Enter a valid phone number (10-15 digits)")
        if User.objects.filter(phone_hash=phone_lookup_hash(cleaned)).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("This phone number is already registered to another account")
        return cleaned

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
        
        if request and request.user.role not in ['admin', 'auditor', 'owner']:
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

        if request and request.user.role not in ['admin', 'auditor', 'owner']:
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
    parcel_refs = serializers.SerializerMethodField()
    parcel_ref = serializers.CharField(source='parcel.parcel_ref', read_only=True, default=None)

    class Meta:
        model = Payment
        fields = [
            'payment_id', 'user', 'user_username', 'account', 'account_type',
            'parcel', 'parcel_ref', 'payment_year',
            'amount', 'currency', 'processor', 'processor_ref', 'status', 'deadline',
            'is_defaulter', 'days_overdue', 'integrity_verified',
            'failure_reason', 'parcel_refs', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'payment_id', 'status', 'processor_ref', 'integrity_verified', 'is_defaulter',
            'days_overdue', 'parcel_refs', 'parcel_ref', 'created_at', 'updated_at'
        ]

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_parcel_refs(self, obj) -> list:
        return [parcel.parcel_ref for parcel in obj.user.parcels.all()]
    
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

        if request and request.user.role not in ['admin', 'auditor', 'owner']:
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
        extra_kwargs = {'user': {'required': False}}

    def validate(self, attrs):
        request = self.context['request']
        account = attrs['account']
        if getattr(request.user, 'role', None) == 'admin':
            user = attrs.setdefault('user', account.owner_user)
        else:
            user = attrs['user'] = request.user
        if account.owner_user_id != user.user_id:
            raise serializers.ValidationError({'account': 'Account does not belong to the paying user'})
        return attrs
    
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

    who_username = serializers.CharField(source='who.username', read_only=True, default=None)
    summary = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    integrity_verified = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            'audit_id', 'who', 'who_username', 'action', 'category', 'summary',
            'object_type', 'object_id', 'ip_address', 'user_agent',
            'details', 'integrity_verified', 'created_at'
        ]
        read_only_fields = fields  # Completely immutable

    def get_summary(self, obj) -> str:
        from .audit import describe
        return describe(obj)

    def get_category(self, obj) -> str:
        return obj.action.split('.', 1)[0]

    def get_integrity_verified(self, obj) -> bool:
        return obj.verify_integrity()
    
    def to_representation(self, instance):
        """Hide sensitive details based on permissions"""
        data = super().to_representation(instance)
        request = self.context.get('request')
        
        # Only admins and auditors see full details
        if request and request.user.role not in ['admin', 'auditor', 'owner']:
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


class LLMQuerySerializer(serializers.Serializer):
    """Serializer for LLM queries"""
    query = serializers.CharField(required=True, help_text="The question to ask the LLM")
    conversation = serializers.UUIDField(required=False, help_text="Continue this conversation; omit to start a new one")



class CountySerializer(serializers.ModelSerializer):
    """Counties the platform owner has onboarded."""
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = County
        fields = [
            'county_id', 'name', 'logo', 'logo_url', 'rates_office_email',
            'rates_office_phone', 'paybill', 'is_active', 'created_at',
        ]
        read_only_fields = ['county_id', 'logo_url', 'created_at']
        extra_kwargs = {'logo': {'write_only': True, 'required': False}}

    @extend_schema_field(serializers.CharField)
    def get_logo_url(self, obj) -> str:
        if not obj.logo:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(obj.logo.url) if request else obj.logo.url

    def validate_name(self, value):
        name = ' '.join(value.split()).title()
        if County.objects.filter(name__iexact=name).exclude(pk=getattr(self.instance, 'pk', None)).exists():
            raise serializers.ValidationError('That county is already on the platform')
        return name


class ParcelDeletionRequestCreateSerializer(serializers.Serializer):
    """What an official sends when asking for a plot to be removed."""
    reason = serializers.CharField(min_length=10, max_length=1000, trim_whitespace=True)


class ParcelDeletionRequestSerializer(serializers.ModelSerializer):
    parcel_ref = serializers.CharField(source='parcel.parcel_ref', read_only=True)
    county = serializers.CharField(source='parcel.county', read_only=True)
    ward = serializers.CharField(source='parcel.ward', read_only=True)
    requested_by_username = serializers.CharField(source='requested_by.username', read_only=True)
    reviewed_by_username = serializers.CharField(source='reviewed_by.username', read_only=True, default=None)

    class Meta:
        model = ParcelDeletionRequest
        fields = [
            'request_id', 'parcel', 'parcel_ref', 'county', 'ward', 'reason', 'status',
            'requested_by', 'requested_by_username', 'reviewed_by', 'reviewed_by_username',
            'reviewed_at', 'decision_note', 'created_at',
        ]
        read_only_fields = fields


class ParcelDeletionDecisionSerializer(serializers.Serializer):
    """The platform owner's note when approving or rejecting."""
    decision_note = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class RateBandSerializer(serializers.Serializer):
    max_ha = serializers.DecimalField(max_digits=10, decimal_places=4, min_value=Decimal('0.0001'))
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0'))


class RateScheduleSerializer(serializers.ModelSerializer):
    """A county's rates for one year: flat area bands, a top flat rate and a site-value percentage."""
    county_name = serializers.CharField(source='county.name', read_only=True)
    bands = RateBandSerializer(many=True)
    set_by_username = serializers.CharField(source='set_by.username', read_only=True, default=None)

    class Meta:
        model = RateSchedule
        fields = [
            'schedule_id', 'county', 'county_name', 'year', 'bands', 'top_amount',
            'usv_rate_percent', 'deadline', 'set_by_username', 'updated_at',
        ]
        read_only_fields = ['schedule_id', 'county', 'county_name', 'set_by_username', 'updated_at']

    def validate_year(self, value):
        current = timezone.now().year
        if not current - 20 <= value <= current + 1:
            raise serializers.ValidationError(f'Rating year must be between {current - 20} and {current + 1}.')
        return value

    def to_representation(self, instance):
        return {**super().to_representation(instance), 'bands': instance.bands}

    def validate_bands(self, value):
        return [
            {'max_ha': format(b['max_ha'].normalize(), 'f'), 'amount': format(b['amount'].normalize(), 'f')}
            for b in sorted(value, key=lambda b: b['max_ha'])
        ]


class ConversationSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ['conversation_id', 'title', 'updated_at']


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ['conversation_id', 'title', 'messages', 'created_at', 'updated_at']


class WaiverSerializer(serializers.ModelSerializer):
    county = serializers.CharField(source='county.name', read_only=True)
    created_by = serializers.CharField(source='created_by.username', read_only=True, default=None)
    revoked_by = serializers.CharField(source='revoked_by.username', read_only=True, default=None)
    status = serializers.SerializerMethodField()

    class Meta:
        model = Waiver
        fields = [
            'waiver_id', 'county', 'name', 'legal_reference', 'percent', 'years', 'sub_counties', 'wards',
            'land_uses', 'parcel_refs', 'starts_on', 'ends_on', 'status', 'created_by', 'created_at',
            'revoked_by', 'revoked_at', 'bills_affected', 'amount_waived',
        ]
        read_only_fields = ['waiver_id', 'created_at', 'revoked_at', 'bills_affected', 'amount_waived']

    def get_status(self, obj) -> str:
        today = timezone.localdate()
        if obj.revoked_at:
            return 'revoked'
        if obj.starts_on > today:
            return 'scheduled'
        return 'ended' if obj.ends_on and obj.ends_on < today else 'active'

    def validate_percent(self, value):
        if not Decimal(0) < value <= Decimal(100):
            raise serializers.ValidationError('Waive between 0 and 100 percent.')
        return value

    def validate_years(self, value):
        current = timezone.now().year
        if not isinstance(value, list) or not all(isinstance(y, int) and current - 20 <= y <= current + 1 for y in value):
            raise serializers.ValidationError(f'Years must be between {current - 20} and {current + 1}.')
        return sorted(set(value))

    def _names(self, value):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise serializers.ValidationError('Give a list of names.')
        return sorted({v.strip() for v in value if v.strip()})

    validate_sub_counties = validate_wards = validate_parcel_refs = _names

    def validate_land_uses(self, value):
        names = self._names(value)
        allowed = {choice for choice, _ in Parcel._meta.get_field('land_use').choices}
        if not set(names) <= allowed:
            raise serializers.ValidationError(f'Land use must be one of {", ".join(sorted(allowed))}.')
        return names

    def validate(self, attrs):
        if attrs.get('ends_on') and attrs['ends_on'] < attrs['starts_on']:
            raise serializers.ValidationError({'ends_on': 'The waiver cannot end before it starts.'})
        return attrs
