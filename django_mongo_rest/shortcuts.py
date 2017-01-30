from django.http.response import Http404
from django_mongo_rest import ApiException
from django_mongo_rest.models import FindParams, ModelPermissionException

def get_object_or_404(model, request, kwargs):
    try:
        obj = model.find_one(params=FindParams(request=request), **kwargs)
    except ModelPermissionException:
        raise ApiException(model.msg404(), 404)

    if not obj:
        raise ApiException(model.msg404(), 404)
    return obj

def get_object_or_404_by_id(model, request, obj_id, enforce_permissions=True):
    try:
        params = FindParams(request=request) if enforce_permissions else FindParams()
        obj = model.find_by_id(obj_id, params=params)
    except ModelPermissionException:
        raise ApiException(model.msg404(obj_id), 404)

    if not obj:
        raise ApiException(model.msg404(obj_id), 404)
    return obj

def get_orm_object_or_404(model, request, **kwargs):
    try:
        return model.get_orm(params=FindParams(request=request), **kwargs)
    except (model.DoesNotExist, ModelPermissionException):
        raise ApiException(model.msg404(), 404)

def get_orm_object_or_404_by_id(model, request, obj_id, enforce_permissions=True):
    try:
        params = FindParams(request=request) if enforce_permissions else FindParams()
        return model.get_orm_by_id(obj_id, params=params)
    except (model.DoesNotExist, ModelPermissionException):
        raise ApiException(model.msg404(obj_id=obj_id), 404)

def url_id(param_name):
    oid_regex = '[a-f0-9]{24}'
    uuid_regex = '[a-z0-9]{8}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{12}'
    return '(?P<%s>(%s|%s))' % (param_name, oid_regex, uuid_regex)

def url_optional_param(param_name, regex):
    return '(?:(?P<%s>%s)/)?' % (param_name, regex)

def url_optional_id(param_name):
    return '(?:%s/)?' % url_id(param_name)

def page_not_found(_):
    raise Http404()
