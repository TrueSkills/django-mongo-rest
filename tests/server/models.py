from django_mongo_rest.models import BaseModel, ModelPermissionException
from django_mongo_rest.validation import date_str_validator
from django_mongoengine.mongo_auth.models import AbstractUser
from mongoengine import (StringField, IntField, ReferenceField, EmbeddedDocument, EmbeddedDocumentListField,
                         DecimalField, BooleanField)

class User(AbstractUser, BaseModel):
    meta = {
        'indexes': [{
            'fields': ['email'],
            'unique': True,
            'sparse': True,
        }]
    }

    serialize_fields = (
        ('_id', 'id'),
        'email',
        'email_verified',
        'is_superuser',
        'first_name',
        'last_name',
    )

    email_verified = BooleanField(default=False)

    @classmethod
    def allowed_find_query(cls, request):
        if not request.user.is_authenticated():
            raise ModelPermissionException
        return {'_id': request.user.id}

    allowed_update_query = allowed_find_query

    def get_username(self):
        return self.username

class PlaygroundEmbeddedDocAllOptional(EmbeddedDocument):
    serialize_fields = ('embedded_string',)
    embedded_string = StringField()

class PlaygroundEmbeddedDoc(EmbeddedDocument):
    serialize_fields = ('embedded_string',)
#     _id = ObjectIdField(default=ObjectId) Doesn't play well with audit log. Maybe support this later
    embedded_string = StringField(required=True)
    start_date = StringField(validator=date_str_validator)

class PlaygroundModel(BaseModel):
    serialize_fields = (('_id', 'id'), 'string', 'integer', 'decimal', 'embedded_list')
    string = StringField(max_length=10, min_length=3, required=True)
    integer = IntField(min_value=4, max_value=100)
    integer_immutable = IntField(min_value=4, max_value=10)
    integer_auto_populated = IntField(min_value=4, max_value=10)
    not_editable = IntField(min_value=4, max_value=10)
    ref = ReferenceField('PlaygroundModel')
    default_required = IntField(required=True, default=7)
    default_optional = IntField(default=20)
    decimal = DecimalField(precision=20)
    boolean = BooleanField()
    embedded_list = EmbeddedDocumentListField(PlaygroundEmbeddedDoc)
    embedded_list_optional = EmbeddedDocumentListField(PlaygroundEmbeddedDocAllOptional)
    created_by = ReferenceField(User)

    @classmethod
    def allowed_find_query(cls, request):
        if not request.user.is_authenticated():
            raise ModelPermissionException
        return {'created_by': request.user.id}

    allowed_update_query = allowed_find_query
