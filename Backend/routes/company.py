from flask import Blueprint, render_template, request, session, redirect, abort

from database import get_db_connection
from routes.applications import is_sponsor_session
from routes.drivers import get_sponsor_company
from security import check_csrf

company_bp = Blueprint('company', __name__)

# Sponsor.sponsor_name is VARCHAR(100)
MAX_COMPANY_NAME_LENGTH = 100


class CompanyError(ValueError):
    pass


# Company Profile: renames the logged-in sponsor's own company. The name shows up in the
# driver sign-up and application dropdowns, so it can't match another sponsor's name.
def update_company_name(sponsor_id, name):
    if not name:
        raise CompanyError('Enter a company name.')
    if len(name) > MAX_COMPANY_NAME_LENGTH:
        raise CompanyError(f'Company name must be {MAX_COMPANY_NAME_LENGTH} characters or fewer.')

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT sponsor_id FROM Sponsor
                WHERE LOWER(sponsor_name) = LOWER(%s) AND sponsor_id <> %s
            """, (name, sponsor_id))
            if cur.fetchone():
                raise CompanyError('Another sponsor company already uses that name.')

            cur.execute('UPDATE Sponsor SET sponsor_name = %s WHERE sponsor_id = %s', (name, sponsor_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Sponsor Users: everyone with a sponsor login at the same company, plus which one is the
# logged-in user, so coworkers can manage each other's access.
def get_company_users(username):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT su.sponsor_user_id, su.sponsor_id
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                WHERE u.username = %s
            """, (username,))
            me = cur.fetchone()
            if not me:
                return None, []
            cur.execute("""
                SELECT su.sponsor_user_id, su.first_name, su.last_name, u.username, u.account_locked
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                WHERE su.sponsor_id = %s
                ORDER BY su.last_name, su.first_name
            """, (me['sponsor_id'],))
            return me['sponsor_user_id'], cur.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
        return None, []
    finally:
        if conn and conn.open:
            conn.close()


# Deactivate Sponsor User: locks a coworker's login (UserAccount.account_locked, the same flag the
# admin Sponsor Users page uses) so a former employee can't log in. Returns False if the login
# doesn't belong to the same company.
def deactivate_sponsor_user(username, sponsor_user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT su.sponsor_user_id, su.sponsor_id
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                WHERE u.username = %s
            """, (username,))
            me = cur.fetchone()
            if not me:
                return False

            cur.execute("""
                SELECT su.user_id, u.account_locked
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                WHERE su.sponsor_user_id = %s AND su.sponsor_id = %s
                FOR UPDATE
            """, (sponsor_user_id, me['sponsor_id']))
            target = cur.fetchone()
            if not target:
                return False
            # Blocking self-deactivation also guarantees the company always keeps an active login
            if sponsor_user_id == me['sponsor_user_id']:
                raise CompanyError("You can't deactivate your own login.")
            if target['account_locked']:
                raise CompanyError('That login is already deactivated.')

            cur.execute('UPDATE UserAccount SET account_locked = TRUE WHERE user_id = %s', (target['user_id'],))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def render_company_page(company, status=200, **extra):
    my_id, users = get_company_users(session['username'])
    return render_template('sponsor_company.html', company=company, values=extra.pop('values', {}),
                           saved=request.args.get('saved') == '1',
                           deactivated=request.args.get('deactivated') == '1',
                           users=users, my_sponsor_user_id=my_id,
                           max_length=MAX_COMPANY_NAME_LENGTH, **extra), status


@company_bp.route('/sponsor/company', methods=['GET', 'POST'])
def sponsor_company():
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')

    company = get_sponsor_company(session['username'])
    if request.method == 'POST':
        check_csrf()
        if not company:
            abort(403)
        values = {'sponsor_name': request.form.get('sponsor_name', '').strip()}
        try:
            # The company always comes from the sponsor's own account, never from the form
            update_company_name(company['sponsor_id'], values['sponsor_name'])
            return redirect('/sponsor/company?saved=1')
        except CompanyError as exc:
            return render_company_page(company, 400, values=values, error=str(exc))

    return render_company_page(company)


@company_bp.route('/sponsor/company/users/<int:sponsor_user_id>/deactivate', methods=['POST'])
def deactivate_company_user(sponsor_user_id):
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')
    check_csrf()

    try:
        if not deactivate_sponsor_user(session['username'], sponsor_user_id):
            abort(404)
    except CompanyError as exc:
        return render_company_page(get_sponsor_company(session['username']), 409, users_error=str(exc))
    return redirect('/sponsor/company?deactivated=1')
