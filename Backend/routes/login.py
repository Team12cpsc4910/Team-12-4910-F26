from flask import Blueprint, request, render_template, session, redirect, g, current_app
from werkzeug.security import check_password_hash
from database import get_db_connection
from security import login_required, check_csrf
from routes.rewards import get_sponsor_catalog

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['POST'])
def login():
    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''

    if not username or not password:
        return 'Enter a username and password.', 400

    # Every role needs a real UserAccount record and a user_id. The old
    # admin/test, driver/test, and sponsor/test shortcuts were not real logins:
    # they created sessions without user_id, so protected pages did not work.
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT user_id, username, password_hash, user_type, account_locked
                    FROM UserAccount WHERE username = %s
                """, (username,))
                user = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        current_app.logger.exception('Unable to check login against UserAccount')
        return 'Login is temporarily unavailable. Please try again later.', 503

    if not user or not check_password_hash(user.get('password_hash') or '', password):
        return 'Invalid username or password. Please press back and try again.', 401

    if user['account_locked']:
        return 'This account is deactivated or locked. Contact your sponsor or administrator.', 403

    session.clear()
    session['user_id'] = user['user_id']
    session['username'] = user['username']
    session['user_type'] = user['user_type']
    return redirect('/dashboard')


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
@login_required
def dashboard():
    current_user = g.user['username']
    user_type = g.user['user_type']

    if user_type == "Admin":
        return render_template('admin_dashboard.html')

    elif user_type == "Sponsor":
        return render_template(
            'sponsor_dashboard.html',
            report=get_sponsor_point_report(current_user),
            catalog=get_sponsor_catalog(g.user['user_id'])
        )
        
    elif user_type == "Driver":
        points = 0
        history = []
        catalog = []
        redemptions = []
        approved = False
        dropped = False
        rejected = False
        driver_profile_missing = False
        driver_error = None
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
                    WHERE d.user_id = %s
                """, (g.user['user_id'],))

                driver = cur.fetchone()

                if not driver:
                    driver_profile_missing = True
                else:
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

                    cur.execute("""
                        SELECT redemption_id, product_name, points_spent,
                               status, redeemed_at, fulfilled_at
                        FROM RewardRedemption WHERE driver_id = %s
                        ORDER BY redeemed_at DESC, redemption_id DESC
                    """, (driver['driver_id'],))
                    redemptions = cur.fetchall()

                    approved = driver['application_status'] == 'Approved'
                    dropped = driver['application_status'] == 'Dropped'
                    rejected = driver['application_status'] == 'Rejected'

                    if approved:
                        try:
                            cur.execute("""
                                SELECT product_id, product_name, description,
                                       image_url, point_cost, availability AS available
                                FROM ProductCatalog
                                WHERE sponsor_id = %s ORDER BY product_name
                            """, (driver['sponsor_id'],))
                        except Exception as exc:
                            # On an older schema, the RAWG image column may not
                            # be deployed yet. Show the catalog without images.
                            if not (exc.args and exc.args[0] == 1054 and
                                    'image_url' in str(exc)):
                                raise
                            cur.execute("""
                                SELECT product_id, product_name, description,
                                       point_cost, availability AS available
                                FROM ProductCatalog
                                WHERE sponsor_id = %s ORDER BY product_name
                            """, (driver['sponsor_id'],))
                        catalog = cur.fetchall()

        except Exception:
            current_app.logger.exception('Driver dashboard database error')
            driver_error = 'Unable to load driver details from the database. Please try again.'

        finally:
            if conn and conn.open:
                conn.close()

        return render_template(
            'driver_dashboard.html',
            points=points,
            history=history,
            catalog=catalog,
            redemptions=redemptions,
            approved=approved,
            dropped=dropped,
            rejected=rejected,
            driver_profile_missing=driver_profile_missing,
            driver_error=driver_error
        )

    else:
        session.clear()
        return redirect('/')


@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    current_user = g.user['username']
    user_type = g.user['user_type']

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:

            if request.method == 'POST':
                new_email = request.form.get('email', '').strip()
                first_name = request.form.get('first_name', '').strip()
                last_name = request.form.get('last_name', '').strip()

                cur.execute("""
                    UPDATE UserAccount
                    SET email = %s
                    WHERE username = %s
                """, (new_email, current_user))

                if user_type == 'Admin':
                    cur.execute("""
                        UPDATE AdminUser
                        SET first_name = %s,
                            last_name = %s
                        WHERE user_id = (
                            SELECT user_id
                            FROM UserAccount
                            WHERE username = %s
                        )
                    """, (first_name, last_name, current_user))

                elif user_type == 'Sponsor':
                    cur.execute("""
                        UPDATE SponsorUser
                        SET first_name = %s,
                            last_name = %s
                        WHERE user_id = (
                            SELECT user_id
                            FROM UserAccount
                            WHERE username = %s
                        )
                    """, (first_name, last_name, current_user))

                elif user_type == 'Driver':
                    cur.execute("""
                        UPDATE DriverUser
                        SET first_name = %s,
                            last_name = %s
                        WHERE user_id = (
                            SELECT user_id
                            FROM UserAccount
                            WHERE username = %s
                        )
                    """, (first_name, last_name, current_user))

                conn.commit()

            if user_type == 'Admin':
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
                    WHERE u.username = %s
                """, (current_user,))

            elif user_type == 'Sponsor':
                cur.execute("""
                    SELECT
                        u.username,
                        u.email,
                        u.user_type,
                        s.first_name,
                        s.last_name
                    FROM UserAccount u
                    JOIN SponsorUser s
                        ON u.user_id = s.user_id
                    WHERE u.username = %s
                """, (current_user,))

            elif user_type == 'Driver':
                cur.execute("""
                    SELECT
                        u.username,
                        u.email,
                        u.user_type,
                        d.first_name,
                        d.last_name
                    FROM UserAccount u
                    JOIN DriverUser d
                        ON u.user_id = d.user_id
                    WHERE u.username = %s
                """, (current_user,))

            user = cur.fetchone()

    finally:
        conn.close()

    return render_template('profile.html', user=user)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect('/')
