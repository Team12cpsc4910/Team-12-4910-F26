from flask import Blueprint, render_template, request, session, redirect

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


# Sponsor Applications tab: finds the logged-in sponsor user's company, then lists every
# pending application drivers have submitted to that company.
def get_sponsor_applications(username):
    applications = []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT su.sponsor_id
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                WHERE u.username = %s
            """, (username,))
            sponsor = cur.fetchone()

            if sponsor:
                cur.execute("""
                    SELECT a.app_id, d.first_name, d.last_name, a.state,
                           (SELECT MIN(l.datetime) FROM DriverAppLog l WHERE l.app_id = a.app_id) AS submitted
                    FROM DriverApp a
                    JOIN DriverUser d ON a.driver_id = d.driver_id
                    WHERE a.sponsor_id = %s AND LOWER(a.state) = 'pending'
                    ORDER BY a.app_id DESC
                """, (sponsor['sponsor_id'],))
                applications = cur.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if conn and conn.open:
            conn.close()
    return applications


@applications_bp.route('/sponsor/applications')
def sponsor_applications():
    if 'username' not in session:
        return redirect('/')

    # Same role check as the dashboard: the "sponsor" test login or a real sponsor account
    current_user = session['username']
    if current_user != 'sponsor' and session.get('user_type', '').lower() != 'sponsor':
        return redirect('/dashboard')

    return render_template('sponsor_applications.html',
                           pending=get_sponsor_applications(current_user))
