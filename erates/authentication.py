from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import serializers
from django.utils import timezone
from .models import User


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = 'username'
    
    def validate(self, attrs):
        username = attrs.get('username')
        password = attrs.get('password')
        
        if not username or not password:
            raise serializers.ValidationError({
                'detail': 'Username and password are required'
            })
        
        
        try:
            user = User.objects.get(username=username, is_deleted=False)
        except User.DoesNotExist:
            raise serializers.ValidationError({
                'detail': 'No active account found with the given credentials'
            })
        
      
        if not user.is_active:
            raise serializers.ValidationError({
                'detail': 'User account is disabled'
            })
        
      
        if user.is_locked():
            raise serializers.ValidationError({
                'detail': 'Account is temporarily locked due to failed login attempts'
            })
        
        if not user.check_password(password):
            raise serializers.ValidationError({
                'detail': 'No active account found with the given credentials'
            })
        
        
        user.last_login = timezone.now()
        user.failed_login_attempts = 0
        user.save(update_fields=['last_login', 'failed_login_attempts'])
        
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
            'is_verified': user.is_verified,
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
        token['is_verified'] = user.is_verified
        
        return token


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
        
        # Get user by phone number
        try:
            # Since phone is encrypted, we need to check all active users
            users = User.objects.filter(is_deleted=False, is_active=True)
            user = None
            for u in users:
                # Decrypt and compare phone numbers
                if u.phone and re.sub(r'[\s\-\(\)]', '', u.phone) == cleaned_phone:
                    user = u
                    break
            
            if not user:
                raise User.DoesNotExist()
                
        except User.DoesNotExist:
            raise serializers.ValidationError({
                'detail': 'No active account found with the given credentials'
            })
        
        if not user.is_active:
            raise serializers.ValidationError({
                'detail': 'User account is disabled'
            })
        
        if user.is_locked():
            raise serializers.ValidationError({
                'detail': 'Account is temporarily locked due to failed login attempts'
            })
        
        if not user.check_password(password):
            raise serializers.ValidationError({
                'detail': 'No active account found with the given credentials'
            })
        
       
        user.last_login = timezone.now()
        user.failed_login_attempts = 0
        user.save(update_fields=['last_login', 'failed_login_attempts'])
        
        
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
            'is_verified': user.is_verified,
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
        token['is_verified'] = user.is_verified
        
        return token


class PhoneTokenObtainPairView(TokenObtainPairView):
    """
    Custom view for obtaining JWT token pair using phone number.
    """
    serializer_class = PhoneTokenObtainPairSerializer
