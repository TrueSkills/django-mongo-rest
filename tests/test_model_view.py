import calendar
from copy import deepcopy
from datetime import datetime, timedelta
from django.utils.timezone import now

import pytest
import pytz
from bson import ObjectId
from django_mongo_rest import serialize
from server.models import PlaygroundModel
from server.settings import MONGODB
from utils import (assert_status, patch_api, post_api, options_api, get_api, delete_api)


def _model(request):
    '''Create a model owned by the correct user'''
    if 'user_session' in request.fixturenames:
        user, _ = request.getfixturevalue('user_session')
    elif 'user_session_const' in request.fixturenames:
        user, _ = request.getfixturevalue('user_session_const')
    else:
        user = {'_id': ObjectId()}

    return {
        'string': 'sdrtsdr',
        'integer': 16,
        'decimal': 3.33,
        'created_by': user['_id'],
        'embedded_list': [
            {'embedded_string': '1'},
            {'embedded_string': '2'}
        ],
        'last_updated': now().replace(microsecond=0) - timedelta(seconds=55)
    }

@pytest.fixture
def model(request):
    model = _model(request)
    MONGODB.playground_model.insert_one(model)
    yield model
    MONGODB.playground_model.delete_one({'_id': model['_id']})

@pytest.fixture
def models(request):
    models = [_model(request) for _ in range(4)]
    MONGODB.playground_model.insert_many(models)
    yield models
    ids = [m['_id'] for m in models]
    MONGODB.playground_model.delete_many({'_id': {'$in': ids}})

def test_get_list_logged_out():
    res = get_api('model_get_only/')
    assert res.json()['playground_models'] == []

def clean_expected_model(model):
    '''To match seralized api results'''
    del model['created_by']
    model.pop('last_updated', None)
    for embedded in model['embedded_list']:
        embedded.pop('_id', None)

def test_get_by_id(model, user_session_const):
    _, client = user_session_const
    res = get_api('model_get_only/%s/' % model['_id'], client=client)
    res = res.json()['playground_model']
    res['_id'] = ObjectId(res.pop('id'))
    clean_expected_model(model)
    assert model == res

def test_get_by_id_logged_out(model):
    assert_status(get_api('model_get_only/%s/' % model['_id']), 404)

def test_get_by_invalid_id():
    assert_status(get_api('model_get_only/%s/' % ObjectId()), 404)

def test_pagination(models, user_session):
    user, client = user_session

    res = get_api('model_get_only/?cnt=1&skip=2&sort=id', client=client)
    returned_models = res.json()['playground_models']
    assert len(returned_models) == 1

    expected_model = models[2]
    clean_expected_model(expected_model)

    for actual in returned_models:
        actual['_id'] = ObjectId(actual.pop('id'))

    assert returned_models[0] == expected_model

def test_delete_not_supported():
    assert_status(delete_api('model_get_only/'), 405)

def test_delete_no_id(user_session_const):
    _, client = user_session_const
    res = delete_api('model/', client=client)
    assert_status(res, 404)

def test_delete(models, user_session):
    user, client = user_session
    deleted_model_id = models[0]['_id']
    assert_status(delete_api('model/%s/' % deleted_model_id, client=client))
    assert MONGODB.playground_model.find_one({'_id': deleted_model_id, 'deleted': True}) is not None

    # Now fetch, both by id and without id, and make sure deleted model is gone
    assert_status(get_api('model_get_only/%s/' % deleted_model_id), 404)

    # No id - should fetch 2/3 models
    res = get_api('model_get_only/?sort=id&sortDir=-1', client=client)
    returned_models = res.json()['playground_models']
    assert len(returned_models) == len(models) - 1

    expected_models = list(reversed(models[1:]))
    for expected in models:
        clean_expected_model(expected)

    for actual in returned_models:
        actual['_id'] = ObjectId(actual.pop('id'))

    assert returned_models == expected_models

    _assert_audit_log({'_id': deleted_model_id}, user['_id'], 'D')

def test_auth():
    res = get_api('model/')
    assert_status(res, 404)

def test_unsupported_method(user_session_const):
    _, client = user_session_const
    res = options_api('model/', client=client)
    assert_status(res, 405)

def test_post_required_param(user_session_const):
    _, client = user_session_const
    expected = {
        'integer': 5,
    }

    res = post_api('model/', client=client, data=expected)
    assert_status(res, 400)
    assert res.json()['message'] == {'string': 'Field is required'}

def test_post_empty_required_str(user_session_const):
    _, client = user_session_const
    expected = {
        'integer': 5,
        'string' : ''
    }

    res = post_api('model/', client=client, data=expected)
    assert_status(res, 400)
    assert res.json()['message'] == {'string': 'Field is required'}

def test_post_out_of_range(user_session_const):
    _, client = user_session_const
    expected = {
        'string': 'jerth',
        'integer': 117,
    }

    res = post_api('model/', client=client, data=expected)
    assert_status(res, 400)
    assert res.json()['message'] == {'integer': 'Integer value is too large'}

def test_post_invalid_type(user_session_const):
    _, client = user_session_const
    expected = {
        'integer': 'a',
    }

    res = post_api('model/', client=client, data=expected)
    assert_status(res, 400)
    assert res.json()['message'] == {
        'string': 'Field is required',
        'integer': 'a could not be converted to int'
    }

def test_custom_validator(user_session_const):
    _, client = user_session_const
    expected = {
        'string': 'abc',
        'embedded_list': [{'embedded_string': '1', 'start_date': '4'}]
    }

    res = post_api('model/', client=client, data=expected)
    assert_status(res, 400)
    expected_msg = {'embedded_list': {'0': {'start_date': 'Invalid year: 4'}}}
    assert res.json()['message'] == expected_msg

def test_post_less_than_equal_to(user_session_const):
    _, client = user_session_const
    expected = {
        'integer': 6,
        'integer_immutable': 7,
        'string': 'abc'
    }

    res = post_api('model/', client=client, data=expected)
    assert_status(res, 400)
    assert res.json()['message'] == {'integer_immutable': 'Must be <= integer'}

def _assert_models_equal(user, expected, actual):
    actual = deepcopy(actual)
    expected = deepcopy(expected)

    expected.setdefault('_id', actual['_id'])
    expected['created_by'] = user['_id']
    assert datetime.utcnow().replace(tzinfo=pytz.utc) - actual.pop('last_updated') < timedelta(seconds=1)
    expected.pop('last_updated', None)
    expected.pop('not_editable', None)
    expected.setdefault('default_required', 7)
    expected.setdefault('default_optional', 20)

    assert expected == actual

def _assert_audit_log(obj, user_id, action):
    obj = deepcopy(obj)

    obj.pop('not_editable', None)
    obj.pop('last_updated', None)
    obj.pop('created_by', None)

    audit_log = MONGODB.audit.find_one({'doc_id': obj['_id']})
    assert audit_log.pop('_id') != audit_log.pop('doc_id')
    del obj['_id']

    assert audit_log.pop('model') == 'playground_model'
    assert audit_log.pop('user') == user_id
    assert audit_log.pop('action') == action
    assert audit_log == obj

def _verify_created_model(user, client, expected, clean_expected_func=None):
    expected = deepcopy(expected)
    res = post_api('model/', client=client, data=expected)
    assert_status(res)
    expected['_id'] = ObjectId(res.json()['id'])
    db_model = PlaygroundModel.find_by_id(expected['_id'])

    if clean_expected_func:
        clean_expected_func(expected)

    _assert_audit_log(expected, user['_id'], 'C')

    expected['integer_auto_populated'] = 8
    _assert_models_equal(user, expected, db_model)


def test_create(user_session_const, model):
    user, client = user_session_const
    expected = {
        'string': 'jweryt',
        'integer': 5,
        'integer_immutable': 4,
        'not_editable': 5,
        'boolean': False,
        'ref': model['_id']
    }

    del expected['boolean']  # We don't store false bools, waste of space
    _verify_created_model(user, client, expected)

def test_create_other_user(user_session_const):
    '''We try setting created_by to someone else, but api should ignore us'''
    user, client = user_session_const
    expected = {
        'string': 'bewth',
        'integer': 5,
        'created_by': ObjectId()
    }

    _verify_created_model(user, client, expected)

def test_post_nonexistent_ref(user_session_const):
    '''Api should check that ref is a valid id of an existing object'''
    _, client = user_session_const
    expected = {
        'string': ';nhergaj',
        'integer': 5,
        'ref': ObjectId()
    }

    res = post_api('model/', client=client, data=expected)
    assert_status(res, 400)
    assert res.json()['message'] == {'ref': "playground_model %s does not exist or you " % expected['ref'] +
                                            "don't have permissions on it"}

def test_post_ref_no_permissions(user_session_const, model):
    '''Api should check that ref points to an object we actually have permission to use '''
    _, client = user_session_const

    MONGODB.playground_model.update_one({'_id': model['_id']}, {'$set': {'created_by': ObjectId()}})
    expected = {
        'string': '#Yhsdf',
        'integer': 5,
        'ref': model['_id']
    }

    res = post_api('model/', client=client, data=expected)
    assert_status(res, 400)
    assert res.json()['message'] == {'ref': "playground_model %s does not exist or you " % expected['ref'] +
                                            "don't have permissions on it"}

def test_create_override_default(user_session_const):
    user, client = user_session_const
    expected = {
        'string': 'vznclgqwer',
        'integer': 5,
        'default_optional': 3,
        'default_required': 1,
        'boolean': True
    }

    _verify_created_model(user, client, expected)

def test_embedded_doc_list(user_session_const):
    user, client = user_session_const
    expected = {
        'string': 'nbaerw',
        'integer': 5,
        'embedded_list': [{'embedded_string': '1', 'start_date': '2015-06'}, {'embedded_string': '2'}]
    }

    _verify_created_model(user, client, expected)

def test_embedded_doc_list_type_err(user_session_const):
    _, client = user_session_const
    expected = {
        'string': 'SDGHJMJre',
        'integer': 'sdfg',
        'embedded_list': [{'embedded_string': '1'}, {'embedded_string': 2}]
    }

    res = post_api('model/', client=client, data=expected)
    assert_status(res, 400)
    expected_msg = {'embedded_list': {'1': {'embedded_string': 'StringField only accepts string values'}},
                    'integer': 'sdfg could not be converted to int'}
    assert res.json()['message'] == expected_msg

def test_embedded_doc_list_extra_field(user_session_const):
    user, client = user_session_const
    expected = {
        'string': 'NWERSgfh',
        'embedded_list': [{'embedded_string': '1', 'r': 3},
                          {'embedded_string': '2', 'start_date': '2015-6'}]
    }

    def clean_expected(e):
        del e['embedded_list'][0]['r']
        e['embedded_list'][1]['start_date'] = '2015-06'

    _verify_created_model(user, client, expected, clean_expected_func=clean_expected)

def _verify_update(user, client, model, expected_model=None):
    model = deepcopy(model)
    model['last_updated'] = calendar.timegm(model['last_updated'].timetuple())  # api expects unix timestamp
    res = patch_api('model/%s/' % model['_id'], client=client, data=model)
    assert_status(res)
    db_model = PlaygroundModel.find_by_id(model['_id'])

    expected_model = expected_model or model
    expected_model = deepcopy(expected_model)
    expected_model.pop('field_should_be_ignored', None)
    expected_model.pop('integer_immutable', None)
    if 'boolean' in expected_model and expected_model['boolean'] is None:
        del expected_model['boolean']

    _assert_models_equal(user, expected_model, db_model)

    del model['decimal']

def test_update(user_session_const, model):
    user, client = user_session_const
    model['field_should_be_ignored'] = 7
    model['integer'] = 6
    model['string'] = 'abc'
    model['integer_immutable'] = 6
    model['embedded_list'] = [
        {'embedded_string': '8'},
        {'embedded_string': '5'},
        {'embedded_string': '2'},
        {'embedded_string': '4'}
    ]

    _verify_update(user, client, model)
    del model['field_should_be_ignored']
    del model['integer_immutable']
    del model['decimal']
    _assert_audit_log(model, user['_id'], 'U')

def test_update_true_bools_saved(user_session_const, model):
    user, client = user_session_const
    model['boolean'] = True
    _verify_update(user, client, model)
    _assert_audit_log({
        '_id': model['_id'],
        'boolean': True
    }, user['_id'], 'U')

def test_update_false_bools(user_session_const, model):
    user, client = user_session_const
    MONGODB.playground_model.update_one({'_id': model['_id']}, {'$set': {'boolean': True}})
    model['boolean'] = False
    _verify_update(user, client, model)
    _assert_audit_log({
        '_id': model['_id'],
        'boolean': False
    }, user['_id'], 'U')

def test_update_someone_elses_model(user_session_const, model):
    _, client = user_session_const
    MONGODB.playground_model.update_one({'_id': model['_id']}, {'$set': {'created_by': ObjectId()}})
    model['integer'] = 6

    res = patch_api('model/%s/' % model['_id'], client=client, data=model)
    assert_status(res, 404)
    assert res.json()['message'] == ("playground_model %s does not exist or you don't " % model['_id'] +
                                     "have permissions on it")

def test_update_out_of_date(user_session_const, model):
    '''Case where user has their browser open and is editing something, but someone else edits the same thing
    from another browser. Instead of simply overwriting, let this user know that there are conflicts.'''
    _, client = user_session_const

    old_int = model['integer']
    model['integer'] = 6
    model['last_updated'] = calendar.timegm(model['last_updated'].timetuple()) - 1
    res = patch_api('model/%s/' % model['_id'], client=client, data=model)
    assert_status(res, 409)
    model['integer'] = old_int

    res = res.json()
    assert res['message'] == 'Object is out of date'
    assert res['new_obj'] == serialize(PlaygroundModel, model, None)

def test_update_null_embedded_doc(user_session_const, model):
    '''Should not be saved'''
    user, client = user_session_const
    model['embedded_list_optional'] = [None, {
        'embedded_string': 'sdfg'
    }]
    expected_model = deepcopy(model)
    expected_model['embedded_list_optional'] = model['embedded_list_optional'][1:]
    _verify_update(user, client, model, expected_model=expected_model)
    _assert_audit_log({
        '_id': model['_id'],
        'embedded_list_optional': [{
            'embedded_string': 'sdfg'
        }]
    }, user['_id'], 'U')

def test_unset(user_session_const, model):
    user, client = user_session_const
    model['integer'] = None
    model['embedded_list'] = []
    del model['last_updated']

    assert_status(patch_api('model/%s/' % model['_id'], client=client, data=model))

    db_model = PlaygroundModel.find_by_id(model['_id'])

    expected_changes = {
        'integer': None,
        'embedded_list': None,
        '_id': model['_id']
    }
    _assert_audit_log(expected_changes, user['_id'], 'U')

    del model['integer']
    del model['embedded_list']
    _assert_models_equal(user, model, db_model)
