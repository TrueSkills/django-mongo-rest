from bson import ObjectId
from bson.errors import InvalidId
from collections import namedtuple
from mongoengine import Document, DateTimeField, BooleanField, StringField, DecimalField, ObjectIdField
from mongoengine.errors import InvalidQueryError
from mongoengine.queryset import Q
from pymongo.errors import DuplicateKeyError

FindParams = namedtuple('FindParams', 'projection sort limit request batch_size')
FindParams.__new__.__defaults__ = (None, None, 0, None, 0)

UpdateParams = namedtuple('UpdateByIdParams', 'unset upsert request')
UpdateParams.__new__.__defaults__ = ((), False, None)

class ModelPermissionException(Exception):
    pass

class BaseModel(Document):
    meta = {'abstract': True}

    last_updated = DateTimeField()
    deleted = BooleanField()

    @classmethod
    def allowed_find_query(cls, request):
        '''Returns a query that would find only records this user has permission to see'''
        raise NotImplementedError

    @classmethod
    def allowed_update_query(cls, request):
        '''Returns a query that would find only records this user has permission to edit'''
        raise NotImplementedError

    @classmethod
    def _get_lookup_query(cls, query, allow_deleted=False):
        if 'id' in query:
            query['_id'] = query.pop('id')
        if '_id' in query and isinstance(query['_id'], (str, unicode)):
            try:
                query['_id'] = ObjectId(query['_id'])
            except InvalidId:
                pass

        if not allow_deleted:
            query['deleted'] = None

        return query

    @classmethod
    def _get_lookup_query_find(cls, query, allow_deleted=False, request=None):
        query = cls._get_lookup_query(query, allow_deleted=allow_deleted)
        if request:
            query = {
                '$and': [query, cls.allowed_find_query(request)]
            }
        return query

    @classmethod
    def _get_lookup_query_update(cls, query, allow_deleted=False, request=None):
        query = cls._get_lookup_query(query, allow_deleted=allow_deleted)
        if request:
            query = {
                '$and': [query, cls.allowed_update_query(request)]
            }
        return query

    @classmethod
    def find(cls, params=FindParams(), **kwargs):
        query = cls._get_lookup_query_find(kwargs, request=params.request)
        return cls._get_collection().find(query, projection=params.projection, sort=params.sort,
                                          limit=params.limit, batch_size=params.batch_size)

    @classmethod
    def find_one(cls, params=FindParams(), **kwargs):
        query = cls._get_lookup_query_find(kwargs, request=params.request)
        return cls._get_collection().find_one(query, projection=params.projection)

    @classmethod
    def get_orm(cls, params=FindParams(), **kwargs):
        query = cls._get_lookup_query_find(kwargs, request=params.request)
        try:
            return cls.objects.no_dereference().get(**query)
        except InvalidQueryError:
            # Query contains $or or something
            return cls.objects.no_dereference().get(Q(__raw__=query))

    @classmethod
    def get_orm_by_id(cls, i, params=FindParams()):
        return cls.get_orm(params=params, _id=i)

    @classmethod
    def find_by_id(cls, i, params=FindParams()):
        return cls.find_one(_id=i, params=params)

    @classmethod
    def find_by_ids(cls, ids, params=FindParams()):
        ids = [ObjectId(i) for i in ids]
        return cls.find(_id={'$in': ids}, params=params)

    @classmethod
    def find_by_ids_ordered(cls, ids, params=FindParams(), strict=True):
        # pylint: disable=no-member
        if isinstance(cls.id, ObjectIdField):
            ids = [ObjectId(i) for i in ids]
        docs = {doc['_id']: doc for doc in cls.find(_id={'$in': ids}, params=params)}
        docs = [docs[i] for i in ids if i in docs]
        if strict and len(docs) != len(ids):
            raise Exception('Some docs not found')
        return docs

    @classmethod
    def count(cls, request=None, **kwargs):
        query = cls._get_lookup_query_find(kwargs, request=request)
        return cls._get_collection().count(query)

    @classmethod
    def exists(cls, request=None, **kwargs):
        params = FindParams(request=request, projection={'_id': 1})
        return bool(cls.find_one(params=params, **kwargs))

    @classmethod
    def _get_update_query(cls, unset=(), **kwargs):
        upd = {}
        if '$push' in kwargs:
            upd['$push'] = kwargs.pop('$push')
        if '$addToSet' in kwargs:
            upd['$addToSet'] = kwargs.pop('$addToSet')
        if '$pull' in kwargs:
            upd['$pull'] = kwargs.pop('$pull')
        if '$inc' in kwargs:
            upd['$inc'] = kwargs.pop('$inc')
        if kwargs:
            upd['$set'] = kwargs
        if unset:
            upd['$unset'] = {k: 1 for k in unset}
        return upd

    @classmethod
    def update_one(cls, lookup_dict, update_params=UpdateParams(), **kwargs):
        query = cls._get_lookup_query_update(lookup_dict, request=update_params.request)
        upd = cls._get_update_query(unset=update_params.unset, **kwargs)
        return cls._get_collection().update_one(query, upd, upsert=update_params.upsert)

    @classmethod
    def replace_one(cls, lookup_dict, update_params=UpdateParams(), **kwargs):
        query = cls._get_lookup_query_update(lookup_dict, request=update_params.request)
        return cls._get_collection().replace_one(query, kwargs, upsert=update_params.upsert)

    @classmethod
    def update_many(cls, lookup_dict, update_params=UpdateParams(), **kwargs):
        query = cls._get_lookup_query_update(lookup_dict, request=update_params.request)
        upd = cls._get_update_query(unset=update_params.unset, **kwargs)
        return cls._get_collection().update_many(query, upd)

    @classmethod
    def update_by_id(cls, _id, update_params=UpdateParams(), **kwargs):
        return cls.update_one({'_id': _id}, update_params=update_params, **kwargs)

    @classmethod
    def find_one_and_update(cls, lookup_dict, update, update_params=UpdateParams(), return_document=True, projection=None):
        query = cls._get_lookup_query_update(lookup_dict, request=update_params.request)
        upd = cls._get_update_query(unset=update_params.unset, **update)
        return cls._get_collection().find_one_and_update(query, upd, return_document=return_document,
                                                         projection=projection, upsert=update_params.upsert)

    @classmethod
    def delete_one(cls, request=None, **kwargs):
        query = cls._get_lookup_query_update(kwargs, request=request)
        return cls._get_collection().delete_one(query)

    @classmethod
    def delete_by_id(cls, _id, request=None):
        return cls.delete_one(request=request, _id=_id)

    @classmethod
    def delete_many(cls, request=None, **kwargs):
        query = cls._get_lookup_query_update(kwargs, request=request)
        return cls._get_collection().delete_many(query)

    @classmethod
    def insert_one(cls, doc):  # doc is not ** so insert_one can modify it
        has_id = '_id' in doc
        try:
            return cls._get_collection().insert_one(doc)
        except DuplicateKeyError:
            if not has_id:
                doc.pop('_id')  # Don't add an id if insert failed
            raise

    @classmethod
    def insert_many(cls, objs):
        return cls._get_collection().insert_many(objs)

    @classmethod
    def aggregate(cls, *args, **kwargs):
        return cls._get_collection().aggregate(*args, **kwargs)

    @classmethod
    def get_collection_name(cls):
        return cls._get_collection_name()

    @classmethod
    def msg404(cls, obj_id=None):
        return ("%s%s does not exist or you don't have permissions on it" %
                (cls._get_collection_name(), (' ' + str(obj_id)) if obj_id else ''))

    @classmethod
    def get_unique_indices(cls):
        indices = cls._meta['index_specs']
        return [idx for idx in indices if idx.get('unique')]

class LocationMixin(object):
    SERIALIZE_FIELDS = (
        'street',
        'city',
        'state_code',
        'region',
        'country_code',
        'zip_code',
        'lat',
        'lng',
    )

    street = StringField(max_length=64, required=True)
    city = StringField(max_length=32, required=True)
    state_code = StringField(max_length=2, min_length=2, required=True)
    region = StringField(max_length=32, required=True)
    country_code = StringField(max_length=3, min_length=2, required=True)
    zip_code = StringField(max_length=15, required=True)
    lat = DecimalField(precision=20, required=True, min_value=-90, max_value=90)
    lng = DecimalField(precision=20, required=True, min_value=-180, max_value=180)

    @staticmethod
    def get_location(doc):
        return {k: doc[k] for k in LocationMixin.SERIALIZE_FIELDS if k in doc}

    @staticmethod
    def display_location(doc):
        loc = doc.get('city', '')
        if 'state' in doc:
            loc += ', ' + doc['state_code']
        if 'country' in doc:
            loc += ', ' + doc['country_code']

        return loc.lstrip(', ')
