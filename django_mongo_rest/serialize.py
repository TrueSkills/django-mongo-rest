from bson import ObjectId
from django_mongo_rest.utils import to_list
from mongoengine import EmbeddedDocumentField, ListField, ReferenceField, Document
from mongoengine.base.datastructures import BaseList

def _document_typeof(doc_cls, field_name):
    try:
        orm_field = doc_cls._fields[field_name]
    except (KeyError, AttributeError):
        return None

    if isinstance(orm_field, ListField):
        orm_field = orm_field.field
    if isinstance(orm_field, (ReferenceField, EmbeddedDocumentField)):
        return orm_field.document_type

    return None

def _serialize_list(lst, list_element_doc_cls, request, foreign_key_cache):
    if not list_element_doc_cls:
        # List of ints or something
        return lst

    # List of embedded documents. Serialize recursively
    return [_serialize(list_element_doc_cls, doc, request, foreign_key_cache) for doc in lst]

def _is_dereference_disabled(field_name):
    '''Usually we deference ObjectIds in ReferenceFields. But that can be disabled by appending '_id'
    to the field name in serialize_fields'''
    return field_name.endswith('_id') and field_name != '_id'

def _is_foreign_key(field_cls, field_name):
    return field_cls and issubclass(field_cls, Document) and not _is_dereference_disabled(field_name)

def _resolve_field(doc, field_name):
    if field_name in doc:
        return doc[field_name]

    if _is_dereference_disabled(field_name):
        return _resolve_field(doc, field_name[:-len('_id')])

    raise AttributeError

def _resolve_field_recursive(doc_cls, doc, field_segments, foreign_key_cache):
    field = ''
    for i, field in enumerate(field_segments):
        doc = _resolve_field(doc, field)
        prev_doc_cls = doc_cls
        doc_cls = _document_typeof(doc_cls, field)

        if isinstance(doc, (BaseList, list)):
            if len(field_segments) > i + 1:
                raise NotImplementedError('Serializing individual fields from ' +
                                          'embedded document list is unsupported')
            field_def = prev_doc_cls._fields.get(field)
            if field_def:
                choices = getattr(field_def.field, 'choices', None)
                if choices:
                    choices_dict = dict(choices)
                    doc = [choices_dict[v].lower() for v in doc]
                return doc_cls, doc

        elif _is_foreign_key(doc_cls, field):
            doc = foreign_key_cache[doc_cls][doc]

    if prev_doc_cls:
        choices = getattr(prev_doc_cls._fields.get(field), 'choices', None)
        if choices:
            doc = dict(choices)[doc].lower()

    return doc_cls, doc

def _serialize_field(value_cls, value, request, foreign_key_cache):
    if value_cls and hasattr(value_cls, 'serialize_fields'):
        return _serialize(value_cls, value, request, foreign_key_cache)
    elif isinstance(value, (BaseList, list)):
        return _serialize_list(value, value_cls, request, foreign_key_cache)
    return value

def _get_fields_to_serialize(doc_cls, include_fields=None):
    if include_fields:
        fields_to_serialize = []
        for field in doc_cls.serialize_fields:
            key = field[0] if isinstance(field, tuple) else field
            if key in include_fields:
                fields_to_serialize.append(field)
    else:
        fields_to_serialize = doc_cls.serialize_fields

    return fields_to_serialize

def _dereference(docs, field_name, document_type):
    ids = [doc[field_name] for doc in docs if field_name in doc]
    return {foreign['_id']: foreign for foreign in document_type.find_by_ids(ids)}

def _prefetch_foreign_keys(doc_cls, dicts, field_names):
    '''If we're serializing a list and each member of that list has a foreign key
    that we need to dereference, we should make only one query to dereference them all.'''
    foreign_key_cache = {}
    for field_name in field_names:
        if isinstance(field_name, tuple):
            field_name = field_name[0]

        segments = field_name.split('.')
        field_name = segments[0]
        foreign_doc_cls = _document_typeof(doc_cls, field_name)
        if _is_foreign_key(foreign_doc_cls, field_name):
            foreign_docs_by_id = _dereference(dicts, field_name, foreign_doc_cls)
            foreign_key_cache[foreign_doc_cls] = foreign_docs_by_id
            foreign_doc_fields = _get_fields_to_serialize(foreign_doc_cls, include_fields=segments[1:])

            sub_cache = _prefetch_foreign_keys(foreign_doc_cls, foreign_docs_by_id.itervalues(),
                                               foreign_doc_fields)
            for subdoc_cls, cache in sub_cache.items():
                foreign_key_cache.setdefault(subdoc_cls, {}).update(cache)

    return foreign_key_cache

def _serialize(doc_cls, dicts, request, foreign_key_cache, include_fields=None):
    is_multiple = isinstance(dicts, (list, tuple))
    dicts = to_list(dicts)

    fields_to_serialize = _get_fields_to_serialize(doc_cls, include_fields=include_fields)

    if foreign_key_cache is None:
        foreign_key_cache = _prefetch_foreign_keys(doc_cls, dicts, fields_to_serialize)

    if hasattr(doc_cls, 'serialize_preprocess'):
        doc_cls.serialize_preprocess(request, dicts)

    res = []
    for dct in dicts:

        fields = {}
        for field in fields_to_serialize:
            if isinstance(field, tuple):
                field, display = field
            else:
                display = field

            if display == '_id':
                display = 'id'

            # Recursively follow fields. i.e. template.body resolves to obj['template']['body']
            segments = field.split('.')

            try:
                value_cls, value = _resolve_field_recursive(doc_cls, dct, segments, foreign_key_cache)
            except AttributeError:
                continue
            else:
                fields[display] = _serialize_field(value_cls, value, request, foreign_key_cache)
                if isinstance(fields[display], ObjectId):
                    fields[display] = str(fields[display])
        res.append(fields)

    if is_multiple:
        return res
    return res[0]

def serialize(doc_cls, dicts, request, include_fields=None):
    return _serialize(doc_cls, dicts, request, None, include_fields=include_fields)
