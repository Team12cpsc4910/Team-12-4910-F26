"""About-page data for Flask pages and the separately hosted Amplify frontend."""

from flask import Blueprint, current_app, jsonify, render_template

from database import get_db_connection

about_bp = Blueprint('about', __name__)

# Visible while the AboutPage table is unavailable. These are informational,
# not a guessed release date or sprint number.
DEFAULT_ABOUT = {
    'team_number': 12,
    'version_number': 'Not available',
    'release_date': 'Not available',
    'product_name': 'Good Driver Incentive Program',
    'product_description': 'Drivers can earn points and redeem sponsor rewards.',
}


def fetch_about_info():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT team_number, version_number, release_date,
                       product_name, product_description
                FROM AboutPage
                ORDER BY about_id DESC LIMIT 1
            ''')
            return cur.fetchone()
    finally:
        conn.close()


@about_bp.get('/api/about')
def about_data():
    """JSON is consumed by about.html when hosted statically on Amplify."""
    try:
        row = fetch_about_info()
    except Exception:
        current_app.logger.exception('AboutPage lookup failed')
        row = None

    if not row:
        return jsonify({**DEFAULT_ABOUT, 'available': False})

    data = {key: row.get(key) for key in DEFAULT_ABOUT}
    data['release_date'] = (data['release_date'].isoformat()
                            if hasattr(data['release_date'], 'isoformat')
                            else str(data['release_date'] or 'Not available'))
    data['available'] = True
    return jsonify(data)


@about_bp.get('/about')
def get_about():
    return render_template('about.html')
