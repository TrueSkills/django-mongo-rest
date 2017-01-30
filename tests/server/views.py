from copy import deepcopy
from django.http.response import HttpResponse
from django_mongo_rest import ApiView, PageView, PERMISSION
from django_mongo_rest.validation import Param, email_validator
from django_mongo_rest.model_view import ModelView
from .models import PlaygroundModel

class NoneApi(ApiView):
    permissions = []

    @staticmethod
    def main(_):
        pass

class Get(ApiView):
    allowed_methods = 'GET'
    permissions = []

    @staticmethod
    def main(_):
        pass

class Post(ApiView):
    allowed_methods = 'POST'
    permissions = []

    @staticmethod
    def main(_):
        pass

class BothMethods(ApiView):
    allowed_methods = ('POST', 'GET')
    permissions = []

    @staticmethod
    def main(_):
        pass

class Params(ApiView):
    permissions = []
    params = (
        Param('email', type_cast=email_validator),
        Param('required_int', required=True, type_cast=int, max=5),
    )

    @staticmethod
    def main(_, email, required_int):
        return {'email': email, 'required_int': required_int}

class LoginRequired(ApiView):
    permissions = PERMISSION.LOGIN

    @staticmethod
    def main(_):
        pass

class Superuser(ApiView):
    permissions = PERMISSION.SUPERUSER

    @staticmethod
    def main(_):
        pass

class LoginRequiredPage(PageView):
    permissions = PERMISSION.LOGIN

    @staticmethod
    def main(_):
        return HttpResponse()

class SuperuserPage(PageView):
    permissions = PERMISSION.SUPERUSER

    @staticmethod
    def main(_):
        return HttpResponse()

class PlaygroundModelView(ModelView):
    model = PlaygroundModel
    allowed_methods = ['GET', 'POST', 'PATCH', 'DELETE']
    permissions = [PERMISSION.LOGIN]
    editable_fields = {'string': 1, 'integer': 1, 'ref': 1, 'default_required': 1, 'default_optional': 1,
                       'embedded_list': ('embedded_string', 'start_date'), 'decimal': 1, 'boolean': 1}
    initial_fields = deepcopy(editable_fields)
    initial_fields['integer_immutable'] = 1

    @staticmethod
    def auto_populate_new_model(request, model):
        model['created_by'] = request.user.id
        model['integer_auto_populated'] = 8

class PlaygroundModelViewGetOnly(PlaygroundModelView):
    allowed_methods = ['GET']
    permissions = []
