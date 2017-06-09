import json
from django.conf import settings
from django.shortcuts import redirect
from django.http.response import HttpResponse, Http404, JsonResponse
from django_mongo_rest import ApiException
from django_mongo_rest.auth import is_authorized
from django_mongo_rest.crypto import verify_signature, InvalidSig, ExpiredSig
from django_mongo_rest.utils import to_list, json_default_serializer
from django_mongo_rest.validation import get_params

def _enforce_allowed_methods(request, allowed_methods):
    if hasattr(allowed_methods, '__call__'):
        allowed_methods = allowed_methods(request)
    allowed_methods = to_list(allowed_methods or [])
    allowed_methods = [m.upper() for m in allowed_methods]
    if allowed_methods and request.method not in allowed_methods:
        return False
    return True

CONTENT_TYPE_METHODS = ('POST', 'PATCH')

def _parse_params(request):
    request.dmr_params = {}
    if request.method in CONTENT_TYPE_METHODS:
        if request.content_type != 'application/json':
            request.dmr_params = request.POST
            return

        if not request.body:
            return
        try:
            request.dmr_params = json.loads(request.body)
        except ValueError:
            raise ApiException('This api accepts json encoded data', 400)
    elif request.method in ('GET', 'DELETE', 'HEAD'):
        request.dmr_params = request.GET
    else:
        raise Exception('Method not supported. Please implement.')

class ERROR_CODES(object):
    SIGNATURE = 'SIG'
    EXPIRED_SIGNATURE = 'SIG_EXP'
    PERMISSION = 'PERM'
    PARAMS = 'PARAMS'

class _EndpointView(object):
    allowed_methods = None
    params = None
    permissions = None
    signature_salt = None  # This endpoint expects a signature
    link_valid_seconds = None  # When there is a signature, the number of seconds until the link expires
    expected_content_type = None

    def __init__(self):
        if self.permissions is None:
            raise Exception('Must define permissions')
        self.permissions = to_list(self.permissions)

    def endpoint(self, request, *args, **kwargs):
        if not self.is_authorized(request):
            raise ApiException('Not found', 404, error_code=ERROR_CODES.PERMISSION)

        if not _enforce_allowed_methods(request, self.allowed_methods):
            raise ApiException('%s not supported' % request.method, 405)

        if (request.method in CONTENT_TYPE_METHODS and
                request.content_type not in to_list(self.expected_content_type or [])):
            raise ApiException('Expected Content-Type: %s' % self.expected_content_type, 400)

        _parse_params(request)

        if self.params:
            try:
                kwargs.update(get_params(request.dmr_params, self.params, request))
            except ValueError as e:
                raise ApiException(e.args[0], 400, error_code=ERROR_CODES.PARAMS)
            except Http404 as e:
                raise ApiException(e.args[0], 404)

        if self.signature_salt:
            max_age = self.link_valid_seconds or None
            try:
                verify_signature(request, expected_salt=self.signature_salt, max_age=max_age)
            except InvalidSig:
                raise ApiException('Invalid signature', 403, error_code=ERROR_CODES.SIGNATURE)
            except ExpiredSig:
                raise ApiException('Expired signature', 403, error_code=ERROR_CODES.EXPIRED_SIGNATURE)

        return self.main_wrapper(request, *args, **kwargs)

    def is_authorized(self, request):
        return is_authorized(request, self.permissions)

    def main_wrapper(self, request, *args, **kwargs):
        raise NotImplementedError

class ApiView(_EndpointView):
    expected_content_type = 'application/json'

    def main_wrapper(self, request, *args, **kwargs):
        res = self.main(request, *args, **kwargs)

        if isinstance(res, dict):
            res = JsonResponse(res, json_dumps_params={'default': json_default_serializer})
        elif res is None:
            res = HttpResponse()

        if request.method == 'HEAD':
            res.content = ''

        return res

class PageView(_EndpointView):
    expected_content_type = 'text/plain'

    def endpoint(self, request, *args, **kwargs):
        try:
            return super(PageView, self).endpoint(request, *args, **kwargs)
        except ApiException as e:
            if e.error_code == ERROR_CODES.PARAMS:
                if self.signature_salt:
                    return redirect('/error/invalid_signature/')
                raise Http404()

            if e.error_code == ERROR_CODES.SIGNATURE:
                return redirect('/error/invalid_signature/')

            if e.error_code == ERROR_CODES.EXPIRED_SIGNATURE:
                return redirect('/error/expired_link/')

            if e.status_code != 404:
                return HttpResponse(e.message, status=e.status_code)

            if e.error_code != ERROR_CODES.PERMISSION:
                raise Http404()

            path = request.get_full_path()
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(path, settings.LOGIN_URL, 'next')

    def main_wrapper(self, request, *args, **kwargs):
        res = self.main(request,*args, **kwargs)
        if request.method == 'HEAD':
            res.content = ''
        return res

def get_params_api(dct, params, request):
    try:
        return get_params(dct, params, request)
    except ValueError as e:
        raise ApiException(e.args[0], 400)
    except Http404 as e:
        raise ApiException(e.args[0], 404)
