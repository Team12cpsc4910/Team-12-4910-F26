from flask import Blueprint, render_template, session, redirect

from database import get_db_connection

applications_bp = Blueprint('applications', __name__)


# Driver Applications tab: lists the logged-in driver's applications that are still
# waiting on a sponsor decision, so the driver can see where they've already applied.
def get_pending_applications(username):
    applications = []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # DriverApp.driver_id references DriverUser.user_id (see Database/Driver.App.sql)
            cur.execute("""
                SELECT a.app_id, s.sponsor_name, a.state,
                       (SELECT MIN(l.datetime) FROM DriverAppLog l WHERE l.app_id = a.app_id) AS submitted
                FROM DriverApp a
                JOIN DriverUser d ON a.driver_id = d.user_id
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
