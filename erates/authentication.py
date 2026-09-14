from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import serializers
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiResponse
from .models import User
from . import audit


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = 'username'
    
    def validate(self, attrs):
        username = attrs.get('username')
        password = attrs.get('password')
        
        if not username or not password:
            raise serializers.ValidationError({
                'detail': 'Username and password are required'
            })
        
        
        user = (
            User.objects.filter(username=username, is_deleted=False).first()
            or User.objects.filter(username=username.lower(), is_deleted=False).first()
        )
        if user is None:
            audit.record('auth.login_failed', object_type='user', identifier=audit.mask(username), method='username')
            raise serializers.ValidationError({
                'detail': 'No active account found with the given credentials'
            })
        
      
        if not user.is_active:
            raise serializers.ValidationError({
                'detail': 'User account is disabled'
            })
        
      
        if user.is_locked():
            audit.record('auth.login_locked', obj=user, who=user)
            raise serializers.ValidationError({
                'detail': 'Account is temporarily locked due to failed login attempts'
            })
        
        if not user.check_password(password):
            audit.record('auth.login_failed', obj=user, who=user, identifier=user.username, attempts=user.failed_login_attempts)
            if user.locked_until:
                audit.record('auth.login_locked', obj=user, who=user)
            raise serializers.ValidationError({
                'detail': 'No active account found with the given credentials'
            })
        
        
        user.last_login = timezone.now()
        user.failed_login_attempts = 0
        user.save(update_fields=['last_login', 'failed_login_attempts'])
        audit.record('auth.login', obj=user, who=user, method='username')
        
        refresh = self.get_token(user)
        
        data = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
        
       
        data['user'] = {
            'user_id': str(user.user_id),
            'username': user.username,
            'email': user.email,
            'role': user.role,
            'county': user.county,
            'is_verified': user.is_verified,
            'must_change_password': user.must_change_password,
        }
        
        return data
    
    @classmethod
    def get_token(cls, user):
        """
        Generate token with custom claims.
        """
        token = super().get_token(user)
        
        
        token['username'] = user.username
        token['email'] = user.email
        token['role'] = user.role
        token['county'] = user.county
        token['is_verified'] = user.is_verified
        token['must_change_password'] = user.must_change_password

        return token


@extend_schema(
    summary="Obtain JWT token pair (username/email)",
    description="Authenticate using username or email and password to obtain JWT access and refresh tokens.",
    tags=['Auth'],
    request=CustomTokenObtainPairSerializer,
    responses={
        200: OpenApiResponse(description="Token pair obtained successfully"),
        400: OpenApiResponse(description="Invalid credentials"),
    },
)
class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class PhoneTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom JWT serializer for phone number + password authentication.
    """
    username_field = 'phone'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Replace username field with phone field
        self.fields['phone'] = serializers.CharField()
        self.fields.pop('username', None)
    
    def validate(self, attrs):
        """
        Validate credentials using phone number and password.
        """
        phone = attrs.get('phone')
        password = attrs.get('password')
        
        if not phone or not password:
            raise serializers.ValidationError({
                'detail': 'Phone number and password are required'
            })
        
        # Clean phone number (remove spaces, dashes, etc.)
        import re
        cleaned_phone = re.sub(r'[\s\-\(\)]', '', phone)
        
        try:
            user = User.find_by_phone(cleaned_phone)
            if not user or not user.is_active:
                raise User.DoesNotExist()
        except User.DoesNotExist:
            audit.record('auth.login_failed', object_type='user', identifier=audit.mask(cleaned_phone), method='phone')
            raise serializers.ValidationError({
                'detail': 'No active account found with the given credentials'
            })
        
        if not user.is_active:
            raise serializers.ValidationError({
                'detail': 'User account is disabled'
            })
        
        if user.is_locked():
            audit.record('auth.login_locked', obj=user, who=user)
            raise serializers.ValidationError({
                'detail': 'Account is temporarily locked due to failed login attempts'
            })
        
        if not user.check_password(password):
            audit.record('auth.login_failed', obj=user, who=user, identifier=user.username, attempts=user.failed_login_attempts)
            if user.locked_until:
                audit.record('auth.login_locked', obj=user, who=user)
            raise serializers.ValidationError({
                'detail': 'No active account found with the given credentials'
            })
        
       
        user.last_login = timezone.now()
        user.failed_login_attempts = 0
        user.save(update_fields=['last_login', 'failed_login_attempts'])
        audit.record('auth.login', obj=user, who=user, method='phone')
        
        
        refresh = self.get_token(user)
        
        data = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
        

        data['user'] = {
            'user_id': str(user.user_id),
            'username': user.username,
            'email': user.email,
            'phone': user.phone,
            'role': user.role,
            'county': user.county,
            'is_verified': user.is_verified,
            'must_change_password': user.must_change_password,
        }
        
        return data
    
    @classmethod
    def get_token(cls, user):
        """
        Generate token with custom claims.
        """
        token = super().get_token(user)
        

        token['username'] = user.username
        token['email'] = user.email
        token['role'] = user.role
        token['county'] = user.county
        token['is_verified'] = user.is_verified
        token['must_change_password'] = user.must_change_password

        return token


@extend_schema(
    summary="Obtain JWT token pair (phone)",
    description="Authenticate using phone number and password to obtain JWT access and refresh tokens.",
    tags=['Auth'],
    request=PhoneTokenObtainPairSerializer,
    responses={
        200: OpenApiResponse(description="Token pair obtained successfully"),
        400: OpenApiResponse(description="Invalid credentials"),
    },
)
class PhoneTokenObtainPairView(TokenObtainPairView):
    """
    Custom view for obtaining JWT token pair using phone number.
    """
    serializer_class = PhoneTokenObtainPairSerializer
