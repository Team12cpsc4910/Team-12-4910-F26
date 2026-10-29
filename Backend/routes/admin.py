import re

import pymysql
from flask import Blueprint, render_template, request, redirect, session, g, abort
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db_connection
from routes.applications import get_sponsors
from security import login_required, check_csrf

admin_bp = Blueprint('admin', __name__)

USERNAME_PATTERN = r'[A-Za-z0-9_.-]{3,50}'
EMAIL_PATTERN = r'[^\s@]+@[^\s@]+\.[^\s@]+'


class AdminError(ValueError):
    pass

def require_admin():
    if g.user['user_type'].lower() != 'admin':
        abort(403)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT admin_user_id FROM AdminUser WHERE user_id = %s', (g.user['user_id'],))
            if not cur.fetchone():
                abort(403)
    finally:
        conn.close()


def get_sponsor_users():
    sponsor_users = []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT su.sponsor_user_id, su.first_name, su.last_name,
                       u.username, u.email, u.account_locked, s.sponsor_name
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                JOIN Sponsor s ON su.sponsor_id = s.sponsor_id
                ORDER BY s.sponsor_name, su.last_name, su.first_name
            """)
            sponsor_users = cur.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if conn and conn.open:
            conn.close()
    return sponsor_users


def get_sponsor_user(sponsor_user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT su.sponsor_user_id, su.first_name, su.last_name,
                       u.username, u.email, u.account_locked, u.failed_logins,
                       s.sponsor_id, s.sponsor_name
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                JOIN Sponsor s ON su.sponsor_id = s.sponsor_id
                WHERE su.sponsor_user_id = %s
            """, (sponsor_user_id,))
            return cur.fetchone()
    finally:
        conn.close()

def update_sponsor_user(sponsor_user_id, values):
    if not all(1 <= len(values[k]) <= 50 for k in ('first_name', 'last_name')):
        raise AdminError('Enter first and last names of up to 50 characters each.')
    if not re.fullmatch(USERNAME_PATTERN, values['username']):
        raise AdminError('Username must be 3–50 letters, numbers, periods, underscores, or hyphens.')
    if len(values['email']) > 100 or not re.fullmatch(EMAIL_PATTERN, values['email']):
        raise AdminError('Enter a valid email address.')
    if not values['sponsor_id'].isdigit():
        raise AdminError('Choose a sponsor company.')

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT user_id FROM SponsorUser WHERE sponsor_user_id = %s FOR UPDATE',
                        (sponsor_user_id,))
            sponsor_user = cur.fetchone()
            if not sponsor_user:
                return False

            cur.execute('SELECT sponsor_id FROM Sponsor WHERE sponsor_id = %s', (values['sponsor_id'],))
            if not cur.fetchone():
                raise AdminError('Choose a sponsor company.')

            cur.execute("""
                SELECT user_id FROM UserAccount
                WHERE (username = %s OR email = %s) AND user_id != %s
            """, (values['username'], values['email'], sponsor_user['user_id']))
            if cur.fetchone():
                raise AdminError('That username or email is already used by another account.')

            cur.execute("""
                UPDATE UserAccount
                SET username = %s, email = %s, account_locked = %s
                WHERE user_id = %s
            """, (values['username'], values['email'], values['account_locked'], sponsor_user['user_id']))
            if not values['account_locked']:
                cur.execute('UPDATE UserAccount SET failed_logins = 0 WHERE user_id = %s',
                            (sponsor_user['user_id'],))

            cur.execute("""
                UPDATE SponsorUser
                SET first_name = %s, last_name = %s, sponsor_id = %s
                WHERE sponsor_user_id = %s
            """, (values['first_name'], values['last_name'], values['sponsor_id'], sponsor_user_id))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def get_admin_users():
    admin_users = []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.admin_user_id, a.first_name, a.last_name,
                       u.username, u.email, u.account_locked
                FROM AdminUser a
                JOIN UserAccount u ON a.user_id = u.user_id
                ORDER BY a.last_name, a.first_name, u.username
            """)
            admin_users = cur.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if conn and conn.open:
            conn.close()
    return admin_users


def get_admin_user(admin_user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.admin_user_id, a.first_name, a.last_name,
                       u.username, u.email, u.account_locked, u.failed_logins
                FROM AdminUser a
                JOIN UserAccount u ON a.user_id = u.user_id
                WHERE a.admin_user_id = %s
            """, (admin_user_id,))
            return cur.fetchone()
    finally:
        conn.close()

def update_admin_user(admin_user_id, values):
    if not all(1 <= len(values[k]) <= 50 for k in ('first_name', 'last_name')):
        raise AdminError('Enter first and last names of up to 50 characters each.')
    if not re.fullmatch(USERNAME_PATTERN, values['username']):
        raise AdminError('Username must be 3–50 letters, numbers, periods, underscores, or hyphens.')
    if len(values['email']) > 100 or not re.fullmatch(EMAIL_PATTERN, values['email']):
        raise AdminError('Enter a valid email address.')

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT user_id FROM AdminUser WHERE admin_user_id = %s FOR UPDATE',
                        (admin_user_id,))
            admin_user = cur.fetchone()
            if not admin_user:
                return False

            cur.execute("""
                SELECT user_id FROM UserAccount
                WHERE (username = %s OR email = %s) AND user_id != %s
            """, (values['username'], values['email'], admin_user['user_id']))
            if cur.fetchone():
                raise AdminError('That username or email is already used by another account.')

            cur.execute("""
                UPDATE UserAccount
                SET username = %s, email = %s, account_locked = %s
                WHERE user_id = %s
            """, (values['username'], values['email'], values['account_locked'], admin_user['user_id']))
            if not values['account_locked']:
                cur.execute('UPDATE UserAccount SET failed_logins = 0 WHERE user_id = %s',
                            (admin_user['user_id'],))

            cur.execute("""
                UPDATE AdminUser
                SET first_name = %s, last_name = %s
                WHERE admin_user_id = %s
            """, (values['first_name'], values['last_name'], admin_user_id))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def change_own_password(user_id, current_password, new_password, confirm_password):
    if not 8 <= len(new_password) <= 128:
        raise AdminError('Password must be 8–128 characters.')
    if not any(c.isupper() for c in new_password):
        raise AdminError('Password must contain at least 1 uppercase letter.')
    if not any(c.isdigit() for c in new_password):
        raise AdminError('Password must contain at least 1 number.')
    if new_password != confirm_password:
        raise AdminError('New passwords do not match.')

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT password_hash FROM UserAccount WHERE user_id = %s FOR UPDATE', (user_id,))
            account = cur.fetchone()
            if not account or not check_password_hash(account['password_hash'], current_password):
                raise AdminError('Your current password is incorrect.')
            if check_password_hash(account['password_hash'], new_password):
                raise AdminError('Your new password must be different from your current password.')

            cur.execute('UPDATE UserAccount SET password_hash = %s WHERE user_id = %s',
                        (generate_password_hash(new_password), user_id))

            try:
                cur.execute("""
                    INSERT INTO PasswordChangeLog (user_id, changed_by_user_id, change_type)
                    VALUES (%s, %s, 'Self Change')
                """, (user_id, user_id))
            except pymysql.MySQLError as e:
                print(f"Password change log error: {e}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@admin_bp.route('/admin/sponsor-users')
@login_required
def admin_sponsor_users():
    require_admin()
    return render_template('admin_sponsor_users.html', sponsor_users=get_sponsor_users())


@admin_bp.route('/admin/sponsor-users/<int:sponsor_user_id>')
@login_required
def admin_sponsor_user(sponsor_user_id):
    require_admin()
    sponsor_user = get_sponsor_user(sponsor_user_id)
    if not sponsor_user:
        abort(404)
    return render_template('admin_sponsor_user.html', sponsor_user=sponsor_user,
                           updated=request.args.get('updated') == '1')

@admin_bp.route('/admin/sponsor-users/<int:sponsor_user_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_sponsor_user(sponsor_user_id):
    require_admin()
    sponsor_user = get_sponsor_user(sponsor_user_id)
    if not sponsor_user:
        abort(404)

    error = None
    fields = ('first_name', 'last_name', 'username', 'email', 'sponsor_id')
    values: dict = {k: str(sponsor_user[k]) for k in fields}
    values['account_locked'] = bool(sponsor_user['account_locked'])

    if request.method == 'POST':
        check_csrf()
        values = {k: request.form.get(k, '').strip() for k in fields}
        values['email'] = values['email'].lower()
        values['account_locked'] = request.form.get('account_locked') == 'on'
        try:
            if not update_sponsor_user(sponsor_user_id, values):
                abort(404)
            return redirect(f'/admin/sponsor-users/{sponsor_user_id}?updated=1')
        except AdminError as exc:
            error = str(exc)

    return render_template('admin_sponsor_user_form.html', sponsor_user=sponsor_user, values=values,
                           sponsors=get_sponsors(), error=error), (400 if error else 200)

@admin_bp.route('/admin/admin-users')
@login_required
def admin_admin_users():
    require_admin()
    return render_template('admin_admin_users.html', admin_users=get_admin_users())


@admin_bp.route('/admin/admin-users/<int:admin_user_id>')
@login_required
def admin_admin_user(admin_user_id):
    require_admin()
    admin_user = get_admin_user(admin_user_id)
    if not admin_user:
        abort(404)
    return render_template('admin_admin_user.html', admin_user=admin_user,
                           updated=request.args.get('updated') == '1')

@admin_bp.route('/admin/admin-users/<int:admin_user_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_admin_user(admin_user_id):
    require_admin()
    admin_user = get_admin_user(admin_user_id)
    if not admin_user:
        abort(404)

    is_self = admin_user['username'] == g.user['username']
    error = None
    fields = ('first_name', 'last_name', 'username', 'email')
    values: dict = {k: admin_user[k] or '' for k in fields}
    values['account_locked'] = bool(admin_user['account_locked'])

    if request.method == 'POST':
        check_csrf()
        values = {k: request.form.get(k, '').strip() for k in fields}
        values['email'] = values['email'].lower()
        values['account_locked'] = request.form.get('account_locked') == 'on'
        try:
            if is_self and values['account_locked']:
                raise AdminError("You can't lock your own account.")
            if not update_admin_user(admin_user_id, values):
                abort(404)
            if is_self:
                session['username'] = values['username']
            return redirect(f'/admin/admin-users/{admin_user_id}?updated=1')
        except AdminError as exc:
            error = str(exc)

    return render_template('admin_admin_user_form.html', admin_user=admin_user, values=values,
                           is_self=is_self, error=error), (400 if error else 200)

@admin_bp.route('/admin/password', methods=['GET', 'POST'])
@login_required
def admin_change_password():
    require_admin()
    error = None
    if request.method == 'POST':
        check_csrf()
        try:
            change_own_password(g.user['user_id'],
                                request.form.get('current_password', ''),
                                request.form.get('new_password', ''),
                                request.form.get('confirm_password', ''))
            return redirect('/profile?password_changed=1')
        except AdminError as exc:
            error = str(exc)

    return render_template('admin_password_form.html', error=error), (400 if error else 200)