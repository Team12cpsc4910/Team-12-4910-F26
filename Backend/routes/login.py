from flask import Blueprint, request, render_template, session, redirect, g
from werkzeug.security import check_password_hash
from database import get_db_connection
from security import login_required, check_csrf

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')

    if username == "admin" and password == "test":
        session['username'] = username
        session['user_type'] = 'Admin'
        return redirect('/dashboard')

    elif username == "driver" and password == "test":
        session['username'] = username
        session['user_type'] = 'Driver'
        return redirect('/dashboard')

    elif username == "sponsor" and password == "test":
        session['username'] = username
        session['user_type'] = 'Sponsor'
        return redirect('/dashboard')

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, username, password_hash, user_type
                FROM UserAccount
                WHERE username = %s
            """, (username,))

            user = cur.fetchone()

    finally:
        conn.close()

    if user and check_password_hash(user['password_hash'], password):
        session['user_id'] = user['user_id']
        session['username'] = user['username']
        session['user_type'] = user['user_type']

        return redirect('/dashboard')

    return "Invalid username or password. Please press back and try again."


def get_sponsor_point_report(username):
    report = []
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
                    SELECT d.first_name,
                           d.last_name,
                           p.datetime,
                           p.points_change,
                           p.reason,
                           COALESCE(
                               CONCAT(su.first_name, ' ', su.last_name),
                               'Unknown'
                           ) AS changed_by
                    FROM PointChangeLog p
                    JOIN DriverUser d
                        ON p.driver_id = d.driver_id
                    LEFT JOIN SponsorUser su
                        ON p.sponsor_user_id = su.sponsor_user_id
                    WHERE d.sponsor_id = %s
                    ORDER BY p.datetime DESC
                """, (sponsor['sponsor_id'],))

                report = cur.fetchall()

    except Exception as e:
        print(f"Database error: {e}")

    finally:
        if conn and conn.open:
            conn.close()

    return report


@auth_bp.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect('/')

    current_user = session['username']
    user_type = session.get('user_type')

    if user_type == "Admin":
        return render_template('admin_dashboard.html')

    elif user_type == "Sponsor":
        return render_template(
            'sponsor_dashboard.html',
            report=get_sponsor_point_report(current_user)
        )

    elif user_type == "Driver":
        points = 0
        history = []
        catalog = []
        approved = False
        conn = None

        try:
            conn = get_db_connection()

            with conn.cursor() as cur:
                cur.execute("""
                    SELECT d.driver_id,
                           d.points,
                           d.sponsor_id,
                           d.application_status
                    FROM DriverUser d
                    JOIN UserAccount u
                        ON d.user_id = u.user_id
                    WHERE u.username = %s
                """, (current_user,))

                driver = cur.fetchone()

                if driver:
                    points = driver['points']

                    cur.execute("""
                        SELECT p.datetime,
                               p.points_change,
                               p.reason,
                               COALESCE(
                                   CONCAT(su.first_name, ' ', su.last_name),
                                   s.sponsor_name
                               ) AS sponsor_name
                        FROM PointChangeLog p
                        JOIN DriverUser d
                            ON p.driver_id = d.driver_id
                        JOIN Sponsor s
                            ON d.sponsor_id = s.sponsor_id
                        LEFT JOIN SponsorUser su
                            ON p.sponsor_user_id = su.sponsor_user_id
                        WHERE p.driver_id = %s
                        ORDER BY p.datetime DESC
                    """, (driver['driver_id'],))

                    history = cur.fetchall()

                    approved = driver['application_status'] == 'Approved'

                    if approved:
                        cur.execute("""
                            SELECT product_name,
                                   description,
                                   point_cost,
                                   Availability AS available
                            FROM ProductCatalog
                            WHERE sponsor_id = %s
                            ORDER BY product_name
                        """, (driver['sponsor_id'],))

                        catalog = cur.fetchall()

        except Exception as e:
            print(f"Database error: {e}")

        finally:
            if conn and conn.open:
                conn.close()

        return render_template(
            'driver_dashboard.html',
            points=points,
            history=history,
            catalog=catalog,
            approved=approved
        )

    else:
        session.clear()
        return redirect('/')


@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_id = g.user['user_id']
    user_type = g.user['user_type']

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:

            if request.method == 'POST':
                check_csrf()

                new_email = request.form.get('email', '').strip().lower()
                first_name = request.form.get('first_name', '').strip()
                last_name = request.form.get('last_name', '').strip()

                if not new_email or not first_name or not last_name:
                    return "Email, first name, and last name are required.", 400

                cur.execute("""
                    UPDATE UserAccount
                    SET email = %s
                    WHERE user_id = %s
                """, (new_email, user_id))

                if user_type == 'Driver':
                    cur.execute("""
                        UPDATE DriverUser
                        SET first_name = %s,
                            last_name = %s
                        WHERE user_id = %s
                    """, (first_name, last_name, user_id))

                elif user_type == 'Sponsor':
                    cur.execute("""
                        UPDATE SponsorUser
                        SET first_name = %s,
                            last_name = %s
                        WHERE user_id = %s
                    """, (first_name, last_name, user_id))

                elif user_type == 'Admin':
                    cur.execute("""
                        UPDATE AdminUser
                        SET first_name = %s,
                            last_name = %s
                        WHERE user_id = %s
                    """, (first_name, last_name, user_id))

                conn.commit()

            if user_type == 'Driver':
                cur.execute("""
                    SELECT
                        u.username,
                        u.email,
                        u.user_type,
                        d.first_name,
                        d.last_name,
                        d.points,
                        d.application_status,
                        s.sponsor_name
                    FROM UserAccount u
                    JOIN DriverUser d
                        ON u.user_id = d.user_id
                    JOIN Sponsor s
                        ON d.sponsor_id = s.sponsor_id
                    WHERE u.user_id = %s
                """, (user_id,))

            elif user_type == 'Sponsor':
                cur.execute("""
                    SELECT
                        u.username,
                        u.email,
                        u.user_type,
                        su.first_name,
                        su.last_name,
                        s.sponsor_name
                    FROM UserAccount u
                    JOIN SponsorUser su
                        ON u.user_id = su.user_id
                    JOIN Sponsor s
                        ON su.sponsor_id = s.sponsor_id
                    WHERE u.user_id = %s
                """, (user_id,))

            elif user_type == 'Admin':
                cur.execute("""
                    SELECT
                        u.username,
                        u.email,
                        u.user_type,
                        a.first_name,
                        a.last_name
                    FROM UserAccount u
                    JOIN AdminUser a
                        ON u.user_id = a.user_id
                    WHERE u.user_id = %s
                """, (user_id,))

            user = cur.fetchone()

    finally:
        conn.close()

    return render_template('profile.html', user=user)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect('/')
