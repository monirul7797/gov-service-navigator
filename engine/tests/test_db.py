import pytest

from gsn.db import connect_kwargs


def test_connect_kwargs_defaults():
    assert connect_kwargs({"GSN_DB_USER": "u", "GSN_DB_PASSWORD": "p"}) == {
        "host": "postgres",
        "port": 5432,
        "dbname": "gsn",
        "user": "u",
        "password": "p",
    }


def test_connect_kwargs_overrides():
    env = {
        "GSN_DB_HOST": "db.internal",
        "GSN_DB_PORT": "6543",
        "GSN_DB_NAME": "other",
        "GSN_DB_USER": "u",
        "GSN_DB_PASSWORD": "p",
    }
    assert connect_kwargs(env) == {
        "host": "db.internal",
        "port": 6543,
        "dbname": "other",
        "user": "u",
        "password": "p",
    }


def test_port_is_an_int():
    kw = connect_kwargs({"GSN_DB_USER": "u", "GSN_DB_PASSWORD": "p", "GSN_DB_PORT": "5433"})
    assert kw["port"] == 5433 and isinstance(kw["port"], int)


def test_password_with_url_special_chars_is_kept_verbatim():
    assert connect_kwargs({"GSN_DB_USER": "u", "GSN_DB_PASSWORD": "p@ss/w:rd#%?"})["password"] == "p@ss/w:rd#%?"


def test_missing_user_raises():
    with pytest.raises(ValueError, match="GSN_DB_USER"):
        connect_kwargs({"GSN_DB_PASSWORD": "p"})


def test_missing_password_raises():
    with pytest.raises(ValueError, match="GSN_DB_PASSWORD"):
        connect_kwargs({"GSN_DB_USER": "u"})


def test_empty_values_count_as_missing():
    with pytest.raises(ValueError, match="GSN_DB_USER"):
        connect_kwargs({"GSN_DB_USER": "", "GSN_DB_PASSWORD": "p"})
    with pytest.raises(ValueError, match="GSN_DB_PASSWORD"):
        connect_kwargs({"GSN_DB_USER": "u", "GSN_DB_PASSWORD": ""})


def test_error_message_does_not_leak_other_secret():
    with pytest.raises(ValueError) as exc:
        connect_kwargs({"GSN_DB_PASSWORD": "super-secret"})
    assert "super-secret" not in str(exc.value)
