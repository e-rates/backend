from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import (
    TokenRefreshView,
    TokenVerifyView,
)
from erates.authentication import CustomTokenObtainPairView, PhoneTokenObtainPairView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)
from drf_spectacular.utils import extend_schema, OpenApiResponse

# Decorated JWT views for better documentation
TokenRefreshViewWithSchema = extend_schema(
    summary="Refresh JWT access token",
    description="Use a refresh token to obtain a new access token.",
    tags=['Auth'],
    responses={
        200: OpenApiResponse(description="New access token obtained"),
        401: OpenApiResponse(description="Invalid or expired refresh token"),
    },
)(TokenRefreshView)

TokenVerifyViewWithSchema = extend_schema(
    summary="Verify JWT token",
    description="Verify if a JWT token is valid and not expired.",
    tags=['Auth'],
    responses={
        200: OpenApiResponse(description="Token is valid"),
        401: OpenApiResponse(description="Token is invalid or expired"),
    },
)(TokenVerifyView)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api-auth/', include('rest_framework.urls')),  # DRF login/logout
    
    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
  
    # Authentication endpoints
    path('api/token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/phone/', PhoneTokenObtainPairView.as_view(), name='token_obtain_pair_phone'),
    path('api/token/refresh/', TokenRefreshViewWithSchema.as_view(), name='token_refresh'),
    path('api/token/verify/', TokenVerifyViewWithSchema.as_view(), name='token_verify'),
    
    # Main API endpoints
    path('api/', include('erates.urls')),
]

