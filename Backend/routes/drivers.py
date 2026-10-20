from flask import Blueprint, render_template, request, session, redirect, abort

from accounts import create_account, AccountValidationError
from database import get_db_connection
from routes.applications import is_sponsor_session, find_sponsor_id
from security import check_csrf

drivers_bp = Blueprint('drivers', __name__)

# Cap on a single point change, so a typo can't hand out a huge balance
MAX_POINTS_PER_CHANGE = 10000
# PointChangeLog.reason is VARCHAR(255)
MAX_REASON_LENGTH = 255


class PointsError(ValueError):
    pass


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
                ORDER BY p.datetime DESC, p.point_log_id DESC
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


# Add Points: rewards an approved driver in the sponsor's program, recording the reason and
# which sponsor user gave the points. Returns False if the driver isn't in the sponsor's program.
def add_driver_points(username, driver_id, points, reason):
    if not points.isdigit() or not 1 <= int(points) <= MAX_POINTS_PER_CHANGE:
        raise PointsError(f'Enter a whole number of points from 1 to {MAX_POINTS_PER_CHANGE:,}.')
    if not reason:
        raise PointsError('Enter a reason for adding these points.')
    if len(reason) > MAX_REASON_LENGTH:
        raise PointsError(f'The reason must be {MAX_REASON_LENGTH} characters or fewer.')
    points = int(points)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT su.sponsor_user_id, su.sponsor_id
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                WHERE u.username = %s
            """, (username,))
            sponsor_user = cur.fetchone()
            if not sponsor_user:
                return False

            # Locking the driver row keeps two point changes for the same driver from interleaving
            cur.execute("""
                SELECT application_status FROM DriverUser
                WHERE driver_id = %s AND sponsor_id = %s
                FOR UPDATE
            """, (driver_id, sponsor_user['sponsor_id']))
            driver = cur.fetchone()
            if not driver:
                return False
            if driver['application_status'] != 'Approved':
                raise PointsError('Points can only be added to approved drivers.')

            cur.execute('SELECT COALESCE(MAX(point_log_id), 0) AS last_id FROM PointChangeLog WHERE driver_id = %s',
                        (driver_id,))
            last_id = cur.fetchone()['last_id']

            cur.execute('UPDATE DriverUser SET points = points + %s WHERE driver_id = %s', (points, driver_id))

            # The log_points_change trigger (if installed) logs a generic "Points added" row with no
            # reason or sponsor user. Fill those in on that row; otherwise write the log row here.
            cur.execute("""
                SELECT point_log_id FROM PointChangeLog
                WHERE driver_id = %s AND point_log_id > %s
                ORDER BY point_log_id DESC LIMIT 1
            """, (driver_id, last_id))
            logged = cur.fetchone()
            if logged:
                cur.execute('UPDATE PointChangeLog SET reason = %s, sponsor_user_id = %s WHERE point_log_id = %s',
                            (reason, sponsor_user['sponsor_user_id'], logged['point_log_id']))
            else:
                cur.execute("""
                    INSERT INTO PointChangeLog (driver_id, sponsor_user_id, points_change, reason)
                    VALUES (%s, %s, %s, %s)
                """, (driver_id, sponsor_user['sponsor_user_id'], points, reason))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def render_driver_profile(driver, history, **extra):
    return render_template('sponsor_driver.html', driver=driver, history=history,
                           max_points=MAX_POINTS_PER_CHANGE, max_reason=MAX_REASON_LENGTH,
                           values=extra.pop('values', {}), **extra)


@drivers_bp.route('/sponsor/drivers/<int:driver_id>')
def sponsor_driver_profile(driver_id):
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')

    driver, history = get_sponsor_driver(session['username'], driver_id)
    if not driver:
        abort(404)
    points_added = request.args.get('points_added', '')
    return render_driver_profile(driver, history, points_added=points_added if points_added.isdigit() else None)


@drivers_bp.route('/sponsor/drivers/<int:driver_id>/points', methods=['POST'])
def add_sponsor_driver_points(driver_id):
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')
    check_csrf()

    values = {k: request.form.get(k, '').strip() for k in ('points', 'reason')}
    try:
        if not add_driver_points(session['username'], driver_id, values['points'], values['reason']):
            abort(404)
    except PointsError as exc:
        driver, history = get_sponsor_driver(session['username'], driver_id)
        if not driver:
            abort(404)
        return render_driver_profile(driver, history, values=values, points_error=str(exc)), 400
    return redirect(f"/sponsor/drivers/{driver_id}?points_added={int(values['points'])}")
