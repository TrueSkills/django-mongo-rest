from django_mongo_rest.utils import Enum

class PERMISSION(Enum):
    EMAIL_UNVERIFIED = 'is_email_unverified'
    LOGIN = 'is_login_required'
    SUPERUSER = 'is_superuser'

_PERMISSIONS = {
    PERMISSION.EMAIL_UNVERIFIED: lambda request: request.user.is_authenticated(),
    PERMISSION.LOGIN: lambda request: request.user.is_authenticated() and request.user.email_verified,
    PERMISSION.SUPERUSER: lambda request: request.user.is_superuser
}

def _is_authorized(permission_name, request):
    return _PERMISSIONS[permission_name](request)

def is_authorized(request, permission_names):
    return all(_is_authorized(permission_name, request) for permission_name in permission_names)
