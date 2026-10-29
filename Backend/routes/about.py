from flask import Blueprint, render_template

from database import get_db_connection

about_bp = Blueprint("about", __name__)


# About page feature: loads team #, sprint/version #, release date, product name,
# and product description from the AboutPage table instead of hardcoding them in the HTML.
def fetch_about_info():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Newest row = current sprint, so updating each sprint is just an INSERT
            cur.execute(
                "SELECT team_number, version_number, release_date, "
                "product_name, product_description "
                "FROM AboutPage ORDER BY about_id DESC LIMIT 1"
            )
            return cur.fetchone()
    finally:
        conn.close()


# Renders the About page with the database values; if the database can't be reached,
# the page shows an "unavailable" message instead of crashing.
@about_bp.route("/about")
def get_about():
    try:
        about = fetch_about_info()
    except Exception:
        about = None
    return render_template("about.html", about=about)
