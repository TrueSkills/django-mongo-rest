import calendar
from django.conf import settings
from django.core import signing
from django.utils import timezone
from urllib import urlencode

TIMESTAMP_QUERY_PARAM = 'tmstmp'
SIGNATURE_QUERY_PARAM = 'sign'
SALT_QUERY_PARAM = 'salt'

class InvalidSig(Exception):
    pass

class ExpiredSig(Exception):
    pass

def _signature(querystring, salt):
    return signing.Signer(key=settings.SECRET_KEY, salt=salt).signature(querystring)

def sign_querystring(querystring, salt, timestamp_override=None):
    if isinstance(querystring, dict):
        querystring = urlencode(querystring)

    timestamp = timestamp_override or calendar.timegm(timezone.now().timetuple())
    querystring += '&%s=%d&%s=%s' % (TIMESTAMP_QUERY_PARAM, timestamp, SALT_QUERY_PARAM, salt)
    return querystring + '&%s=%s' % (SIGNATURE_QUERY_PARAM, _signature(querystring, salt))

def _verify_params_match_query(request):
    for key, value in request.GET.items():
        if key in (TIMESTAMP_QUERY_PARAM, SIGNATURE_QUERY_PARAM, SALT_QUERY_PARAM):
            continue

        if request.dmr_params.get(key) != value:
            '''Url signature is signing one set of params, but POST contains different params.
            Someone is trying to trick us.'''
            raise InvalidSig

def verify_signature(request, expected_salt=None, force=True, max_age=None):
    if SIGNATURE_QUERY_PARAM not in request.GET:
        if force:
            raise InvalidSig
        return

    query_salt = request.GET.get(SALT_QUERY_PARAM)
    if not query_salt or (expected_salt and query_salt != expected_salt):
        raise InvalidSig

    try:
        querystring = request.get_full_path().split('?')[1]
    except IndexError:
        raise InvalidSig

    split = querystring.split('&%s=' % SIGNATURE_QUERY_PARAM)
    if len(split) != 2:
        raise InvalidSig

    base, signature = split
    if signature != _signature(base, query_salt):
        raise InvalidSig

    if max_age:
        timestamp = int(request.GET.get(TIMESTAMP_QUERY_PARAM))
        if calendar.timegm(timezone.now().timetuple()) - timestamp > max_age:
            raise ExpiredSig

    _verify_params_match_query(request)
