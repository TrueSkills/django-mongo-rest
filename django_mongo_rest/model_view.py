import pytz
from bson import ObjectId
from collections import namedtuple
from copy import deepcopy
from datetime import datetime
from django.http.response import Http404
from django.utils.timezone import now
from django_mongo_rest import serialize, ApiException, ApiView, audit
from django_mongo_rest.models import FindParams, UpdateParams, ModelPermissionException
from django_mongo_rest.shortcuts import get_object_or_404_by_id, get_orm_object_or_404_by_id
from django_mongo_rest.utils import pluralize
from mongoengine import (ReferenceField, StringField, EmbeddedDocumentListField, ListField, BooleanField,
                         ObjectIdField)
from mongoengine.errors import ValidationError
from pymongo.errors import DuplicateKeyError
from six import string_types

model_registry = {}

class ImproperlyConfigured(Exception):
    pass

def _get_duplicate_model(model_class, model):
    for idx in model_class.get_unique_indices():
        query = {field: model.get(field) for field, _ in idx['fields']}
        duplicate = model_class.find_one(**query)
        if duplicate:
            return duplicate

def _remove_empty_lists(doc):
    for k, v in doc.items():
        if v == []:
            del doc[k]
        elif isinstance(v, dict):
            _remove_empty_lists(v)

def _extract_embedded_document_list(request, field, input_list, allowed_fields, permission_exempt_fields):
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

        choices = getattr(field, 'choices', None)
        if choices:
            choices_dict = {c[1].lower(): c[0] for c in choices}
            try:
                return choices_dict[val.lower()]
            except KeyError:
                error_msg = 'Must be one of %s' % str([k.lower() for k in choices_dict.keys()])
                raise ValidationError(errors=error_msg)

    elif isinstance(field, EmbeddedDocumentListField):
        return _extract_embedded_document_list(request, field, val or [], allowed_fields[field.name],
                                               permission_exempt_fields)

    elif isinstance(field, ListField):
        choices = getattr(field.field, 'choices', None)
        if choices:
            choices_dict = {c[1].lower(): c[0] for c in choices}
            try:
                return [choices_dict[v.lower()] for v in val]
            except KeyError:
                error_msg = 'Must be one of %s' % str([k.lower() for k in choices_dict.keys()])
                raise ValidationError(errors=error_msg)

    return val

def _extract_request_model_field(request, doc, input_data, field, allowed_fields, errors, changed_fields,
                                 permission_exempt_fields):
    # pylint: disable=too-many-arguments
    if hasattr(allowed_fields, '__call__'):
        allowed_fields = allowed_fields(request, doc.to_mongo())
    if field.name not in allowed_fields or field.name not in input_data:
        return

    val = input_data[field.name]

    try:
        val = _process_value(request, field, val, allowed_fields, permission_exempt_fields)
    except ValidationError as e:
        errors[field.name] = e.errors
        return

    if hasattr(field, 'validator') and val is not None:
        try:
            val = field.validator(val)
        except ValueError as e:
            errors[field.name] = e
            return

    existing_val = getattr(doc, field.name, None)
    # Convert field.to_python because some fields, such as DecimalField, won't be equal otherwise
    # Decimal('3.33000000000000000000') != 3.33
    isNone = val is None
    if (isNone and existing_val is not None or
            not isNone and getattr(doc, field.name, None) != field.to_python(val)):
        changed_fields.append(field.name)

    setattr(doc, field.name, val)

def _extract_request_model_recursive(model_class, request, input_data, allowed_fields, errors,
                                     changed_fields, permission_exempt_fields, existing=None):
    # pylint: disable=too-many-arguments
    doc = existing or model_class()
    if input_data:
        for name, field in model_class._fields.iteritems():
            _extract_request_model_field(request, doc, input_data, field, allowed_fields, errors, changed_fields,
                                         permission_exempt_fields)

    doc.last_updated = now()

    for name, field in model_class._fields.iteritems():
        if hasattr(field, 'less_than_equal_to'):
            greater_field = field.less_than_equal_to
            if (hasattr(doc, name) and hasattr(doc, greater_field) and
                    getattr(doc, name) > getattr(doc, greater_field)):
                errors[name] = 'Must be <= %s' % greater_field

    try:
        doc.validate()
    except ValidationError as e:
        e.errors.update(errors)  # errors that already exist should take precedence
        errors.update(e.errors)  # return by reference

    return doc

Filter = namedtuple('Filter', 'field type_cast preserve_case')
Filter.__new__.__defaults__ = (lambda x: x, False)

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
        elif request.GET.get('ids', []):
            return self.get_by_ids(request, request.GET['ids'])
        else:
            return self.get_list(request, **kwargs)

    def get_by_id(self, request, obj_id):
        obj = get_object_or_404_by_id(self.model, request, obj_id,
                                      enforce_permissions=not request.user.is_superuser)
        serialized = serialize(self.model, obj, request)
        return {self.model.get_collection_name(): serialized,
                'object': serialized}  # Temporarily return both model name and objects until ui is migrated

    def get_by_ids(self, request, ids):
        try:
            params = FindParams(request=None if request.user.is_superuser else request)
            objs = list(self.model.find(params=params, id={'$in': ids}))
        except ModelPermissionException:
            raise ApiException(self.model.msg404(), 404)

        if len(objs != len(ids)):
            found_ids = {str(obj['_id']) for obj in objs}
            missing_ids = [i for i in ids if i not in found_ids]
            raise ApiException(', '.join(missing_ids) + ' not found', 404)
        serialized = serialize(self.model, objs, request)
        return {pluralize(self.model.get_collection_name()): serialized,
                'objects': serialized}  # Temporarily return both model name and objects until ui is migrated

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
                v = flter.type_cast(v)
                if isinstance(v, (str, unicode)) and not flter.preserve_case:
                    v = v.lower()

                query[flter.field] = v

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
        return {pluralize(self.model.get_collection_name()): serialized,
                'objects': serialized, # Temporarily return both model name and objects until ui is migrated
                'num_maches': num_matches}

    def extract_request_model(self, request, input_data, allowed_fields, existing=None):
        errors = {}
        changed_fields = []
        doc = _extract_request_model_recursive(self.model, request, input_data, allowed_fields,
                                               errors, changed_fields, self.permission_exempt_fields,
                                               existing=existing)

        if errors:
            raise ValidationError('Validation Error', errors=errors)

        doc = doc.to_mongo()

        _remove_empty_lists(doc)

        request.model_view_changed_fields = changed_fields
        try:
            self.post_process_model(request, doc)
        except NotImplementedError:
            pass

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

    def update(self, request, obj_id):
        if not obj_id:
            raise Http404()

        existing_model = get_orm_object_or_404_by_id(self.model, request, obj_id)
        self._refuse_conflicting_update(request.dmr_params, request, existing_model)

        try:
            obj = self.extract_request_model(request, request.dmr_params, self.editable_fields,
                                             existing=existing_model)
        except ValidationError as e:
            raise ApiException(e.to_dict(), 400)

        unset = []
        for field_name, field in self.model._fields.iteritems():
            val = request.dmr_params.get(field_name, True)
            if val is None or val == []:
                unset.append(field_name)

        update_params = UpdateParams(request=None if request.user.is_superuser else request, unset=unset)

        try:
            del obj['_id'] # Don't try to update this
            res = self.model.update_by_id(obj_id, update_params=update_params, **obj)
        except ModelPermissionException:
            raise ApiException(self.model.msg404(), 400)
        except DuplicateKeyError:
            raise ApiException('Duplicate object', 400, 'DUP')
        else:
            if isinstance(self.model.id, ObjectIdField):
                obj['_id'] = ObjectId(obj_id)
            else:
                obj['_id'] = obj_id
            self._create_audit_log(request, obj, audit.ACTIONS.UPDATE, self.editable_fields)

        if not res.matched_count:
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
