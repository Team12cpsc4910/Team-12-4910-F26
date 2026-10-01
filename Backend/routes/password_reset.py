import os
import secrets
import smtplib
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from threading import BoundedSemaphore
from urllib.parse import urlsplit
from flask import Blueprint, request, render_template, current_app, session
from werkzeug.security import generate_password_hash
from database import get_db_connection
from security import check_csrf, digest

reset_bp = Blueprint('reset', __name__)
_pool = ThreadPoolExecutor(max_workers=2)
_slots = BoundedSemaphore(20)
_GENERIC = 'If a driver or sponsor account matches that email, a reset link will be sent. Please check your inbox and spam folder.'


def send_reset_email(email, token):
    base = os.environ['PUBLIC_BACKEND_URL'].rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError('PUBLIC_BACKEND_URL must be an HTTPS URL.')
    message = EmailMessage()
    message['From'] = os.environ['MAIL_FROM']
    message['To'] = email
    message['Subject'] = 'Reset your Good Driver Incentive password'
    # fragment keeps secret 
    message.set_content(f'Use this link within 30 minutes to reset your password:\n{base}/reset-password#token={token}\n\nYour points and sponsor relationships will stay unchanged. If you did not request this, ignore this email.')
    with smtplib.SMTP(os.environ['SMTP_HOST'], int(os.getenv('SMTP_PORT', '587')), timeout=10) as mail:
        mail.starttls(context=ssl.create_default_context())
        mail.login(os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'])
        mail.send_message(message)


def issue_reset(email):
    conn = get_db_connection()
    token = secrets.token_urlsafe(32)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, email FROM UserAccount WHERE email = %s AND LOWER(user_type) IN ('driver', 'sponsor') FOR UPDATE", (email,))
            user = cur.fetchone()
            if not user:
                return
            cur.execute('INSERT INTO PasswordResetToken (token_hash, user_id, expires_at) VALUES (%s, %s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 30 MINUTE))', (digest(token), user['user_id']))
        conn.commit()
        try:
            send_reset_email(user['email'], token)
        except Exception:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM PasswordResetToken WHERE token_hash = %s', (digest(token),))
            conn.commit()
            raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _deliver(app, email):
    try:
        with app.app_context():
            issue_reset(email)
    except Exception:
        app.logger.error('Password reset delivery failed; check database and mail configuration.')
    finally:
        _slots.release()


def request_allowed(email):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            key, window = digest(email), int(time.time() // 900)
            cur.execute('INSERT INTO ResetRateLimit (identity_hash, window_id, attempts) VALUES (%s, %s, 1) ON DUPLICATE KEY UPDATE attempts = attempts + 1', (key, window))
            cur.execute('SELECT attempts FROM ResetRateLimit WHERE identity_hash = %s AND window_id = %s', (key, window))
            allowed = cur.fetchone()['attempts'] <= 3
        conn.commit()
        return allowed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@reset_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    message = None
    if request.method == 'POST':
        check_csrf()
        email = request.form.get('email', '').strip().lower()
        if len(email) <= 100 and '@' in email and request_allowed(email) and _slots.acquire(blocking=False):
            try:
                _pool.submit(_deliver, current_app._get_current_object(), email)
            except Exception:
                _slots.release()
                current_app.logger.error('Password reset delivery queue unavailable.')
        message = _GENERIC
    return render_template('forgot_password.html', message=message)


def apply_reset(token, password):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Read the owner then lock the account
            cur.execute('SELECT user_id FROM PasswordResetToken WHERE token_hash = %s', (digest(token),))
            owner = cur.fetchone()
            if not owner:
                return False
            cur.execute("SELECT user_id FROM UserAccount WHERE user_id = %s AND LOWER(user_type) IN ('driver', 'sponsor') FOR UPDATE", (owner['user_id'],))
            if not cur.fetchone():
                return False
            cur.execute('SELECT user_id FROM PasswordResetToken WHERE token_hash = %s AND used_at IS NULL AND expires_at > UTC_TIMESTAMP() FOR UPDATE', (digest(token),))
            if not cur.fetchone():
                return False
            cur.execute('UPDATE UserAccount SET password_hash = %s, failed_logins = 0, account_locked = FALSE WHERE user_id = %s', (generate_password_hash(password), owner['user_id']))
            cur.execute("INSERT INTO PasswordChangeLog (user_id, changed_by_user_id, change_type) VALUES (%s, %s, 'self_reset')", (owner['user_id'], owner['user_id']))
            cur.execute('UPDATE PasswordResetToken SET used_at = UTC_TIMESTAMP() WHERE user_id = %s AND used_at IS NULL', (owner['user_id'],))
            cur.execute('DELETE FROM AuthSession WHERE user_id = %s', (owner['user_id'],))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@reset_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    error = None
    if request.method == 'POST':
        check_csrf()
        token = request.form.get('token', '')
        password = request.form.get('password', '')
        if not 15 <= len(password) <= 128:
            error = 'Use a password between 15 and 128 characters.'
        elif password != request.form.get('confirm_password'):
            error = 'The passwords do not match.'
        elif not 40 <= len(token) <= 128 or not apply_reset(token, password):
            error = 'This reset link is invalid or expired. Request a new link.'
        else:
            session.clear()
            return render_template('reset_password.html', success=True)
    return render_template('reset_password.html', error=error), (400 if error else 200)
