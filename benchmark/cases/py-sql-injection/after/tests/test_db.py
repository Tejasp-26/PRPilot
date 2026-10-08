import sqlite3

from app.db import get_user_by_id, search_users


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT, email TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'ada', 'ada@example.com')")
    return conn


def test_get_user_by_id():
    assert get_user_by_id(make_db(), 1)[1] == "ada"


def test_search_users():
    assert len(search_users(make_db(), "ad")) == 1
