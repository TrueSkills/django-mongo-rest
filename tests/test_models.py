from django_mongo_rest.models import FindParams
from server.models import PlaygroundModel
from utils import DummyObject

def test_permission_queries(user):
    '''Model should add permission checks to the query'''
    PlaygroundModel.insert_one({'created_by': user['_id']})
    PlaygroundModel.insert_one({'created_by': '123'})
    request = DummyObject()
    request.user = DummyObject()
    request.user.is_authenticated = lambda: True
    request.user.id = user['_id']
    assert PlaygroundModel.find_one(created_by='123', params=FindParams(request=request)) is None
