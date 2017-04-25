import re
from bson.errors import InvalidId
from collections import namedtuple
from django.http.response import Http404
from django_mongo_rest.models import FindParams
from django_mongo_rest.utils import EnumValueError

Param = namedtuple('Param', 'name type_cast required max_len min_len max min let choices')
Param.__new__.__defaults__ = (None, False, None, None, None, None, None, None)

EMAIL_REGEX = re.compile(
    # dot-atom
    r"(^[-!#$%&'*+/=?^_`{}|~0-9A-Z]+(\.[-!#$%&'*+/=?^_`{}|~0-9A-Z]+)*"
    # quoted-string
    r'|^"([\001-\010\013\014\016-\037!#-\[\]-\177]|\\[\001-011\013\014\016-\177])*"'
    # domain (max length of an ICAAN TLD is 22 characters)
    r')@(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}|[A-Z0-9-]{2,}(?<!-))$', re.IGNORECASE
)
def email_validator(value):
    if not EMAIL_REGEX.match(value):
        raise ValueError('Invalid email: %s' % value)
    return value

def date_str_validator(value):
    match = re.match('^[0-9]+(-[0-9]+)?$', value)
    if not match:
        raise ValueError('Invalid date, expected "mm-yyyy" or "yyyy": %s' % value)

    if '-' in value:
        year, month = value.split('-')
        month = int(month)
    else:
        month = 0
        year = value

    year = int(year)

    if month < 0 or month > 12:  # 0 means unknown month
        raise ValueError('Invalid month: %s' % value)

    if year < 1900 or year > 2030:
        raise ValueError('Invalid year: %s' % value)

    if month:
        return '%.4d-%.2d' % (year, month)
    return '%.4d' % year

class ModelById(object):
    def __init__(self, model_cls):
        self.model_cls = model_cls

    def __call__(self, request, i):
        doc = self.model_cls.find_by_id(i, params=FindParams(request=request))
        if not doc:
            raise Http404(self.model_cls.msg404(obj_id=i))
        return doc

def type_cast(request, param, val):
    if not param.type_cast:
        return val

    if isinstance(param.type_cast, ModelById):
        return param.type_cast(request, val)
    else:
        try:
            return param.type_cast(val)
        except (ValueError, TypeError, InvalidId, EnumValueError) as e:
            if param.type_cast == email_validator:
                raise ValueError('Must be an email')
            elif isinstance(e, EnumValueError):
                raise ValueError(e.message)
            else:
                raise ValueError('Must be of type %s' % param.type_cast.__name__)

def get_param(dct, param, request):
    val = dct.get(param.name)
    if isinstance(val, (str, unicode)):
        val = val.strip()

    # 0 int or False are ok
    if val is None or val == '':
        if param.required:
            raise ValueError('Is required')
        else:
            return val

    val = type_cast(request, param, val)

    if param.max_len is not None and len(val) > param.max_len:
        raise ValueError('Must be at most %d characters' % param.max_len)

    if param.min_len is not None and len(val) < param.min_len:
        raise ValueError('Must be at least %d characters' % param.min_len)

    if param.max is not None and val > param.max:
        raise ValueError('Must be <= %s' % str(param.max))

    if param.min is not None and val < param.min:
        raise ValueError('Must be >= %s' % str(param.min))

    if param.choices is not None and val.lower() not in param.choices:
        raise ValueError('Must be one of %s' % str(param.choices))

    return val

def get_params(dct, params, request):
    errors = {}
    resolved_params = {}
    for param in params:
        try:
            resolved_params[param.name] = get_param(dct, param, request)
        except ValueError as e:
            errors[param.name] = e.message

    for param in params:
        if (param.name in resolved_params and param.let and param.let in resolved_params and
                resolved_params[param.name] > resolved_params[param.let]):
            errors[param.name] = 'Must be <= %s' % param.let

    if errors:
        raise ValueError(errors)
    return resolved_params
