import csv
import io

from flask import Blueprint, request, render_template, session, redirect, g, Response
from markupsafe import escape
from werkzeug.security import check_password_hash
from database import get_db_connection
from security import login_required, check_csrf
from routes.rewards import get_sponsor_catalog, require_role, CatalogValidationError
from routes.admin import change_own_password, AdminError, csv_safe, get_admin_overview

auth_bp = Blueprint('auth', __name__)

# index.html stays plain HTML because Amplify serves it as-is (no Jinja), so a failed login
# re-shows the page with the error placed where this HTML comment sits in the login card.
LOGIN_ERROR_MARKER = '<!-- login-error -->'


def login_page_with_error(message, username=''):
    page = render_template('index.html')
    page = page.replace(LOGIN_ERROR_MARKER,
                        f'<p class="auth-error" role="alert">{escape(message)}</p>', 1)
    # Keep what they typed so only the password needs re-entering
    if username:
        page = page.replace('name="username"',
                            f'name="username" value="{escape(username[:50])}"', 1)
    return page


@auth_bp.route('/login', methods=['POST'])
def login():
    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''

    if not username or not password:
        return login_page_with_error('Enter your username and password.', username)

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
                SELECT user_id, username, password_hash, user_type, account_locked
                FROM UserAccount
                WHERE username = %s
            """, (username,))

            user = cur.fetchone()

    finally:
        conn.close()

    if user and check_password_hash(user['password_hash'], password):
        # Checked after the password so a wrong guess can't reveal which accounts are deactivated
        if user['account_locked']:
            return login_page_with_error('This account has been deactivated or locked. '
                                         'Please contact your sponsor or an administrator.', username)

        session['user_id'] = user['user_id']
        session['username'] = user['username']
        session['user_type'] = user['user_type']

        return redirect('/dashboard')

    return login_page_with_error('Invalid username or password. Please try again.', username)


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

@auth_bp.route('/sponsor/point-report/download')
@login_required
def sponsor_point_report_csv():
    require_role('Sponsor')
    rows = [[f"{r['first_name']} {r['last_name']}", r['datetime'], r['points_change'], r['changed_by'], r['reason']]
            for r in get_sponsor_point_report(g.user['username'])]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Driver', 'Date', 'Change', 'Changed By', 'Reason'])
    writer.writerows([[csv_safe(value) for value in row] for row in rows])
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=driver-point-report.csv'})



@auth_bp.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect('/')

    current_user = session['username']
    user_type = session.get('user_type')

    if user_type == "Admin":
        return render_template('admin_dashboard.html', overview=get_admin_overview())

    elif user_type == "Sponsor":
        catalog_filters = {key: request.args.get(key, '') for key in
                           ('search', 'availability', 'min_points', 'max_points', 'sort')}
        try:
            catalog = get_sponsor_catalog(
                session.get('user_id'),
                search=catalog_filters['search'].strip()[:100],
                availability=catalog_filters['availability'],
                min_points=catalog_filters['min_points'],
                max_points=catalog_filters['max_points'],
                sort=catalog_filters['sort'] or 'name'
            )
            catalog_error = None
        except CatalogValidationError as exc:
            catalog = []
            catalog_error = str(exc)

        return render_template(
            'sponsor_dashboard.html',
            report=get_sponsor_point_report(current_user),
            catalog=catalog,
            catalog_filters=catalog_filters,
            catalog_error=catalog_error,
        )
        
    elif user_type == "Driver":
        points = 0
        history = []
        catalog = []
        redemptions = []
        approved = False
        dropped = False
        dropped_by = None
        rejected = False
        driver_name = None
        sponsor_name = None
        status = None
        conn = None

        try:
            conn = get_db_connection()

            with conn.cursor() as cur:
                cur.execute("""
                    SELECT d.driver_id,
                           d.first_name,
                           d.points,
                           d.sponsor_id,
                           d.application_status,
                           s.sponsor_name
                    FROM DriverUser d
                    LEFT JOIN Sponsor s
                        ON d.sponsor_id = s.sponsor_id
                    WHERE d.user_id = %s
                """, (session.get('user_id', 0),))

                driver = cur.fetchone()

                if driver:
                    points = driver['points']
                    driver_name = driver['first_name']
                    sponsor_name = driver['sponsor_name']
                    status = driver['application_status']

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
                    # A dropped driver keeps the sponsor_id of the sponsor that dropped them
                    dropped_by = driver['sponsor_name'] if dropped else None
                    rejected = driver['application_status'] == 'Rejected'

                    if approved:
                        cur.execute("""
                            SELECT product_id,
                                   product_name,
                                   description,
                                   point_cost,
                                   Availability AS available
                            FROM ProductCatalog
                            WHERE sponsor_id = %s AND catalog_enabled = TRUE
                              AND content_rating IN ('G', 'PG')
                            ORDER BY product_name
                        """, (driver['sponsor_id'],))

                        catalog = cur.fetchall()

        except Exception as e:
            print(f"Database error: {e}")

        finally:
            if conn and conn.open:
                conn.close()

        # Dashboard highlights: the priciest rewards the driver can redeem right now, or
        # (when none are affordable) the cheapest one to work toward.
        redeemable = [item for item in catalog if item['available'] and 0 < item['point_cost']]
        affordable = sorted((item for item in redeemable if item['point_cost'] <= points),
                            key=lambda item: item['point_cost'], reverse=True)[:4]
        next_reward = None if affordable else min(redeemable, key=lambda item: item['point_cost'], default=None)

        return render_template(
            'driver_dashboard.html',
            driver_name=driver_name,
            sponsor_name=sponsor_name,
            status=status,
            affordable=affordable,
            next_reward=next_reward,
            points=points,
            history=history,
            catalog=catalog,
            redemptions=redemptions,
            approved=approved,
            dropped=dropped,
            dropped_by=dropped_by,
            rejected=rejected
        )

    else:
        session.clear()
        return redirect('/')


@auth_bp.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'username' not in session:
        return redirect('/')

    current_user = session['username']
    user_type = session.get('user_type')

    error = None
    submitted = {}
    if request.method == 'POST':
        check_csrf()
        submitted = {k: request.form.get(k, '').strip() for k in ('first_name', 'last_name', 'email')}
        error = profile_error(**submitted)

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:

            # Emails are unique, so say so instead of failing on the UPDATE
            if request.method == 'POST' and not error:
                cur.execute('SELECT 1 FROM UserAccount WHERE email = %s AND username <> %s',
                            (submitted['email'], current_user))
                if cur.fetchone():
                    error = 'That email address is already used by another account.'

            if request.method == 'POST' and not error:
                new_email = submitted['email']
                first_name = submitted['first_name']
                last_name = submitted['last_name']

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
                # Redirect so a refresh doesn't resubmit the form
                return redirect('/profile?saved=1')

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

    # Keep what the user typed so they only have to fix the problem
    if error and user:
        user = dict(user, **submitted)

    return render_template('profile.html', user=user, error=error,
                           saved=request.args.get('saved') == '1',
                           password_changed=request.args.get('password_changed') == '1'), (400 if error else 200)


PROFILE_NAME_MAX = 50
PROFILE_EMAIL_MAX = 100


def profile_error(first_name, last_name, email):
    """Message for the first problem with the submitted profile fields, or None."""
    if not first_name or not last_name:
        return 'Please enter your first and last name.'
    if len(first_name) > PROFILE_NAME_MAX or len(last_name) > PROFILE_NAME_MAX:
        return f'Names must be {PROFILE_NAME_MAX} characters or fewer.'
    if not email or '@' not in email or ' ' in email or len(email) > PROFILE_EMAIL_MAX:
        return 'Please enter a valid email address.'
    return None

# Change Password: any real (database) account changes its own password by confirming the current
# one. Reuses the admin page's change_own_password, so complexity rules and logging stay in one place.
@auth_bp.route('/password', methods=['GET', 'POST'])
@login_required
def change_password():
    # An admin assuming a driver is acting as that driver, so they can't change the driver's password
    if session.get('impersonator'):
        return redirect('/profile')

    error = None
    if request.method == 'POST':
        check_csrf()
        try:
            change_own_password(g.user['user_id'],
                                request.form.get('current_password', ''),
                                request.form.get('new_password', ''),
                                request.form.get('confirm_password', ''))
            return redirect('/profile?password_changed=1')
        except AdminError as exc:
            error = str(exc)

    return render_template('password_form.html', error=error), (400 if error else 200)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect('/')
