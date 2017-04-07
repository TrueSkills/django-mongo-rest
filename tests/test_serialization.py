import random
import time
from copy import deepcopy
from itertools import chain

import pytest
from bson import ObjectId
from django_mongo_rest import serialize
from django_mongo_rest.models import BaseModel
from mongoengine import (EmbeddedDocument, EmbeddedDocumentField, EmbeddedDocumentListField,
                         IntField, ListField, ReferenceField, StringField)
from utils import uniquify


class EmbeddedDoc(EmbeddedDocument):
    serialize_fields = ('val',)

    val = IntField()
    val2 = IntField()

class ForeignDoc3(BaseModel):
    serialize_fields = ('val',)

    val = IntField()

class ForeignDoc2(BaseModel):
    serialize_fields = ('val',)

    val = IntField()

class ForeignDoc(BaseModel):
    serialize_fields = ('val', 'foreign2')

    val = IntField()
    foreign2 = ReferenceField(ForeignDoc2)

class SerializationDoc(BaseModel):
    serialize_fields = (
        'val',
        'foreign',  # id into a separate collection the we need to dereference
        'foreign3_id',  # suffixed by '_id', so should NOT dereference
        'embedded',  # embedded document
        'embedded.val2',  # explicitly select this value from the
                          # embedded document even though it's not in serialize_fields
        'embedded_list',  # list of embedded docs
        'with_choices',  # db values should be converted to display
        'with_choices_list',
        'int_list',  # list of ints
        'null_field'  # non-existent field. Should not cause errors
    )

    choices = (('A', 'A_CHOICE'), ('B', 'B_CHOICE'))

    embedded = EmbeddedDocumentField(EmbeddedDoc)
    embedded_list = EmbeddedDocumentListField(EmbeddedDoc)
    with_choices = StringField(choices=choices)
    with_choices_list = ListField(StringField(choices=choices))
    int_list = ListField(IntField)
    foreign = ReferenceField(ForeignDoc)
    foreign3 = ReferenceField(ForeignDoc3)
    val = IntField()

def _embedded_doc():
    return {'val': random.random(), 'val2': random.random()}

def _insert_test_docs(identifier):
    num_items = 1000
    test_foreign2 = [{
        '_id': ObjectId(),
        'identifier': identifier,
        'val': random.random()
    } for i in range(num_items)]
    test_foreign3 = deepcopy(test_foreign2)
    test_foreign = [{
        '_id': ObjectId(),
        'identifier': identifier,
        'val': random.random(),
        'foreign2': test_foreign2[i]['_id']
    } for i in range(num_items)]
    test_docs = [{
        '_id': ObjectId(),
        'identifier': identifier,
        'val': random.random(),
        'foreign': test_foreign[i]['_id'],
        'foreign3': test_foreign3[i]['_id'],
        'with_choices': 'B',
        'with_choices_list': ['A', 'B', 'A'],
        'embedded': _embedded_doc(),
        'embedded_list': [_embedded_doc() for i in range(3)],
        'int_list': [random.randint(0, 10) for i in range(3)],
    } for i in range(num_items)]

    '''Make calls using mongoengine so it authenticates its db connection.
    Otherwise it will authenticate when we make the first query inside serialize().
    One of our tests is that serialization is fast, so we don't want authentication
    confounding that.'''
    ForeignDoc3.insert_many(test_foreign3)
    ForeignDoc2.insert_many(test_foreign2)
    ForeignDoc.insert_many(test_foreign)
    SerializationDoc.insert_many(test_docs)

    return test_docs, test_foreign, test_foreign2

def _expected_serialized(test_docs, test_foreign, test_foreign2):
    test_docs = deepcopy(test_docs)
    test_foreign = deepcopy(test_foreign)
    test_foreign2 = deepcopy(test_foreign2)

    # These fields should not be serialized bc they're not in serialize_fields
    for doc in chain(test_docs, test_foreign, test_foreign2):
        del doc['_id']
        del doc['identifier']

    for doc in test_docs:
        doc['foreign3_id'] = str(doc.pop('foreign3'))
        doc['embedded.val2'] = doc['embedded']['val2']
        del doc['embedded']['val2']
        for embedded in doc['embedded_list']:
            del embedded['val2']
        doc['with_choices'] = 'b_choice'
        doc['with_choices_list'] = ['a_choice', 'b_choice', 'a_choice']

    for i, doc in enumerate(test_docs):
        test_foreign[i]['foreign2'] = test_foreign2[i]
        test_docs[i]['foreign'] = test_foreign[i]

    return test_docs

@pytest.fixture(scope='module')
def documents():
    identifier = uniquify('')
    test_docs, test_foreign, test_foreign2 = _insert_test_docs(identifier)
    expected_serialized = _expected_serialized(test_docs, test_foreign, test_foreign2)

    yield test_docs, test_foreign, test_foreign2, expected_serialized

    SerializationDoc.delete_many(identifier=identifier)
    ForeignDoc.delete_many(identifier=identifier)
    ForeignDoc2.delete_many(identifier=identifier)
    ForeignDoc3.delete_many(identifier=identifier)

def test_one(documents):
    docs, _, _, expected_serialized = documents
    res = serialize(SerializationDoc, docs[0], None)
    assert expected_serialized[0] == res

def test_many(documents):
    docs, _, _, expected_serialized = documents

    begin = time.time()
    res = serialize(SerializationDoc, docs, None)
    elapsed_time = time.time() - begin
    print 'serialization took %.2fs' % elapsed_time

    assert expected_serialized == res

    '''Serialization should be fast
    Even though we have 2000 foreign keys, we should only make 2 queries (1 for each collecion)'''
    assert elapsed_time < 0.0003 * len(docs)
