import pytz
from bson import ObjectId
from collections import namedtuple, defaultdict
from copy import deepcopy
from datetime import datetime
from django.http.response import Http404
from django.utils.timezone import now
from django_mongo_rest import serialize, ApiException, ApiView, audit
from django_mongo_rest.models import FindParams, UpdateParams, ModelPermissionException
from django_mongo_rest.shortcuts import (get_object_or_404, get_orm_object_or_404_by_id,
                                         get_object_or_404_by_id)
from django_mongo_rest.utils import pluralize
from mongoengine import (ReferenceField, StringField, EmbeddedDocumentListField, ListField, ObjectIdField)
from mongoengine.errors import ValidationError
from pymongo.errors import DuplicateKeyError
from six import string_types
from time import sleep

model_registry = {}

class ImproperlyConfigured(Exception):
    pass

def _get_duplicate_model(model_class, model):
    for idx in model_class.get_unique_indices():
        query = {field: model.get(field) for field, _ in idx['fields']}
        duplicate = model_class.find_one(**query)
        if duplicate:
            return duplicate

def remove_empty_lists(doc):
    for k, v in doc.items():
        if v == []:
            del doc[k]
        elif isinstance(v, dict):
            remove_empty_lists(v)

def _to_mongo(doc):
    doc = doc.to_mongo()
    remove_empty_lists(doc)
    return doc

def _extract_embedded_document_list(request, field, input_list, allowed_fields, permission_exempt_fields):
    if not isinstance(input_list, list):
        raise ValidationError(errors='expected array')
    document_type = field.field.document_type
    the_list = []
    errors = {}
    for i, subdoc in enumerate(input_list):
        subdoc_errors = {}
        subdoc = _extract_request_model_recursive(document_type, request, subdoc, allowed_fields,
                                                  subdoc_errors, [], permission_exempt_fields)
        if subdoc_errors:
            errors[i] = subdoc_errors
            continue

        if subdoc.to_mongo():
            the_list.append(subdoc)

    if errors:
        raise ValidationError('Validation Error', errors=errors)

    return the_list

def _display_to_enum(field, val):
    choices = getattr(field, 'choices', None)
    if not choices:
        return None

    choices_dict = {c[1].lower(): c[0] for c in choices}
    try:
        return choices_dict[val.lower()]
    except KeyError:
        error_msg = 'Must be one of %s' % str([k.lower() for k in choices_dict.keys()])
        raise ValidationError(errors=error_msg)

def _process_value(request, field, val, allowed_fields, permission_exempt_fields):
    if isinstance(field, ReferenceField):
        kwargs = {}
        if field.name not in permission_exempt_fields:
            kwargs['request'] = request

        if not field.document_type.exists(id=val, **kwargs):
            raise ValidationError(errors=field.document_type.msg404(obj_id=val))
        return field.to_python(val)

    elif isinstance(field, StringField):
        if isinstance(val, string_types) and not len(val):
            return None

        enum_val = _display_to_enum(field, val)
        if enum_val:
            return enum_val
    elif isinstance(field, EmbeddedDocumentListField):
        return _extract_embedded_document_list(request, field, val or [], allowed_fields[field.name],
                                               permission_exempt_fields)

    elif isinstance(field, ListField):
        if not isinstance(val, list):
            raise ValidationError(errors='expected array')
        choices = getattr(field.field, 'choices', None)
        if choices:
            choices_dict = {c[1].lower(): c[0] for c in choices}
            try:
                return [choices_dict[v.lower()] for v in val]
            except KeyError:
                error_msg = 'Must be one of %s' % str([k.lower() for k in choices_dict.keys()])
                raise ValidationError(errors=error_msg)
        val = [field.field.to_python(v) for v in val]

    return val

def _validate_field(field, val):
    field.validate(val)
    if hasattr(field, 'validator') and val is not None:
        val = field.validator(val)
    return val

def _extract_request_model_field(request, input_data, field, allowed_fields, errors,
                                 permission_exempt_fields):
    # pylint: disable=too-many-arguments
    if field.name not in allowed_fields or field.name not in input_data:
        return None, False

    val = input_data[field.name]

    try:
        val = _process_value(request, field, val, allowed_fields, permission_exempt_fields)
    except ValidationError as e:
        errors[field.name] = e.errors
        return None, False

    return val, True

def _extract_request_query_recursive(model_class, doc, request, input_data, allowed_fields, errors,
                                     permission_exempt_fields):
    # pylint: disable=too-many-arguments
    query = {}

    if hasattr(allowed_fields, '__call__'):
        allowed_fields = allowed_fields(request, doc.to_mongo())

    for allowed_field in allowed_fields:
        field = model_class._fields[allowed_field]
        val, extracted = _extract_request_model_field(request, input_data, field, allowed_fields,
                                                      errors, permission_exempt_fields)
        if not extracted:
            continue

        if val is not None:
            try:
                val = _validate_field(field, val)
            except (ValueError, ValidationError) as e:
                errors[field.name] = e.message

        query[field.name] = val

    return query

def _extract_request_model_recursive(model_class, request, input_data, allowed_fields, errors,
                                     permission_exempt_fields, existing=None):
    # pylint: disable=too-many-arguments
    doc = deepcopy(existing) or model_class()
    if input_data:
        query = _extract_request_query_recursive(model_class, doc, request, input_data, allowed_fields,
                                                 errors, permission_exempt_fields)
        for k, v in query.iteritems():
            setattr(doc, k, v)

    try:
        doc.validate()
    except ValidationError as e:
        e.errors.update(errors)  # errors that already exist should take precedence
        errors.update(e.errors)  # return by reference

    return doc

Filter = namedtuple('Filter', 'field type_cast preserve_case')
Filter.__new__.__defaults__ = (None, False)

class ModelView(ApiView):
    duplicate_key_ok = True
    audit = True

    '''If there are any foreign keys, ModelView will automatically check permissions when updating them.
    This is a whitelist of fields where we should ignore permissions'''
    permission_exempt_fields = ()

    real_delete = False  # Whether to really delete documents or to only mark them as deleted
    filters = {}  # hash of Filters to be used with list api
    sortable_fields = []

    SORTABLE_ALL = 'sortable_all'

    def auto_populate_new_model(self, request, obj):
        raise NotImplementedError()

    def post_process_model(self, request, obj):
        raise NotImplementedError()

    def __init__(self):
        super(ModelView, self).__init__()
        self._verify_configuration()
        model_registry[self.model.get_collection_name()] = self.model

    def main(self, request, obj_id=None, **kwargs):
        method_map = {
            'GET': self.get,
            'POST': self.create,
            'PATCH': self.update,
            'DELETE': self.delete,
        }
        if request.method not in method_map:
            raise ApiException(request.method + ' not allowed', 405)
        return method_map[request.method](request, obj_id, **kwargs)

    def _supports_method(self, method):
        # pylint: disable=unsupported-membership-test
        return hasattr(self.allowed_methods, '__call__') or method in self.allowed_methods

    def _verify_allowed_fields_config(self, name):
        fields = getattr(self, name, None)
        if not fields or hasattr(fields, '__call__'):
            return

        model_fields = self.model._fields
        try:
            embedded_list = next(field for field in fields
                                 if isinstance(model_fields[field], EmbeddedDocumentListField))
        except StopIteration:
            return

        if not isinstance(fields, dict):
            raise ImproperlyConfigured(('{} has an EmbeddedDocumentListField that is editable (%s). ' +
                                        'We need to know which of its fields are editable, which means %s ' +
                                        'must be a dictionary') % (embedded_list, name))

    def _verify_configuration(self):
        if not hasattr(self, 'model'):
            raise ImproperlyConfigured('self.model not defined')
        if not hasattr(self, 'permissions'):
            # No default on purpose so people don't forget to set permissions
            raise ImproperlyConfigured('self.permissions not defined. ' +
                                       'Use empty array if you want no permissioning')
        if self.allowed_methods is None:
            raise ImproperlyConfigured('self.allowed_methods not defined')
        if self._supports_method('PATCH') and not hasattr(self, 'editable_fields'):
            raise ImproperlyConfigured('self.editable_fields not defined ' +
                                       '(the fields a user has permission to set when editing a model)')
        if self._supports_method('POST') and not hasattr(self, 'initial_fields'):
            raise ImproperlyConfigured('self.initial_fields not defined ' +
                                       '(the fields a user has permission to set when creating a model)')

        self._verify_allowed_fields_config('initial_fields')
        self._verify_allowed_fields_config('editable_fields')

    def get(self, request, obj_id, **kwargs):
        if obj_id:
            return self.get_by_id(request, obj_id)
        elif request.GET.get('ids'):
            return self.get_by_ids(request, request.GET['ids'].split(','))
        else:
            return self.get_list(request, **kwargs)

    def get_by_id(self, request, obj_id):
        query = {'_id': obj_id}
        self._filter(request, query, {})
        obj = get_object_or_404(self.model, request, query)
        serialized = serialize(self.model, obj, request)
        return {'object': serialized}

    def get_by_ids(self, request, ids):
        query = {'_id': {'$in': ids}}
        self._filter(request, query, {})
        params = FindParams(request=None if request.user.is_superuser else request)
        try:
            objs = list(self.model.find(params=params, **query))
        except ModelPermissionException:
            raise ApiException(self.model.msg404(), 404)

        if len(objs) != len(ids):
            found_ids = {str(obj['_id']) for obj in objs}
            missing_ids = [i for i in ids if i not in found_ids]
            raise ApiException(', '.join(missing_ids) + ' not found', 404)
        serialized = serialize(self.model, objs, request)
        return {'objects': serialized}

    def _filter(self, request, query, view_kwargs):
        filter_args = {}
        filter_args.update(view_kwargs)
        filter_args.update(request.GET.items())
        for k, v in filter_args.iteritems():
            operator = None
            if '__' in k:
                k, operator = k.split('__')

            if k in self.filters:
                flter = self.filters[k]
                if flter.type_cast:
                    v = flter.type_cast(v)
                if isinstance(v, (str, unicode)) and not flter.preserve_case:
                    v = v.lower()

                field = getattr(self.model, flter.field, None)
                try:
                    if isinstance(v, list):
                        v = [_display_to_enum(field, e) or e for e in v]
                    else:
                        v = _display_to_enum(field, v) or v
                except ValidationError as e:
                    raise ApiException(e.to_dict(), 400)

                query[flter.field] = {'$in': v} if isinstance(v, list) else v

        if hasattr(self, 'process_filter'):
            self.process_filter(request, query)

    @staticmethod
    def _paginate(request, cursor):
        if request.GET.get('skip'):
            try:
                cursor.skip(int(request.GET['skip']))
            except (TypeError, ValueError):
                raise ApiException('skip must be an integer', 400)

        DEFAULT_LIMIT = 10
        if request.GET.get('cnt'):
            try:
                cursor.limit(int(request.GET.get('cnt')) or DEFAULT_LIMIT)
            except (TypeError, ValueError):
                raise ApiException('cnt must be an integer', 400)
        else:
            cursor.limit(DEFAULT_LIMIT)

    def _sort(self, request, cursor):
        if not request.GET.get('sort'):
            return

        sort_field = request.GET.get('sort')
        if (self.sortable_fields != self.SORTABLE_ALL and sort_field not in self.sortable_fields):
            raise ApiException('Unknown sort field: %s. Allowed are %s' %
                               (sort_field, self.sortable_fields), 400)

        fields_map = {}
        for f in self.model.serialize_fields:
            if isinstance (f, tuple):
                fields_map[f[1]] = f[0]
            elif f == '_id':
                fields_map['id'] = f
            else:
                fields_map[f] = f

        if sort_field not in fields_map:
            raise ApiException('Unknown sort field: %s. Allowed are %s' %
                               (sort_field, self.sortable_fields), 400)

        try:
            direction = int(request.GET.get('sortDir', 1))
        except (TypeError, ValueError):
            raise ApiException('sortDir must be either 1 or -1', 400)
        if direction not in [1, -1]:
            raise ApiException('sortDir must be either 1 or -1', 400)

        cursor.sort(fields_map[sort_field], direction=direction)

    def get_list(self, request, **kwargs):
        params = FindParams(request=request)

        query = {}
        if request.GET.get('mine'):
            query.update(self.model.allowed_update_query(request))

        self._filter(request, query, kwargs)

        try:
            cursor = self.model.find(params=params, **query)
        except ModelPermissionException:
            num_matches = 0
            objs = []
        else:
            num_matches = cursor.count()
            self._paginate(request, cursor)
            self._sort(request, cursor)
            objs = list(cursor)

        serialized = serialize(self.model, objs, request)
        return {'objects': serialized,
                'num_matches': num_matches}

    def compute_changed_fields(self, request, existing_doc, new_doc, explicit=True):
        changed_fields = []

        for field in self.model._fields.itervalues():
            if explicit and field.name not in request.dmr_params:
                continue

            existing_val = existing_doc.get(field.name)
            new_val = new_doc.get(field.name)

            isNone = new_val is None
            if (isNone and existing_val is not None or
                    not isNone and existing_val != new_val):
                changed_fields.append(field.name)

        return changed_fields

    def extract_request_model(self, request, input_data, allowed_fields, existing=None):
        errors = {}
        doc = _extract_request_model_recursive(self.model, request, input_data, allowed_fields,
                                               errors, self.permission_exempt_fields,
                                               existing=existing)

        if errors:
            raise ValidationError('Validation Error', errors=errors)

        doc = _to_mongo(doc)
        existing = _to_mongo(existing) if existing else {}
        request.model_view_changed_fields = self.compute_changed_fields(request, existing, doc)

        try:
            self.post_process_model(request, doc)
        except NotImplementedError:
            pass

        if request.GET.get('dmr_sleep'):  # for testing race conditions
            sleep(float(request.GET['dmr_sleep']))

        return doc

    def _prune_uneditable_fields(self, doc, allowed_fields):
        for field in doc:
            if field not in allowed_fields:
                del doc[field]
            elif isinstance(doc[field], dict):
                self._prune_uneditable_fields(doc[field], allowed_fields[field])

    def _create_audit_log(self, request, doc, action, allowed_fields):
        if not self.audit:
            return

        updates = {}
        if action != audit.ACTIONS.DELETE:
            if not request.model_view_changed_fields and action == audit.ACTIONS.UPDATE:
                # Nothing was actually updated
                return

            # Remove fields which user didn't actually edit, like _id, from embedded documents
            doc = deepcopy(doc)
            for field in request.model_view_changed_fields:
                if field not in doc:
                    updates[field] = None
                    continue

                if isinstance(doc[field], dict):
                    self._prune_uneditable_fields(doc[field], allowed_fields[field])
                elif isinstance(doc[field], list) and len(doc[field]) and isinstance(doc[field][0], dict):
                    for embedded in doc[field]:
                        self._prune_uneditable_fields(embedded, allowed_fields[field])

                updates[field] = doc[field]

        audit.create(request, action, self.model, doc, updates)

    def create(self, request, obj_id):
        if obj_id:
            raise Http404()

        try:
            obj = self.extract_request_model(request, request.dmr_params, self.initial_fields)
        except ValidationError as e:
            raise ApiException(e.to_dict(), 400)

        obj['last_updated'] = now()

        try:
            self.auto_populate_new_model(request, obj)
        except NotImplementedError:
            pass

        try:
            self.model.insert_one(obj)
        except DuplicateKeyError as e:
            '''Usually this means a duplicate request (user pressed button twice or browser sent request
            twice) and we want to ignore it.'''
            if not self.duplicate_key_ok:
                raise ApiException('Duplicate object', 400, 'DUP')

            obj = _get_duplicate_model(self.model, obj)
            if not obj:
                raise Exception('DuplicateKeyError, but no duplicate found')
        else:
            self._create_audit_log(request, obj, audit.ACTIONS.CREATE, self.initial_fields)

        return {'id': str(obj['_id'])}

    def _extract_array_query(self, request, op, existing_doc):
        if not isinstance(request.dmr_params[op], dict):
            raise ValidationError(errors={op: 'expected object'})

        errors = defaultdict(dict)
        query = _extract_request_query_recursive(self.model, existing_doc, request, request.dmr_params[op], self.editable_fields,
                                         errors, self.permission_exempt_fields)

        for k, v in query.iteritems():
            if not isinstance(self.model._fields[k], ListField):
                errors[k] = '%s is not an array, %s not supported' % (k, op)
                continue

            if isinstance(self.model._fields[k], EmbeddedDocumentListField):
                v = [v.to_mongo() for v in v]

            request.model_view_changed_fields.append(k)
            if op == '$pull':
                query[k] = {'$or': v}
            else:
                query[k] = {'$each': v}

        if errors:
            raise ValidationError(errors=errors)

        return query


    def update(self, request, obj_id):
        if not obj_id:
            raise Http404()

        existing_model = get_orm_object_or_404_by_id(self.model, request, obj_id)
        self._refuse_conflicting_update(request.dmr_params, request, existing_model)

        errors = {}
        query = {'last_updated': now()}

        try:
            obj = self.extract_request_model(request, request.dmr_params, self.editable_fields,
                                             existing=existing_model)
        except ValidationError as e:
            errors.update(e.errors)

        existing_keys = set(query.keys())

        for op in ('$push', '$pull', '$addToSet'):
            if not request.dmr_params.get(op):
                continue

            try:
                query[op] = self._extract_array_query(request, op, existing_model)
            except ValidationError as e:
                errors[op] = e.errors
            else:
                for k in query[op]:
                    if k in existing_keys:
                        errors[k] = 'Appears twice'
                    existing_keys.add(k)

        if errors:
            raise ApiException(ValidationError(errors=errors).to_dict(), 400)

        unset = []
        for field_name in self.compute_changed_fields(request, _to_mongo(existing_model), obj, explicit=False):
            val = obj.get(field_name)
            if val is None or val == []:
                unset.append(field_name)
            else:
                query[field_name] = val

            if field_name in existing_keys:
                errors[field_name] = 'Appears twice'
            existing_keys.add(field_name)

        if errors:
            raise ApiException(ValidationError(errors=errors).to_dict(), 400)

        update_params = UpdateParams(request=None if request.user.is_superuser else request, unset=unset)

        try:
            updated = self.model.find_one_and_update({'_id': obj_id}, query, update_params=update_params)
        except ModelPermissionException:
            raise ApiException(self.model.msg404(), 400)
        except DuplicateKeyError:
            raise ApiException('Duplicate object', 400, 'DUP')
        else:
            if isinstance(self.model.id, ObjectIdField):
                obj['_id'] = ObjectId(obj_id)
            else:
                obj['_id'] = obj_id
            self._create_audit_log(request, updated, audit.ACTIONS.UPDATE, self.editable_fields)

        if not updated:
            raise ApiException(self.model.msg404(), 404)

    def _refuse_conflicting_update(self, input_data, request, existing_model):
        '''
        Check if user is trying to update an old version of this object

        Useful if 2 users can update the same object from different browsers, ui can show the changes
        and ask for confirmation.
        '''
        request_last_updated = input_data.get('last_updated')
        if not request_last_updated:
            return

        try:
            request_last_updated = int(request_last_updated)
        except ValueError:
            raise ApiException({'last_updated': 'expected integer'}, 400)
        request_last_updated = datetime.utcfromtimestamp(request_last_updated).replace(tzinfo=pytz.utc)

        existing_model = existing_model.to_mongo()
        if existing_model.get('last_updated', datetime(1970, 1, 1)) > request_last_updated:
            up_to_date_obj = serialize(self.model, existing_model, request)
            raise ApiException('Object is out of date', 409, new_obj=up_to_date_obj)

    def delete(self, request, obj_id):
        update_params = UpdateParams(request=None if request.user.is_superuser else request)

        if self.real_delete:
            try:
                res = self.model.delete_by_id(obj_id, request=request)
            except ModelPermissionException:
                raise ApiException(self.model.msg404(), 404)

            if not res.deleted_count:
                raise ApiException(self.model.msg404(), 404)
        else:
            try:
                res = self.model.update_by_id(obj_id, update_params=update_params, deleted=True)
            except ModelPermissionException:
                raise ApiException(self.model.msg404(), 404)

            if not res.matched_count:
                raise ApiException(self.model.msg404(), 404)

        if isinstance(self.model.id, ObjectIdField):
            obj_id = ObjectId(obj_id)
        self._create_audit_log(request, {'_id': obj_id}, audit.ACTIONS.DELETE, None)
