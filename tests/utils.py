from bson import ObjectId
from datetime import datetime
from django.test.client import Client
import json
import random
import string

from simplejson.scanner import JSONDecodeError

def json_default(obj):
    if isinstance(obj, (datetime, ObjectId)):
        return str(obj)
    raise Exception('Can\'t serialize {} {}'.format(type(obj), obj))

def _hit_api(method, url, data=None, client=None):
    client = client or Client()
    return getattr(client, method)('/api/' + url, data=data, content_type='application/json')

def post_api(url, data=None, client=None):
    if isinstance(data, dict):
        data = json.dumps(data, default=json_default)
    return _hit_api('post', url, data=data, client=client)

def delete_api(url, client=None):
    return _hit_api('delete', url, client=client)

def get_api(url, client=None):
    return _hit_api('get', url, client=client)

def patch_api(url, data=None, client=None):
    if isinstance(data, dict):
        data = json.dumps(data, default=json_default)
    return _hit_api('patch', url, data=data, client=client)

def options_api(url, data=None, client=None):
    return _hit_api('options', url, data=data, client=client)

def get_page(url, client=None):
    client = client or Client()
    return client.get('/' + url)

def assert_status(response, expected_status=200, error_code=None):
    if expected_status == response.status_code:
        if error_code:
            assert response.json()['error_code'] == error_code
        return
    if response.status_code in (404, 500, 200):
        try:
            response.json()
        except (JSONDecodeError, ValueError):
            # If there is no json, that means we have a long stacktrace/html,
            # which we can see in the django log anyways, so don't pollute the test logs
            raise Exception(response.status_code)
    text = getattr(response, 'text', getattr(response, 'content'))
    raise Exception('%s, %s' % (response.status_code, text))

def uniquify(s):
    # email, we still want it to be valid after uniquifying
    s = s.split('@')

    # lowercase because backend will lowercase emails and we want our data to match
    s[0] = s[0] + '-' + ''.join([random.choice(string.ascii_lowercase + string.digits) for _ in range(8)])

    if len(s) == 1:
        return s[0]

    return s[0] + '@' + s[1]

def to_id(obj):
    if obj is None:
        return ObjectId()
    if isinstance(obj, ObjectId):
        return obj
    if isinstance(obj, dict):
        return obj['_id']
    return ObjectId(obj)

def to_list(x):
    if isinstance(x, (list, tuple)):
        return x
    return [x]

class DummyObject(object):
    pass
