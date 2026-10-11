from flask import Blueprint, render_template, request, session, redirect, abort

from database import get_db_connection
from security import check_csrf

applications_bp = Blueprint('applications', __name__)

# DriverApp.app is VARCHAR(255), so the driver's message has to fit in it
MAX_APPLICATION_LENGTH = 255


class ApplicationError(ValueError):
    pass


# Driver Applications tab: lists the logged-in driver's applications that are still
# waiting on a sponsor decision, so the driver can see where they've already applied.
def get_pending_applications(username):
    applications = []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.app_id, s.sponsor_name, a.state,
                       (SELECT MIN(l.datetime) FROM DriverAppLog l WHERE l.app_id = a.app_id) AS submitted
                FROM DriverApp a
                JOIN DriverUser d ON a.driver_id = d.driver_id
                JOIN UserAccount u ON d.user_id = u.user_id
                JOIN Sponsor s ON a.sponsor_id = s.sponsor_id
                WHERE u.username = %s AND LOWER(a.state) = 'pending'
                ORDER BY a.app_id DESC
            """, (username,))
            applications = cur.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if conn and conn.open:
            conn.close()
    return applications


@applications_bp.route('/applications')
def applications():
    if 'username' not in session:
        return redirect('/')

    return render_template('applications.html',
                           pending=get_pending_applications(session['username']))


# Sponsor dropdown for the application form; an empty list just shows "no sponsors" instead of crashing
def get_sponsors():
    sponsors = []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute('SELECT sponsor_id, sponsor_name FROM Sponsor ORDER BY sponsor_name')
            sponsors = cur.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if conn and conn.open:
            conn.close()
    return sponsors


# Submit Application: saves a pending DriverApp row for the logged-in driver.
# The log_new_app trigger records the "App submitted" entry in DriverAppLog.
def submit_application(username, sponsor_id, message):
    if not sponsor_id.isdigit():
        raise ApplicationError('Please choose a sponsor.')
    if not message:
        raise ApplicationError('Please tell the sponsor why you want to join their program.')
    if len(message) > MAX_APPLICATION_LENGTH:
        raise ApplicationError(f'Your message must be {MAX_APPLICATION_LENGTH} characters or fewer.')

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.driver_id
                FROM DriverUser d
                JOIN UserAccount u ON d.user_id = u.user_id
                WHERE u.username = %s
            """, (username,))
            driver = cur.fetchone()
            if not driver:
                raise ApplicationError('Only driver accounts can submit applications.')

            cur.execute('SELECT sponsor_id FROM Sponsor WHERE sponsor_id = %s', (int(sponsor_id),))
            if not cur.fetchone():
                raise ApplicationError('Please choose a sponsor.')

            # One pending application per sponsor, so a driver can't flood a company
            cur.execute("""
                SELECT app_id FROM DriverApp
                WHERE driver_id = %s AND sponsor_id = %s AND LOWER(state) = 'pending'
            """, (driver['driver_id'], int(sponsor_id)))
            if cur.fetchone():
                raise ApplicationError('You already have a pending application with this sponsor.')

            cur.execute("""
                INSERT INTO DriverApp (driver_id, sponsor_id, app)
                VALUES (%s, %s, %s)
            """, (driver['driver_id'], int(sponsor_id), message))
        conn.commit()
    except ApplicationError:
        raise
    except Exception as e:
        print(f"Database error: {e}")
        raise ApplicationError('Your application could not be submitted right now. Please try again later.')
    finally:
        if conn and conn.open:
            conn.close()


@applications_bp.route('/applications/new', methods=['GET', 'POST'])
def new_application():
    if 'username' not in session:
        return redirect('/')

    error = None
    values = {}
    if request.method == 'POST':
        check_csrf()
        values = {k: request.form.get(k, '').strip() for k in ('sponsor_id', 'message')}
        try:
            submit_application(session['username'], values['sponsor_id'], values['message'])
            return redirect('/applications')
        except ApplicationError as exc:
            error = str(exc)

    return render_template('application_form.html', sponsors=get_sponsors(), values=values,
                           error=error, max_length=MAX_APPLICATION_LENGTH), (400 if error else 200)


# Same role check as the dashboard: the "sponsor" test login or a real sponsor account
def is_sponsor_session():
    return session['username'] == 'sponsor' or session.get('user_type', '').lower() == 'sponsor'


# The logged-in sponsor user's company, so every sponsor query only touches that company's applications
def find_sponsor_id(cur, username):
    cur.execute("""
        SELECT su.sponsor_id
        FROM SponsorUser su
        JOIN UserAccount u ON su.user_id = u.user_id
        WHERE u.username = %s
    """, (username,))
    sponsor = cur.fetchone()
    return sponsor['sponsor_id'] if sponsor else None


# Sponsor Applications tab: lists every pending application drivers have submitted to the sponsor's company.
def get_sponsor_applications(username):
    applications = []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            sponsor_id = find_sponsor_id(cur, username)
            if sponsor_id:
                cur.execute("""
                    SELECT a.app_id, d.first_name, d.last_name, a.state,
                           (SELECT MIN(l.datetime) FROM DriverAppLog l WHERE l.app_id = a.app_id) AS submitted
                    FROM DriverApp a
                    JOIN DriverUser d ON a.driver_id = d.driver_id
                    WHERE a.sponsor_id = %s AND LOWER(a.state) = 'pending'
                    ORDER BY a.app_id DESC
                """, (sponsor_id,))
                applications = cur.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if conn and conn.open:
            conn.close()
    return applications


# View Application: one application with the driver's message, or None if it
# doesn't exist or was sent to a different sponsor company.
def get_sponsor_application(username, app_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sponsor_id = find_sponsor_id(cur, username)
            if not sponsor_id:
                return None
            cur.execute("""
                SELECT a.app_id, a.app, a.state, d.first_name, d.last_name, u.email,
                       (SELECT MIN(l.datetime) FROM DriverAppLog l WHERE l.app_id = a.app_id) AS submitted
                FROM DriverApp a
                JOIN DriverUser d ON a.driver_id = d.driver_id
                JOIN UserAccount u ON d.user_id = u.user_id
                WHERE a.app_id = %s AND a.sponsor_id = %s
            """, (app_id, sponsor_id))
            return cur.fetchone()
    finally:
        conn.close()


# Approve Application: marks the application approved and adds the driver to the sponsor's
# program in one transaction. Approval unlocks the sponsor's catalog on the driver dashboard.
# Returns False if the application doesn't exist or belongs to a different sponsor company.
def approve_application(username, app_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sponsor_id = find_sponsor_id(cur, username)
            if not sponsor_id:
                return False
            cur.execute("""
                SELECT driver_id, state FROM DriverApp
                WHERE app_id = %s AND sponsor_id = %s
                FOR UPDATE
            """, (app_id, sponsor_id))
            application = cur.fetchone()
            if not application:
                return False
            if application['state'].lower() != 'pending':
                raise ApplicationError(f"This application has already been {application['state'].lower()}.")

            cur.execute("UPDATE DriverApp SET state = 'approved' WHERE app_id = %s", (app_id,))
            # DriverUser holds a single sponsor_id, so joining this program moves the driver to this sponsor
            cur.execute("""
                UPDATE DriverUser
                SET application_status = 'Approved', sponsor_id = %s
                WHERE driver_id = %s
            """, (sponsor_id, application['driver_id']))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@applications_bp.route('/sponsor/applications')
def sponsor_applications():
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')

    return render_template('sponsor_applications.html',
                           pending=get_sponsor_applications(session['username']),
                           approved=request.args.get('approved') == '1')


@applications_bp.route('/sponsor/applications/<int:app_id>')
def view_sponsor_application(app_id):
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')

    application = get_sponsor_application(session['username'], app_id)
    if not application:
        abort(404)
    return render_template('sponsor_application.html', application=application)


@applications_bp.route('/sponsor/applications/<int:app_id>/approve', methods=['POST'])
def approve_sponsor_application(app_id):
    if 'username' not in session:
        return redirect('/')
    if not is_sponsor_session():
        return redirect('/dashboard')
    check_csrf()

    try:
        if not approve_application(session['username'], app_id):
            abort(404)
    except ApplicationError as exc:
        application = get_sponsor_application(session['username'], app_id)
        return render_template('sponsor_application.html', application=application, error=str(exc)), 409
    return redirect('/sponsor/applications?approved=1')
