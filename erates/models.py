from django.conf import settings
from django.db import models
from django.contrib.gis.db import models as gis_models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.utils import timezone
from encrypted_model_fields.fields import EncryptedCharField
import json
import re
import uuid
import hashlib
import hmac
from decimal import Decimal
from typing import Optional


def integrity_hmac(*parts) -> str:
    message = "|".join(str(p) for p in parts).encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def money(value) -> str:
    return f"{Decimal(value):.2f}"


def canonical_phone(value) -> str:
    digits = re.sub(r'\D', '', value or '')
    if digits.startswith('0') and len(digits) == 10:
        return '254' + digits[1:]
    if len(digits) == 9 and digits[0] in '17':
        return '254' + digits
    return digits


def phone_lookup_hash(value):
    canonical = canonical_phone(value)
    return integrity_hmac('phone', canonical) if canonical else None


class SecurityMixin(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        abstract = True
    
    def soft_delete(self):
        """Soft delete this record instead of permanent deletion"""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])
    
    def restore(self):
        """Restore a soft-deleted record"""
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=['is_deleted', 'deleted_at'])

class UserManager(BaseUserManager):
    """Custom user manager for User model"""
    
    def create_user(self, username, email, password=None, **extra_fields):
        """Create and return a regular user"""
        if not username:
            raise ValueError('Username is required')
        if not email:
            raise ValueError('Email is required')
        
        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, username, email, password=None, **extra_fields):
        """Create and return a superuser"""
        extra_fields.setdefault('role', 'admin')
        extra_fields.setdefault('is_verified', True)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        
        return self.create_user(username, email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin, SecurityMixin):
    user_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    national_id = EncryptedCharField(max_length=255, blank=True, null=True)
    phone = EncryptedCharField(max_length=255, blank=True, null=True)
    phone_hash = models.CharField(max_length=64, blank=True, null=True, unique=True, editable=False)
    username = models.CharField(max_length=150, unique=True, db_index=True)
    email = models.EmailField(unique=True, db_index=True)
    is_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # Required for admin access
    failed_login_attempts = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(auto_now_add=True)
   
    role = models.CharField(
        max_length=50,
        choices=[
            ('user', 'Land owner'),
            ('admin', 'County official'),
            ('auditor', 'Auditor'),
            ('owner', 'Platform owner'),
        ],
        default='user'
    )
    county = models.CharField(max_length=100, blank=True, db_index=True,
                              help_text="County this account belongs to; blank for platform owners")
    must_change_password = models.BooleanField(default=False)
    
    # Required for AbstractBaseUser
    USERNAME_FIELD = 'username'
    EMAIL_FIELD = 'email'
    REQUIRED_FIELDS = ['email']
    
    objects = UserManager()
    
    class Meta:
        db_table = 'users'
        indexes = [
            models.Index(fields=['email', 'is_active']),
            models.Index(fields=['username', 'is_active']),
        ]
    
    def set_password(self, raw_password: str) -> None:
        """Override to add password validation and update password_changed_at"""
        if not raw_password or len(raw_password) < 8:
            raise ValidationError("Password must be at least 8 characters long")
        super().set_password(raw_password)  # Use AbstractBaseUser's set_password
        self.password_changed_at = timezone.now()
        self.must_change_password = False

    def check_password(self, raw_password: str) -> bool:
        """Override to add account locking logic"""
        if self.is_locked():
            return False
        
        is_correct = super().check_password(raw_password)  # Use AbstractBaseUser's check_password
        
        if is_correct:
            self.failed_login_attempts = 0
            self.last_login = timezone.now()
            self.save(update_fields=['failed_login_attempts', 'last_login'])
        else:
            self.failed_login_attempts += 1
            if self.failed_login_attempts >= 5:
                self.locked_until = timezone.now() + timezone.timedelta(minutes=30)
            self.save(update_fields=['failed_login_attempts', 'locked_until'])
        
        return is_correct
    
    def is_locked(self) -> bool:
        if self.locked_until and timezone.now() < self.locked_until:
            return True
        elif self.locked_until and timezone.now() >= self.locked_until:
            self.locked_until = None
            self.failed_login_attempts = 0
            self.save(update_fields=['locked_until', 'failed_login_attempts'])
        return False
    
    def save(self, *args, **kwargs):
        self.phone_hash = phone_lookup_hash(self.phone)
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'phone' in update_fields:
            kwargs['update_fields'] = {*update_fields, 'phone_hash'}
        super().save(*args, **kwargs)

    @property
    def is_platform_owner(self) -> bool:
        return self.role == 'owner'

    @property
    def is_county_staff(self) -> bool:
        return self.role in ('admin', 'auditor')

    @classmethod
    def find_by_phone(cls, phone):
        key = phone_lookup_hash(phone)
        return cls.objects.filter(phone_hash=key, is_deleted=False).first() if key else None

    def __str__(self):
        return self.username


class County(models.Model):
    """A county on the platform: its name, crest and rates office contact."""
    county_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    logo = models.ImageField(upload_to='county-logos/', blank=True, null=True)
    rates_office_email = models.EmailField(blank=True)
    rates_office_phone = models.CharField(max_length=20, blank=True)
    paybill = models.CharField(max_length=20, blank=True, help_text="M-Pesa short code rates are paid to")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'counties'
        ordering = ['name']
        verbose_name_plural = 'counties'

    def __str__(self):
        return self.name


class Account(SecurityMixin):
    account_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_user = models.ForeignKey(
        User, 
        on_delete=models.PROTECT,
        related_name="accounts"
    )
    account_type = models.CharField(
        max_length=50,
        choices=[
            ('main', 'Main Account'),
            ('escrow', 'Escrow Account'),
            ('reserve', 'Reserve Account'),
        ]
    )
    currency = models.CharField(max_length=3, default="KES")
    
    # Balance tracking with integrity
    current_balance = models.DecimalField(
        max_digits=20, 
        decimal_places=2, 
        default=Decimal('0.00')
    )
    balance_hash = models.CharField(max_length=225,default='000000000000', editable=False)
    
    status = models.CharField(
        max_length=20,
        choices=[
            ('active', 'Active'),
            ('frozen', 'Frozen'),
            ('closed', 'Closed'),
        ],
        default='active'
    )
    
    metadata = models.JSONField(blank=True, null=True)
    
    class Meta:
        db_table = 'accounts'
        indexes = [
            models.Index(fields=['owner_user', 'status']),
        ]
    
    def compute_balance_hash(self) -> str:
        """Compute HMAC-SHA256 of the balance for integrity verification."""
        return integrity_hmac(self.account_id, money(self.current_balance))
    
    def verify_balance_integrity(self) -> bool:
        """Verify that balance hasn't been tampered with"""
        expected_hash = self.compute_balance_hash()
        return hmac.compare_digest(self.balance_hash, expected_hash)
    
    def save(self, *args, **kwargs):
        """Override save to always update balance hash"""
        self.balance_hash = self.compute_balance_hash()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.owner_user.username} - {self.account_type} ({self.current_balance} {self.currency})"


class Parcel(SecurityMixin):
    parcel_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_user = models.ForeignKey(
        User, 
        on_delete=models.PROTECT,
        related_name="parcels",
        null=True,
        blank=True,
        help_text="Property owner (can be assigned later)"
    )
    parcel_ref = models.CharField(max_length=100, unique=True, db_index=True)
    geom = gis_models.GeometryField(srid=4326)
    centroid = gis_models.PointField(srid=4326, blank=True, null=True)
    area_m2 = models.FloatField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ('active', 'Active'),
            ('disputed', 'Disputed'),
            ('transferred', 'Transferred'),
            ('archived', 'Archived'),
        ],
        default='active'
    )
    
    # Location fields
    county = models.CharField(max_length=100, db_index=True)
    sub_county = models.CharField(max_length=100, db_index=True)
    ward = models.CharField(max_length=100, blank=True, null=True, db_index=True)

    land_use = models.CharField(
        max_length=20,
        choices=[
            ('residential', 'Residential'),
            ('commercial', 'Commercial'),
            ('industrial', 'Industrial'),
            ('agricultural', 'Agricultural'),
            ('institutional', 'Institutional'),
            ('mixed', 'Mixed Use'),
        ],
        default='residential',
    )
    unimproved_site_value = models.DecimalField(
        max_digits=20, decimal_places=2, blank=True, null=True,
        help_text="USV from the county valuation roll; blank means flat-rate by area",
    )

    props = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = 'parcels'
        indexes = [
            models.Index(fields=['owner_user', 'status']),
            models.Index(fields=['parcel_ref']),
        ]
    
    def clean(self):
        """Validate geometry coordinates are within WGS84 bounds"""
        super().clean()
        
        if self.geom:
            try:
                # Check if coordinates are within valid WGS84 range
                # Longitude: -180 to 180, Latitude: -90 to 90
                extent = self.geom.extent  # Returns (xmin, ymin, xmax, ymax)
                
                if extent[0] < -180 or extent[2] > 180:
                    raise ValidationError(
                        f"Longitude out of valid range (-180 to 180): {extent[0]} to {extent[2]}"
                    )
                
                if extent[1] < -90 or extent[3] > 90:
                    raise ValidationError(
                        f"Latitude out of valid range (-90 to 90): {extent[1]} to {extent[3]}"
                    )
            except ValidationError:
                raise
            except Exception as e:
                raise ValidationError(f"Invalid geometry: {str(e)}")
    
    def save(self, *args, **kwargs):
        """Auto-calculate centroid and area if not provided"""
        if self.geom:
            # Ensure geometry has SRID set
            if not self.geom.srid:
                self.geom.srid = 4326
            
            # Calculate centroid
            if not self.centroid:
                try:
                    self.centroid = self.geom.centroid
                except Exception:
                    self.centroid = None
            
            if not self.area_m2:
                self.area_m2 = self.compute_area_m2()

        super().save(*args, **kwargs)

    def compute_area_m2(self) -> Optional[float]:
        try:
            geom = self.geom.clone()
            if geom.srid != 4326:
                geom.transform(4326)
            zone = int((geom.centroid.x + 180) // 6) + 1
            geom.transform((32600 if geom.centroid.y >= 0 else 32700) + zone)
            return geom.area
        except Exception:
            return None
    
    def __str__(self):
        return f"Parcel {self.parcel_ref}"


class ParcelHistory(models.Model):
    history_id = models.BigAutoField(primary_key=True)
    parcel = models.ForeignKey(Parcel, on_delete=models.PROTECT, related_name="history")
    owner_user = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True)
    geom = gis_models.GeometryField(srid=4326)
    area_m2 = models.FloatField(blank=True, null=True)
    changed_by = models.ForeignKey(
        User, 
        on_delete=models.PROTECT,
        related_name="parcel_changes_made"
    )
    change_reason = models.TextField(blank=True, null=True)
    change_ts = models.DateTimeField(auto_now_add=True, editable=False)
    record_hash = models.CharField(max_length=64, editable=False)
    previous_hash = models.CharField(max_length=64, blank=True, null=True)
    
    class Meta:
        db_table = 'parcel_history'
        ordering = ['-change_ts']
        indexes = [
            models.Index(fields=['parcel', '-change_ts']),
        ]
    
    def compute_record_hash(self) -> str:
        """Compute hash including previous hash for chain integrity"""
        data = (
            f"{self.parcel_id}{self.owner_user_id}{self.area_m2}"
            f"{self.changed_by_id}{self.change_reason}{self.previous_hash or ''}"
        )
        return hashlib.sha256(data.encode()).hexdigest()
    
    def save(self, *args, **kwargs):
        """Override save to create hash chain"""
        if not self.pk:  
            last_record = ParcelHistory.objects.filter(
                parcel=self.parcel
            ).order_by('-change_ts').first()
            
            if last_record:
                self.previous_hash = last_record.record_hash
        
        self.record_hash = self.compute_record_hash()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"History for {self.parcel.parcel_ref} at {self.change_ts}"


class LedgerEntry(models.Model):
    entry_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        Account, 
        on_delete=models.PROTECT,
        related_name="ledger_entries"
    )
    related_account_id = models.UUIDField(blank=True, null=True)
    
    
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    balance_after = models.DecimalField(max_digits=20, decimal_places=2)
    entry_type = models.CharField(
        max_length=50,
        choices=[
            ('credit', 'Credit'),
            ('debit', 'Debit'),
            ('transfer_in', 'Transfer In'),
            ('transfer_out', 'Transfer Out'),
            ('fee', 'Fee'),
            ('refund', 'Refund'),
        ]
    )
    

    external_ref = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    idempotency_key = models.UUIDField(null=True, blank=True, unique=True)
    
    entry_hash = models.CharField(max_length=64, editable=False)
    
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    
    class Meta:
        db_table = 'ledger_entries'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['account', '-created_at']),
            models.Index(fields=['idempotency_key']),
        ]
    
    def compute_entry_hash(self) -> str:
        """Compute HMAC for entry integrity"""
        return integrity_hmac(
            self.entry_id, self.account_id, money(self.amount),
            money(self.balance_after), self.entry_type,
        )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Ledger entries are immutable and cannot be updated")
        
        self.entry_hash = self.compute_entry_hash()
        super().save(*args, **kwargs)
    
    def verify_integrity(self) -> bool:
        """Verify entry hasn't been tampered with"""
        expected_hash = self.compute_entry_hash()
        return hmac.compare_digest(self.entry_hash, expected_hash)
    
    def __str__(self):
        return f"{self.entry_type}: {self.amount} (Balance: {self.balance_after})"


class Payment(SecurityMixin):
    payment_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="payments")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="payments")
    parcel = models.ForeignKey(
        Parcel, on_delete=models.PROTECT, related_name="payments", blank=True, null=True,
    )
    payment_year = models.IntegerField(blank=True, null=True, db_index=True)

    amount = models.DecimalField(max_digits=20, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    
    processor = models.CharField(max_length=100, blank=True, null=True)
    processor_ref = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    
    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('processing', 'Processing'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
            ('refunded', 'Refunded'),
        ],
        default='pending'
    )
    
    deadline = models.DateTimeField(
        help_text="Payment deadline. If payment is not completed by this date, user becomes a defaulter.",
        blank=True,
        null=True,
        db_index=True
    )
    
    idempotency_key = models.CharField(max_length=255, unique=True, db_index=True)
    payment_hash = models.CharField(max_length=64, editable=False)
    
    
    failure_reason = models.TextField(blank=True, null=True)
    metadata = models.JSONField(blank=True, null=True)
    
    class Meta:
        db_table = 'payments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['parcel', 'payment_year'],
                condition=models.Q(parcel__isnull=False, payment_year__isnull=False),
                name='one_rate_bill_per_parcel_year',
            ),
        ]

    def compute_payment_hash(self) -> str:
        """Compute integrity hash for payment"""
        return integrity_hmac(
            self.payment_id, self.user_id, self.account_id,
            money(self.amount), self.currency, self.status,
        )
    
    def is_defaulter(self) -> bool:
        """Check if payment is past deadline and not completed"""
        if not self.deadline:
            return False
        return (
            timezone.now() > self.deadline and 
            self.status not in ['completed', 'refunded']
        )
    
    def days_overdue(self) -> Optional[int]:
        """Calculate how many days the payment is overdue"""
        if not self.deadline or self.status in ['completed', 'refunded']:
            return None
        delta = timezone.now() - self.deadline
        return delta.days if delta.days > 0 else None
    
    def save(self, *args, **kwargs):
        """Update payment hash on save"""
        self.payment_hash = self.compute_payment_hash()
        super().save(*args, **kwargs)
    
    def verify_integrity(self) -> bool:
        """Verify payment hasn't been tampered with"""
        expected_hash = self.compute_payment_hash()
        return hmac.compare_digest(self.payment_hash, expected_hash)
    
    def __str__(self):
        return f"Payment {self.payment_id} - {self.amount} {self.currency} ({self.status})"


class MpesaTransaction(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="mpesa_transactions")
    checkout_request_id = models.CharField(max_length=100, unique=True)
    merchant_request_id = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=15)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    result_code = models.IntegerField(blank=True, null=True)
    result_desc = models.TextField(blank=True)
    receipt = models.CharField(max_length=30, blank=True, db_index=True)
    raw_callback = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mpesa_transactions'
        ordering = ['-created_at']

    @property
    def is_final(self) -> bool:
        return self.result_code is not None

    def __str__(self):
        return f"M-Pesa {self.checkout_request_id} ({self.result_code})"


class AuditLog(models.Model):
    audit_id = models.BigAutoField(primary_key=True)
    who = models.ForeignKey(
        User, 
        on_delete=models.PROTECT,
        null=True,
        related_name="audit_logs"
    )
    
    action = models.CharField(max_length=100, db_index=True)
    object_type = models.CharField(max_length=100)
    object_id = models.UUIDField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, null=True)
    
    details = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    
    log_hash = models.CharField(max_length=64, editable=False)
    previous_hash = models.CharField(max_length=64, blank=True, null=True)
    
    class Meta:
        db_table = 'audit_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['who', '-created_at']),
            models.Index(fields=['action', '-created_at']),
            models.Index(fields=['object_type', 'object_id']),
        ]
    
    def compute_log_hash(self) -> str:
        """Compute hash including previous hash for chain integrity"""
        return integrity_hmac(
            self.who_id, self.action, self.object_type, self.object_id, self.ip_address,
            json.dumps(self.details or {}, sort_keys=True, default=str), self.previous_hash or '',
        )

    def verify_integrity(self) -> bool:
        return hmac.compare_digest(self.log_hash, self.compute_log_hash())
    
    def save(self, *args, **kwargs):
        """Create hash chain for audit trail"""
        if not self.pk:
            last_log = AuditLog.objects.order_by('-audit_id').first()
            if last_log:
                self.previous_hash = last_log.log_hash
        
        self.log_hash = self.compute_log_hash()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"Audit: {self.action} by {self.who} at {self.created_at}"


class ParcelDeletionRequest(SecurityMixin):
    """A county official asking the platform owner to remove a parcel they cannot delete themselves."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    request_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parcel = models.ForeignKey(
        'Parcel', on_delete=models.CASCADE, related_name='deletion_requests',
    )
    requested_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='parcel_deletion_requests',
    )
    reason = models.TextField(help_text="Why this parcel should go; shown to the platform owner")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='parcel_deletions_reviewed',
        null=True, blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'parcel_deletion_requests'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'])]
        constraints = [
            models.UniqueConstraint(
                fields=['parcel'],
                condition=models.Q(status='pending', is_deleted=False),
                name='one_open_deletion_request_per_parcel',
            )
        ]

    def __str__(self):
        return f"{self.parcel.parcel_ref} deletion ({self.status})"


class RateSchedule(SecurityMixin):
    """What one county charges for one rating year (National Rating Act 2024, s.9)."""
    schedule_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    county = models.ForeignKey(County, on_delete=models.PROTECT, related_name='rate_schedules')
    year = models.IntegerField(db_index=True)
    bands = models.JSONField(help_text='Flat area rates, ascending: [{"max_ha": "0.1", "amount": "2560"}]')
    top_amount = models.DecimalField(max_digits=12, decimal_places=2, help_text='Flat rate above the largest band')
    usv_rate_percent = models.DecimalField(
        max_digits=7, decimal_places=4, help_text='Percent of unimproved site value, for parcels that have one',
    )
    deadline = models.DateTimeField()
    set_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='rate_schedules_set', null=True, blank=True,
    )

    class Meta:
        db_table = 'rate_schedules'
        ordering = ['county__name', '-year']
        constraints = [
            models.UniqueConstraint(fields=['county', 'year'], name='one_rate_schedule_per_county_year'),
        ]

    def __str__(self):
        return f"{self.county.name} {self.year}"


class Conversation(SecurityMixin):
    """One official's chat with the assistant, kept so they can return to it."""
    conversation_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversations')
    title = models.CharField(max_length=120)
    messages = models.JSONField(default=list)

    class Meta:
        db_table = 'conversations'
        ordering = ['-updated_at']

    def __str__(self):
        return self.title
