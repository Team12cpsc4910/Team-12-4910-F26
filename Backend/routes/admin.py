from flask import Blueprint, render_template, g, abort

from database import get_db_connection
from security import login_required

admin_bp = Blueprint('admin', __name__)

def require_admin():
    if g.user['user_type'].lower() != 'admin':
        abort(403)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT admin_user_id FROM AdminUser WHERE user_id = %s', (g.user['user_id'],))
            if not cur.fetchone():
                abort(403)
    finally:
        conn.close()


def get_sponsor_users():
    sponsor_users = []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT su.sponsor_user_id, su.first_name, su.last_name,
                       u.username, u.email, u.account_locked, s.sponsor_name
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                JOIN Sponsor s ON su.sponsor_id = s.sponsor_id
                ORDER BY s.sponsor_name, su.last_name, su.first_name
            """)
            sponsor_users = cur.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if conn and conn.open:
            conn.close()
    return sponsor_users


def get_sponsor_user(sponsor_user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT su.sponsor_user_id, su.first_name, su.last_name,
                       u.username, u.email, u.account_locked, u.failed_logins,
                       s.sponsor_id, s.sponsor_name
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                JOIN Sponsor s ON su.sponsor_id = s.sponsor_id
                WHERE su.sponsor_user_id = %s
            """, (sponsor_user_id,))
            return cur.fetchone()
    finally:
        conn.close()


@admin_bp.route('/admin/sponsor-users')
@login_required
def admin_sponsor_users():
    require_admin()
    return render_template('admin_sponsor_users.html', sponsor_users=get_sponsor_users())


@admin_bp.route('/admin/sponsor-users/<int:sponsor_user_id>')
@login_required
def admin_sponsor_user(sponsor_user_id):
    require_admin()
    sponsor_user = get_sponsor_user(sponsor_user_id)
    if not sponsor_user:
        abort(404)
    return render_template('admin_sponsor_user.html', sponsor_user=sponsor_user)