# app/common/db.py
import os
import time
from typing import Any, Iterable, Optional

from psycopg import connect, OperationalError
from psycopg.rows import dict_row

class DBUnavailable(Exception):
    pass

def _get_db_conf():
    host = os.getenv("DB_HOST")
    port = int(os.getenv("DB_PORT", "5432"))
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASS")
    name = os.getenv("DB_NAME")
    missing = [k for k, v in {"DB_HOST": host, "DB_USER": user, "DB_PASS": password, "DB_NAME": name}.items() if not v]
    if missing:
        raise DBUnavailable(f"missing env: {', '.join(missing)}")
    return host, port, user, password, name

def get_conn(retries: int = 5, delay: float = 2.0):
    last: Optional[Exception] = None
    for _ in range(retries):
        try:
            host, port, user, password, name = _get_db_conf()
            return connect(host=host, port=port, user=user, password=password, dbname=name, autocommit=False)
        except Exception as e:
            last = e
            time.sleep(delay)
    raise DBUnavailable(f"database not reachable: {last}")

def init_tables():
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    user_email TEXT NOT NULL,
                    item TEXT NOT NULL,
                    qty INT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            conn.commit()
    except DBUnavailable:
        pass

def execute(sql: str, params: Optional[Iterable[Any]] = None) -> dict:
    """
    Run any SQL. If the statement returns rows (e.g., SELECT or INSERT ... RETURNING),
    return them. Otherwise return rowcount.
    """
    try:
        with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params or [])
            if cur.description is not None:
                rows = cur.fetchall()
                conn.commit()
                return {"rows": rows, "rowcount": cur.rowcount}
            else:
                conn.commit()
                return {"rows": None, "rowcount": cur.rowcount}
    except OperationalError as e:
        raise DBUnavailable(str(e))

def select(sql: str, params: Optional[Iterable[Any]] = None, one: bool = False):
    res = execute(sql, params)
    if res["rows"] is None:
        return None if one else []
    return res["rows"][0] if one else res["rows"]

