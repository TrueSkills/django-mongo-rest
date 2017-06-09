from django.contrib.auth.backends import ModelBackend

class PasswordlessAuthBackend(object):
    '''Log in to Django without providing a password.'''

    @staticmethod
    def authenticate(*args, **kwargs):
        return None

    get_user = ModelBackend.__dict__['get_user']
    user_can_authenticate = ModelBackend.__dict__['user_can_authenticate']
