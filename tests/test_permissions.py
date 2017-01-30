from django.conf import settings
from utils import assert_status, get_api, post_api, get_page

def assert_redirects_to_login(url, client=None):
    res = get_page(url, client=client)
    assert_status(res, expected_status=302)
    assert res.url.endswith(settings.LOGIN_URL + '?next=/' + url)

def test_validation():
    assert_status(get_api(''))
    assert_status(post_api(''))

def test_validation_get():
    assert_status(get_api('get/'))
    assert_status(post_api('get/'), 405)

def test_validation_post():
    assert_status(get_api('post/'), 405)
    assert_status(post_api('post/'))

def test_validation_both_methods():
    assert_status(get_api('both_methods/'))
    assert_status(post_api('both_methods/'))

def _verify_allowed(url, client):
    res = get_api(url, client=client)
    assert_status(res)

def test_login_required(user_session_const):
    url = 'login_required/'

    res = get_api(url)
    assert_status(res, 404)
    _verify_allowed(url, user_session_const[1])

def test_superuser(user_session_const, superuser_session_const):
    url = 'superuser/'

    res = get_api(url)
    assert_status(res, 404)

    _, client = user_session_const
    res = get_api(url, client=client)
    assert_status(res, 404)

    _verify_allowed(url, superuser_session_const[1])

def test_login_required_page(user_session_const):
    url = 'login_required/'

    assert_redirects_to_login(url)
    _verify_allowed(url, user_session_const[1])

def test_superuser_page(user_session_const, superuser_session_const):
    url = 'superuser/'

    assert_redirects_to_login(url)

    _, client = user_session_const
    assert_redirects_to_login(url, client)

    _verify_allowed(url, superuser_session_const[1])
