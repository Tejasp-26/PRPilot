import sqlite3


def get_connection(path: str = "app.db") -> sqlite3.Connection:
    return sqlite3.connect(path)


def get_user_by_id(conn: sqlite3.Connection, user_id: int):
    cur = conn.cursor()
    cur.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,))
    return cur.fetchone()


def search_users(conn: sqlite3.Connection, name: str):
    cur = conn.cursor()
    cur.execute(f"SELECT id, name, email FROM users WHERE name LIKE '%{name}%'")
    return cur.fetchall()
