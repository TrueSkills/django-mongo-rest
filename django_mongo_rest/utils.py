from bson import ObjectId
from datetime import datetime
from enum import Enum as enum_Enum

def to_list(x):
    if isinstance(x, (list, tuple)):
        return x
    return [x]

def pluralize(s):
    if s.endswith('y'):
        return s[:-1] + 'ies'
    if s.endswith('s'):
        return s + 'ses'
    return s + 's'

# http://stackoverflow.com/questions/27973988/python-how-to-remove-all-empty-fields-in-a-nested-dict
def purge_empty_values(d):
    if not isinstance(d, (dict, list)):
        return d
    if isinstance(d, list):
        return [v for v in (purge_empty_values(v) for v in d) if v]
    return {k: v for k, v in ((k, purge_empty_values(v)) for k, v in d.items()) if v}

class EnumValueError(ValueError):
    def __init__(self, enum_cls):
        msg = 'Must be one of %s' % str([k.lower() for k in enum_cls.reverse_dict.keys()])
        super(EnumValueError, self).__init__(msg)

class Enum(enum_Enum):
    @classmethod
    def choices(cls):
        return [choice.value for choice in cls._member_map_.values()]

    @classmethod
    def choices_dict(cls):
        return {choice.value: choice.name for choice in cls._member_map_.values()}

    @classmethod
    def to_display(cls, val):
        return cls.choices_dict()[val].lower()

    @classmethod
    def reverse(cls, val):
        if not hasattr(cls, 'reverse_dict'):
            cls.reverse_dict = {v.lower() : k for k, v in cls.choices_dict().iteritems()}
        try:
            return cls.reverse_dict[val.lower()]
        except KeyError:
            raise EnumValueError(cls)

def json_default_serializer(obj):
    if isinstance(obj, (datetime, ObjectId)):
        return str(obj)
    raise Exception('Can\'t serialize {} {}'.format(type(obj), obj))