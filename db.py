import os
import json
import time
import threading
from contextlib import contextmanager
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
from psycopg2 import OperationalError, InterfaceError

LOCAL_URL = 'postgresql://poem:poem@127.0.0.1:5432/poem_db'
_cached_url = None
_lock = threading.Lock()
_pool = []
_POOL_SIZE = 4


def set_pool_size(n: int):
    """Expand connection pool for parallel batch eval (call before heavy work)."""
    global _POOL_SIZE
    with _lock:
        _POOL_SIZE = max(1, min(32, int(n)))


def database_url():
    global _cached_url
    if _cached_url:
        return _cached_url
    load_dotenv(override=True)
    val = os.getenv('DATABASE_URL') or LOCAL_URL
    if 'neon.tech' in (val or ''):
        val = LOCAL_URL
    _cached_url = val
    return _cached_url


def set_database_url(url):
    global _cached_url
    with _lock:
        if _cached_url == url:
            return
        _cached_url = url
        while _pool:
            conn = _pool.pop()
            try:
                conn.close()
            except Exception:
                pass


def _is_conn_error(exc):
    if isinstance(exc, (OperationalError, InterfaceError)):
        return True
    msg = str(exc).lower()
    return any(x in msg for x in (
        'connection abort', 'connection reset', 'server closed',
        'ssl syscall', 'timeout expired', 'could not receive',
        'could not connect', 'broken pipe', 'eof detected',
        'connection refused'
    ))


def _wsl_url():
    try:
        import subprocess
        env = os.environ.copy()
        env['PATH'] = r'C:\Windows\System32;C:\Windows'
        ip = subprocess.check_output(
            ['wsl', '-d', 'Ubuntu', '--', 'hostname', '-I'],
            text=True, timeout=3, env=env
        ).split()[0]
        if ip.count('.') == 3:
            return f'postgresql://poem:poem@{ip}:5432/poem_db'
    except Exception:
        pass
    return None


def _candidate_urls():
    urls = [LOCAL_URL]
    cur = database_url()
    if cur and 'neon.tech' not in cur and cur != LOCAL_URL:
        urls.append(cur)
    return list(dict.fromkeys(urls))


def _connect(url):
    conn = psycopg2.connect(
        url,
        connect_timeout=3,
        keepalives=1,
        keepalives_idle=10,
        keepalives_interval=5,
        keepalives_count=3,
    )
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute('SELECT 1')
    return conn


def _open_fresh():
    last = None
    for url in _candidate_urls():
        try:
            conn = _connect(url)
            set_database_url(url)
            return conn
        except Exception as e:
            last = e
    # last resort: WSL eth0 IP
    wsl = _wsl_url()
    if wsl:
        try:
            conn = _connect(wsl)
            set_database_url(wsl)
            return conn
        except Exception as e:
            last = e
    if last is None:
        raise RuntimeError('No DATABASE_URL configured')
    raise last


def get_db():
    # Pop under lock, validate OUTSIDE lock — never block other threads on SELECT 1
    while True:
        conn = None
        with _lock:
            if _pool:
                conn = _pool.pop()
        if conn is None:
            break
        try:
            if conn.closed:
                continue
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    return _open_fresh()


def put_db(conn, pooled=True):
    if conn is None:
        return
    try:
        if conn.closed:
            return
        conn.rollback()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return
    if pooled:
        with _lock:
            if len(_pool) < _POOL_SIZE:
                _pool.append(conn)
                return
    try:
        conn.close()
    except Exception:
        pass


@contextmanager
def db_cursor(commit=False):
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
            if commit:
                conn.commit()
            else:
                conn.rollback()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass
    finally:
        put_db(conn)


def _run_with_retry(fn, attempts=5):
    last = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if attempt < attempts - 1 and _is_conn_error(e):
                with _lock:
                    while _pool:
                        try:
                            _pool.pop().close()
                        except Exception:
                            pass
                time.sleep(min(1.6, 0.05 * (2 ** attempt)))
                continue
            raise
    raise last


def init_db():
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    with open(schema_path, 'r', encoding='utf-8') as f:
        sql = f.read()

    def work():
        with db_cursor(commit=True) as cur:
            cur.execute(sql)
    _run_with_retry(work)


def fetchone(query, params=None):
    def work():
        with db_cursor() as cur:
            cur.execute(query, params or ())
            return cur.fetchone()
    return _run_with_retry(work)


def fetchall(query, params=None):
    def work():
        with db_cursor() as cur:
            cur.execute(query, params or ())
            return cur.fetchall()
    return _run_with_retry(work)


def execute(query, params=None, returning=False):
    def work():
        with db_cursor(commit=True) as cur:
            cur.execute(query, params or ())
            if returning:
                return cur.fetchone()
            return None
    return _run_with_retry(work)


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


def loads(obj, default=None):
    if obj is None:
        return default if default is not None else {}
    if isinstance(obj, (dict, list)):
        return obj
    return json.loads(obj)


def ping():
    row = fetchone('SELECT 1 AS ok')
    return bool(row and row.get('ok') == 1)
