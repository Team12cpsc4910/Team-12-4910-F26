import secrets
from functools import wraps

from flask import session, request, abort, redirect, g

from database import get_db_connection


def csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)

    return session['_csrf_token']


def check_csrf():
    submitted_token = request.form.get('csrf_token', '')
    stored_token = session.get('_csrf_token', '')

    if not submitted_token or not stored_token:
        abort(400)

    if not secrets.compare_digest(submitted_token, stored_token):
        abort(400)


def load_user():
    g.user = None

    user_id = session.get('user_id')

    if not user_id:
        return

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, username, email, user_type
                FROM UserAccount
                WHERE user_id = %s
                """,
                (user_id,)
            )

            g.user = cur.fetchone()

    finally:
        conn.close()


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            return redirect('/')

        return view(*args, **kwargs)

    return wrapped_view
