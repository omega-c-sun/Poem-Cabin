import hashlib
from flask import session, redirect, url_for
from functools import wraps
import db


def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def register_user(username, password):
    username = username.strip()
    if not username or not password:
        return None, 'Username and password required'
    existing = db.fetchone('SELECT id FROM users WHERE username = %s', (username,))
    if existing:
        return None, 'Username already taken'
    row = db.execute(
        '''INSERT INTO users (username, password_hash)
           VALUES (%s, %s) RETURNING id, username''',
        (username, hash_password(password)),
        returning=True)
    db.execute(
        '''INSERT INTO user_preferences (user_id) VALUES (%s)''',
        (str(row['id']),))
    return row, None


def login_user(username, password):
    row = db.fetchone(
        'SELECT id, username, password_hash FROM users WHERE username = %s',
        (username.strip(),))
    if not row:
        return None, 'Invalid credentials'
    if row['password_hash'] != hash_password(password):
        return None, 'Invalid credentials'
    return row, None


def current_user_id():
    try:
        return session.get('user_id')
    except Exception:
        return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user_id():
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped
