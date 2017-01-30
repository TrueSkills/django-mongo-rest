from utils import assert_status, get_api

REQUIRED_INT_ERROR = {'required_int': 'Is required'}

def test_required():
    res = get_api('params/')
    assert_status(res, 400)
    assert REQUIRED_INT_ERROR == res.json()['message']

def test_required2():
    res = get_api('params/?email=abc@abc.com')
    assert_status(res, 400)
    assert REQUIRED_INT_ERROR == res.json()['message']

def test_2_errors():
    res = get_api('params/?email=abc')
    assert_status(res, 400)
    error = {'email': 'Must be an email'}
    error.update(REQUIRED_INT_ERROR)
    assert error == res.json()['message']

def test_max():
    res = get_api('params/?email=abc@abc.com&required_int=7')
    assert_status(res, 400)
    assert res.json()['message'] == {'required_int': 'Must be <= 5'}

def test_int():
    res = get_api('params/?email=abc@abc.com&required_int=s')
    assert_status(res, 400)
    assert res.json()['message'] == {'required_int': 'Must be of type int'}

def test_pass():
    res = get_api('params/?email=abc@abc.com&required_int=5')
    assert_status(res)
    assert res.json() == {'email': 'abc@abc.com', 'required_int': 5}

def test_missing_optional_param():
    res = get_api('params/?required_int=5')
    assert_status(res)
    assert res.json() == {'email': None, 'required_int': 5}

def test_empty_optional_param():
    res = get_api('params/?email=&required_int=5')
    assert_status(res)
    assert res.json() == {'email': '', 'required_int': 5}
