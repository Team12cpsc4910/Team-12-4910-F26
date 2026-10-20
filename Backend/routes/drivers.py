from flask import Blueprint, render_template, request, session, redirect, abort

from accounts import create_account, AccountValidationError
from database import get_db_connection
from routes.applications import is_sponsor_session, find_sponsor_id
from security import check_csrf

drivers_bp = Blueprint('drivers', __name__)


# Sponsor Drivers tab: every driver in the logged-in sponsor's program, so the sponsor
# can pick one to view.
def get_sponsor_drivers(username):
    drivers = []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            sponsor_id = find_sponsor_id(cur, username)
            if sponsor_id:
                cur.execute("""
                    SELECT d.driver_id, d.first_name, d.last_name, d.points, d.application_status
                    FROM DriverUser d
                    WHERE d.sponsor_id = %s
                    ORDER BY d.last_name, d.first_name
                """, (sponsor_id,))
                drivers = cur.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if conn and conn.open:
            conn.close()
    return drivers


# Driver Profile: one driver's account details, status, and point history, or None if the
# driver doesn't exist or belongs to a different sponsor company.
def get_sponsor_driver(username, driver_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sponsor_id = find_sponsor_id(cur, username)
            if not sponsor_id:
                return None, []
            cur.execute("""
                SELECT d.driver_id, d.first_name, d.last_name, d.points, d.application_status,
                       u.username, u.email, u.account_locked, s.sponsor_name
                FROM DriverUser d
                JOIN UserAccount u ON d.user_id = u.user_id
                JOIN Sponsor s ON d.sponsor_id = s.sponsor_id
                WHERE d.driver_id = %s AND d.sponsor_id = %s
            """, (driver_id, sponsor_id))
            driver = cur.fetchone()
            if not driver:
                return None, []

            cur.execute("""
                SELECT p.datetime, p.points_change, p.reason,
                       COALESCE(CONCAT(su.first_name, ' ', su.last_name), 'Unknown') AS changed_by
                FROM PointChangeLog p
                LEFT JOIN SponsorUser su ON p.sponsor_user_id = su.sponsor_user_id
                WHERE p.driver_id = %s
                ORDER BY p.datetime DESC
            """, (driver_id,))
            return driver, cur.fetchall()
    finally:
        conn.close()


@drivers_bp.route('/sponsor/drivers')
def sponsor_drivers():
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')

    return render_template('sponsor_drivers.html', drivers=get_sponsor_drivers(session['username']),
                           added=request.args.get('added') == '1')


# The logged-in sponsor user's company (id, name, point value), or None if the account isn't linked to one
def get_sponsor_company(username):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.sponsor_id, s.sponsor_name, s.point_value
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                JOIN Sponsor s ON su.sponsor_id = s.sponsor_id
                WHERE u.username = %s
            """, (username,))
            return cur.fetchone()
    except Exception as e:
        print(f"Database error: {e}")
        return None
    finally:
        if conn and conn.open:
            conn.close()


# Add Driver: a sponsor onboards a driver directly into their own program. The driver is
# created already Approved, since the sponsor is adding them without an application.
@drivers_bp.route('/sponsor/drivers/new', methods=['GET', 'POST'])
def add_sponsor_driver():
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
        values = {k: request.form.get(k, '').strip() for k in ('username', 'email', 'first_name', 'last_name')}
        values['email'] = values['email'].lower()
        # The company always comes from the sponsor's own account, never from the form
        data = {**values, 'sponsor_id': str(company['sponsor_id']), 'company_name': '',
                'password': request.form.get('password', ''),
                'confirm_password': request.form.get('confirm_password', '')}
        try:
            create_account(data, 'Driver', driver_status='Approved')
            return redirect('/sponsor/drivers?added=1')
        except AccountValidationError as exc:
            error = str(exc)

    return render_template('sponsor_driver_form.html', company=company, values=values,
                           error=error), (400 if error else 200)


@drivers_bp.route('/sponsor/drivers/<int:driver_id>')
def sponsor_driver_profile(driver_id):
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')

    driver, history = get_sponsor_driver(session['username'], driver_id)
    if not driver:
        abort(404)
    return render_template('sponsor_driver.html', driver=driver, history=history)
