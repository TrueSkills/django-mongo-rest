import os, sys, django, pytest

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'server.settings')
django.setup()

from datetime import datetime
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.test.client import Client
from server.settings import MONGODB
from utils import uniquify

@pytest.fixture
def user():
    user = create_user()
    yield user
    MONGODB.user.delete_one({'_id': user})

@pytest.fixture
def superuser_session():
    user = create_user(is_superuser=True)
    client = Client()
    client.login(username=user['username'], password=user['password'])
    yield (user, client)
    MONGODB.user.delete_one({'_id': user})

@pytest.fixture(scope='session')
def superuser_session_const():
    '''Session that is reused across tests

    Avoids the overhead of creating a new user for every test.
    Use in tests that will not interfere with other tests using the same user.
    '''
    user = create_user(is_superuser=True)
    client = Client()
    client.login(username=user['username'], password=user['password'])
    yield (user, client)
    MONGODB.user.delete_one({'_id': user})

@pytest.fixture
def user_session(user):
    client = Client()
    client.login(username=user['username'], password=user['password'])
    yield (user, client)
    MONGODB.user.delete_one({'_id': user})

@pytest.fixture(scope='session')
def user_session_const():
    '''Session that is reused across tests

    Avoids the overhead of creating a new user for every test.
    Use in tests that will not interfere with other tests using the same user.
    '''
    user = create_user()
    client = Client()
    client.login(username=user['username'], password=user['password'])
    yield (user, client)
    MONGODB.user.delete_one({'_id': user})

def create_user(**kwargs):
    user = {
        'first_name': 'TEST',
        'last_name': 'USER',
        'email': 'TESTUSER@sfgsldfgsdf.com',
        'is_superuser': False,
        'is_staff': False,
        'username': None,
        'email_verified': True,
        'date_joined': datetime.utcnow(),
        'is_active': True,
        'last_updated': datetime.utcnow(),
    }

    user.update(kwargs)

    password = 'sdnblfdgl34g'
    user['email'] = uniquify(user['email'])
    user['password'] = PBKDF2PasswordHasher().encode(password, 'salt')
    user['is_staff'] = user['is_superuser'] or user['is_staff']
    user['username'] = (user['username'] or user['email'])

    MONGODB.user.insert_one(user)
    user['password'] = password
    return user
