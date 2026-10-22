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


@company_bp.route('/sponsor/company', methods=['GET', 'POST'])
def sponsor_company():
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')

    company = get_sponsor_company(session['username'])
    error = None
    values = {}
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
            error = str(exc)

    return render_template('sponsor_company.html', company=company, values=values, error=error,
                           saved=request.args.get('saved') == '1',
                           max_length=MAX_COMPANY_NAME_LENGTH), (400 if error else 200)
