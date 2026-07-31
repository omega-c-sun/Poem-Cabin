import os
import re
import subprocess
import time
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV = ROOT / '.env'
CLEAN_LINUX_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
LOCAL = 'postgresql://poem:poem@127.0.0.1:5432/poem_db'


def _win_env():
    env = os.environ.copy()
    env['PATH'] = r'C:\Windows\System32;C:\Windows'
    env.pop('PYTHONPATH', None)
    env.pop('WSLENV', None)
    return env


def wsl(cmd, timeout=20):
    return subprocess.check_output(
        [
            'wsl.exe', '-d', 'Ubuntu', '--cd', '/root', '--user', 'root', '--exec',
            '/usr/bin/env', f'PATH={CLEAN_LINUX_PATH}', '/bin/bash', '-lc', cmd
        ],
        text=True,
        encoding='utf-8',
        errors='ignore',
        timeout=timeout,
        env=_win_env(),
    )


def wsl_ip():
    out = wsl('hostname -I', timeout=12)
    for part in out.split():
        if part.count('.') == 3:
            return part
    raise RuntimeError('WSL IP not found: ' + out)


def ensure_pg():
    try:
        out = wsl('pg_isready -h 127.0.0.1 -p 5432 || true', timeout=10)
        if 'accepting connections' in out:
            return
    except Exception:
        pass
    try:
        wsl('/usr/sbin/service postgresql start || pg_ctlcluster 16 main start || true', timeout=25)
    except Exception:
        pass
    for _ in range(12):
        time.sleep(0.35)
        try:
            out = wsl('pg_isready -h 127.0.0.1 -p 5432 || true', timeout=8)
            if 'accepting connections' in out:
                return
        except Exception:
            continue


def write_url(url):
    text = ENV.read_text(encoding='utf-8') if ENV.exists() else ''
    if re.search(r'^DATABASE_URL=.*$', text, re.M):
        text = re.sub(r'^DATABASE_URL=.*$', 'DATABASE_URL=' + url, text, count=1, flags=re.M)
    else:
        text = 'DATABASE_URL=' + url + '\n' + text
    # drop neon leftovers if any remain as other keys — leave alone
    ENV.write_text(text, encoding='utf-8')
    try:
        import db
        db.set_database_url(url)
    except Exception:
        pass
    return url


def probe(url):
    import psycopg2
    conn = psycopg2.connect(url, connect_timeout=3)
    conn.close()


def main(quiet=False):
    ensure_pg()
    candidates = [LOCAL]
    try:
        ip = wsl_ip()
        candidates.append(f'postgresql://poem:poem@{ip}:5432/poem_db')
    except Exception:
        pass
    last = None
    for url in dict.fromkeys(candidates):
        try:
            probe(url)
            written = write_url(url)
            if not quiet:
                print(written)
            return written
        except Exception as e:
            last = e
    raise last or RuntimeError('Postgres unreachable')


if __name__ == '__main__':
    main()
