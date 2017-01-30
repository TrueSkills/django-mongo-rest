from django.conf.urls import url, include
from django_mongo_rest.shortcuts import url_optional_id
import views

urlpatterns = [
    url(r'^api/', include([
        url(r'^$', views.NoneApi().endpoint),
        url(r'^get/$', views.Get().endpoint),
        url(r'^post/$', views.Post().endpoint),
        url(r'^both_methods/$', views.BothMethods().endpoint),
        url(r'^params/$', views.Params().endpoint),
        url(r'^login_required/$', views.LoginRequired().endpoint),
        url(r'^superuser/$', views.Superuser().endpoint),
        url(r'^model_get_only/%s$' % url_optional_id('obj_id'),
            views.PlaygroundModelViewGetOnly().endpoint),
        url(r'^model/%s$' % url_optional_id('obj_id'),
            views.PlaygroundModelView().endpoint)
    ])),
    url(r'^login_required/$', views.LoginRequiredPage().endpoint),
    url(r'^superuser/$', views.SuperuserPage().endpoint),
]
