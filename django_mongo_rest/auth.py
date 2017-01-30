from django.contrib.auth.backends import ModelBackend
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

class PasswordlessAuthBackend(object):
    '''Log in to Django without providing a password.'''

    @staticmethod
    def authenticate(*args, **kwargs):
        return None

    get_user = ModelBackend.__dict__['get_user']
    user_can_authenticate = ModelBackend.__dict__['user_can_authenticate']
