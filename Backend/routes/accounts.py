from flask import Blueprint, render_template, request, g, abort
from accounts import create_account, AccountValidationError
from database import get_db_connection
from security import login_required, check_csrf

accounts_bp = Blueprint('accounts', __name__)


def account_form(admin_mode=False):
    error = None
    values = {}
    role = 'Driver'
    if request.method == 'POST':
        check_csrf()
        values = {k: request.form.get(k, '').strip() for k in
                  ('username', 'email', 'first_name', 'last_name', 'sponsor_id', 'company_name')}
        values['email'] = values['email'].lower()
        # The public route never accepts a role or company-creation privilege.
        role = request.form.get('role', '') if admin_mode else 'Driver'
        if not admin_mode:
            values['company_name'] = ''
        data = {**values, 'password': request.form.get('password', ''),
                'confirm_password': request.form.get('confirm_password', '')}
        try:
            create_account(data, role, allow_new_company=admin_mode)
            return render_template('create_account.html', success=True, admin_mode=admin_mode, created_role=role), 201
        except AccountValidationError as exc:
            error = str(exc)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT sponsor_id, sponsor_name FROM Sponsor ORDER BY sponsor_name')
            sponsors = cur.fetchall()
    finally:
        conn.close()
    return render_template('create_account.html', admin_mode=admin_mode, values=values,
                           role=role, sponsors=sponsors, error=error), (400 if error else 200)


@accounts_bp.route('/register', methods=['GET', 'POST'])
def register():
    return account_form()


@accounts_bp.route('/admin/accounts/new', methods=['GET', 'POST'])
@login_required
def admin_create():
    if g.user['user_type'].lower() != 'admin':
        abort(403)
    # Require the matching admin profile, not just a claimed cookie role.
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT admin_user_id FROM AdminUser WHERE user_id = %s', (g.user['user_id'],))
            if not cur.fetchone():
                abort(403)
    finally:
        conn.close()
    return account_form(admin_mode=True)
