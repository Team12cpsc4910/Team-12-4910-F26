from flask import Blueprint, render_template, session, redirect, abort

from database import get_db_connection
from routes.applications import is_sponsor_session, find_sponsor_id

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

    return render_template('sponsor_drivers.html', drivers=get_sponsor_drivers(session['username']))


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
