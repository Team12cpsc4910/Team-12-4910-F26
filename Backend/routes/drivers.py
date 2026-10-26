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


class DropError(ValueError):
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
                        AND d.application_status IN ('Approved', 'Dropped')
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
                    AND d.application_status IN ('Approved', 'Dropped')
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


# Add/Deduct Points: rewards or penalizes an approved driver in the sponsor's program, recording
# the reason and which sponsor user made the change. Returns False if the driver isn't in the
# sponsor's program.
def change_driver_points(username, driver_id, points, reason, *, deduct=False):
    verb = 'deducting' if deduct else 'adding'
    if not points.isdigit() or not 1 <= int(points) <= MAX_POINTS_PER_CHANGE:
        raise PointsError(f'Enter a whole number of points from 1 to {MAX_POINTS_PER_CHANGE:,}.')
    if not reason:
        raise PointsError(f'Enter a reason for {verb} these points.')
    if len(reason) > MAX_REASON_LENGTH:
        raise PointsError(f'The reason must be {MAX_REASON_LENGTH} characters or fewer.')
    change = -int(points) if deduct else int(points)

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
                SELECT application_status, points FROM DriverUser
                WHERE driver_id = %s AND sponsor_id = %s
                FOR UPDATE
            """, (driver_id, sponsor_user['sponsor_id']))
            driver = cur.fetchone()
            if not driver:
                return False
            if driver['application_status'] != 'Approved':
                raise PointsError(f"Points can only be {'deducted from' if deduct else 'added to'} approved drivers.")
            # DriverUser.points has CHECK (points >= 0), so a deduction can't overdraw the balance
            if driver['points'] + change < 0:
                raise PointsError(f"This driver only has {driver['points']} points, so you can't deduct {points}.")

            cur.execute('SELECT COALESCE(MAX(point_log_id), 0) AS last_id FROM PointChangeLog WHERE driver_id = %s',
                        (driver_id,))
            last_id = cur.fetchone()['last_id']

            cur.execute('UPDATE DriverUser SET points = points + %s WHERE driver_id = %s', (change, driver_id))

            # The log_points_change trigger (if installed) logs a generic "Points added"/"Points removed"
            # row with no reason or sponsor user. Fill those in on that row; otherwise write the log row here.
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
                """, (driver_id, sponsor_user['sponsor_user_id'], change, reason))
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


# Drop Driver: removes an approved driver from the sponsor's program by marking them 'Dropped'.
# Catalog access and point changes require 'Approved', so the driver loses access right away;
# their account, balance, and point history are kept. Returns False if the driver isn't in the
# sponsor's program.
def drop_driver(username, driver_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sponsor_id = find_sponsor_id(cur, username)
            if not sponsor_id:
                return False
            cur.execute("""
                SELECT application_status FROM DriverUser
                WHERE driver_id = %s AND sponsor_id = %s
                FOR UPDATE
            """, (driver_id, sponsor_id))
            driver = cur.fetchone()
            if not driver:
                return False
            if driver['application_status'] != 'Approved':
                raise DropError('Only approved drivers in your program can be dropped.')

            cur.execute("UPDATE DriverUser SET application_status = 'Dropped' WHERE driver_id = %s", (driver_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
    points_deducted = request.args.get('points_deducted', '')
    return render_driver_profile(driver, history,
                                 points_added=points_added if points_added.isdigit() else None,
                                 points_deducted=points_deducted if points_deducted.isdigit() else None,
                                 dropped=request.args.get('dropped') == '1')


@drivers_bp.route('/sponsor/drivers/<int:driver_id>/drop', methods=['POST'])
def drop_sponsor_driver(driver_id):
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')
    check_csrf()

    try:
        if not drop_driver(session['username'], driver_id):
            abort(404)
    except DropError as exc:
        driver, history = get_sponsor_driver(session['username'], driver_id)
        if not driver:
            abort(404)
        return render_driver_profile(driver, history, drop_error=str(exc)), 409
    return redirect(f'/sponsor/drivers/{driver_id}?dropped=1')


# Shared by the Add Points and Deduct Points forms; form_name says which form shows the error
def handle_points_form(driver_id, *, deduct):
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')
    check_csrf()

    form_name = 'deduct' if deduct else 'add'
    values = {k: request.form.get(k, '').strip() for k in ('points', 'reason')}
    try:
        if not change_driver_points(session['username'], driver_id, values['points'], values['reason'],
                                    deduct=deduct):
            abort(404)
    except PointsError as exc:
        driver, history = get_sponsor_driver(session['username'], driver_id)
        if not driver:
            abort(404)
        return render_driver_profile(driver, history, values=values, points_error=str(exc),
                                     error_form=form_name), 400
    result = 'points_deducted' if deduct else 'points_added'
    return redirect(f"/sponsor/drivers/{driver_id}?{result}={int(values['points'])}")


@drivers_bp.route('/sponsor/drivers/<int:driver_id>/points', methods=['POST'])
def add_sponsor_driver_points(driver_id):
    return handle_points_form(driver_id, deduct=False)


@drivers_bp.route('/sponsor/drivers/<int:driver_id>/points/deduct', methods=['POST'])
def deduct_sponsor_driver_points(driver_id):
    return handle_points_form(driver_id, deduct=True)
