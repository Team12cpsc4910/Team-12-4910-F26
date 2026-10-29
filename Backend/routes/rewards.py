"""Reward catalog, point redemption and sponsor fulfillment.

All money-like operations use MySQL row locks and a single database transaction.
The logged-in identity comes from security.load_user, not untrusted form fields.
"""

import json
import os
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Blueprint, abort, flash, g, redirect, request, render_template

from database import get_db_connection
from security import check_csrf, login_required

rewards_bp = Blueprint('rewards', __name__)


class RedemptionError(ValueError):
    pass


def redemption_key():
    """Generate a new request key for each redemption form."""
    return secrets.token_hex(16)


def require_role(role):
    if g.user['user_type'] != role:
        abort(403)


def redeem_reward(user_id, product_id, request_key):
    """Charge the current driver for ONE sponsor reward; returns the redemption ID.

    The driver row is locked before checking its balance; two concurrent requests
    cannot both spend the same points. The purchase and point-log are committed
    together, or all changes are rolled back together.
    """
    if not re.fullmatch(r'[0-9a-f]{32}', request_key or ''):
        raise RedemptionError('Please reload the page and try again.')

    conn = get_db_connection()
    try:
        # Start a database transaction
        conn.begin()
    
        with conn.cursor() as cur:
            cur.execute("""
                SELECT driver_id, sponsor_id, points, application_status
                FROM DriverUser WHERE user_id = %s FOR UPDATE
            """, (user_id,))
            driver = cur.fetchone()
            if not driver or driver['application_status'] != 'Approved':
                raise RedemptionError('Your sponsor must approve you before you can redeem rewards.')

            # Browser retries of the SAME form return the first order, without
            # charging points a second time. The driver lock serializes retries.
            cur.execute("""
                SELECT redemption_id, product_id FROM RewardRedemption
                WHERE driver_id = %s AND request_key = %s
            """, (driver['driver_id'], request_key))
            existing = cur.fetchone()
            if existing:
                if existing['product_id'] != product_id:
                    raise RedemptionError('This redemption request belongs to another reward.')
            
                # Release locks because this purchase already exists
                conn.rollback()
            
                return existing['redemption_id']

            # Match the product against the sponsor recorded for THIS driver,
            # never a sponsor ID supplied by the browser.
            cur.execute("""
                SELECT product_id, product_name, point_cost, availability
                FROM ProductCatalog
                WHERE product_id = %s AND sponsor_id = %s FOR UPDATE
            """, (product_id, driver['sponsor_id']))
            product = cur.fetchone()
            if not product:
                raise RedemptionError('This reward is not available from your sponsor.')
            if not product['availability']:
                raise RedemptionError('This reward is currently unavailable.')
            cost = product['point_cost']
            if not isinstance(cost, int) or cost <= 0:
                raise RedemptionError('This reward has an invalid points price.')
            if driver['points'] < cost:
                raise RedemptionError(f'You need {cost} points, but only have {driver["points"]}.')

            # Find a log row produced by the optional existing log_points_change
            # trigger. That trigger logs a generic reason; update it if present.
            cur.execute("""
                SELECT COALESCE(MAX(point_log_id), 0) AS last_id
                FROM PointChangeLog WHERE driver_id = %s
            """, (driver['driver_id'],))
            previous_log_id = cur.fetchone()['last_id']

            # Save name/cost snapshots so a later product-price change does not
            # rewrite the history of what the driver spent.
            cur.execute("""
                INSERT INTO RewardRedemption
                    (driver_id, sponsor_id, product_id, product_name, points_spent, request_key)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (driver['driver_id'], driver['sponsor_id'], product_id,
                  product['product_name'], cost, request_key))
            redemption_id = cur.lastrowid

            cur.execute("""
                UPDATE DriverUser SET points = points - %s
                WHERE driver_id = %s AND application_status = 'Approved' AND points >= %s
            """, (cost, driver['driver_id'], cost))
            if cur.rowcount != 1:
                raise RedemptionError('Your balance changed. Please try again.')

            reason = f'Reward #{redemption_id}: {product["product_name"]}'
            cur.execute("""
                SELECT point_log_id FROM PointChangeLog
                WHERE driver_id = %s AND point_log_id > %s
                ORDER BY point_log_id DESC LIMIT 1
            """, (driver['driver_id'], previous_log_id))
            triggered_log = cur.fetchone()
            if triggered_log:
                cur.execute("""
                    UPDATE PointChangeLog SET reason = %s
                    WHERE point_log_id = %s
                """, (reason, triggered_log['point_log_id']))
            else:
                cur.execute("""
                    INSERT INTO PointChangeLog (driver_id, points_change, reason)
                    VALUES (%s, %s, %s)
                """, (driver['driver_id'], -cost, reason))

        conn.commit()
        return redemption_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@rewards_bp.post('/rewards/<int:product_id>/redeem')
@login_required
def redeem_product(product_id):
    require_role('Driver')
    check_csrf()
    try:
        redemption_id = redeem_reward(g.user['user_id'], product_id,
                                      request.form.get('redemption_key', ''))
    except RedemptionError as exc:
        flash(str(exc), 'error')
    else:
        flash(f'Reward order #{redemption_id} confirmed. Check My Reward Orders for its status.', 'success')
    return redirect('/dashboard')


def sponsor_id_for_user(cur, user_id):
    cur.execute('SELECT sponsor_id FROM SponsorUser WHERE user_id = %s', (user_id,))
    sponsor = cur.fetchone()
    return sponsor['sponsor_id'] if sponsor else None


def get_sponsor_catalog(user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sponsor_id = sponsor_id_for_user(cur, user_id)
            if sponsor_id is None:
                return []

            cur.execute("""
                SELECT product_id, product_name, point_cost, image_url,
                       availability AS available
                FROM ProductCatalog
                WHERE sponsor_id = %s
                ORDER BY product_name
            """, (sponsor_id,))

            return cur.fetchall()
    finally:
        conn.close()

def fetch_rawg(path, params=None):
    """Retrieve game metadata server-side so the RAWG key stays private."""

    api_key = os.environ.get('RAWG_API_KEY', '').strip()

    if not api_key:
        raise RuntimeError('RAWG_API_KEY is not configured on the server.')

    query = urlencode({'key': api_key, **(params or {})})

    url = f'https://api.rawg.io/api/{path}?{query}'

    req = Request(url, headers={
        'Accept': 'application/json',
        'User-Agent': 'Team12-Game-Catalog/1.0',
    })

    try:
        with urlopen(req, timeout=8) as response:
            return json.load(response)

    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(
            'RAWG is unavailable. Check your API key or try again later.'
        ) from exc

@rewards_bp.get('/sponsor/games')
@login_required
def search_rawg_games():

    require_role('Sponsor')

    term = request.args.get('search', '').strip()[:80]

    games = []
    error = None

    if term:
        if len(term) < 2:
            error = 'Enter at least two characters to search.'
        else:
            try:
                data = fetch_rawg('games', {
                    'search': term,
                    'page_size': 10
                })

                games = data.get('results', [])

            except RuntimeError as exc:
                error = str(exc)

    return render_template(
        'sponsor_games.html',
        games=games,
        search=term,
        error=error
    )

@rewards_bp.post('/sponsor/catalog/rawg')
@login_required
def add_rawg_reward():

    require_role('Sponsor')
    check_csrf()

    raw_id = request.form.get('game_id', '').strip()
    raw_cost = request.form.get('point_cost', '').strip()

    if (not raw_id.isascii() or not raw_id.isdigit() or len(raw_id) > 12 or
            int(raw_id) < 1 or not raw_cost.isascii() or not raw_cost.isdigit() or
            not 1 <= int(raw_cost) <= 1000000):

        flash(
            'Choose a valid game and point cost between 1 and 1,000,000.',
            'error'
        )

        return redirect('/sponsor/games')

    try:
        game = fetch_rawg(f'games/{raw_id}')

    except RuntimeError as exc:
        flash(str(exc), 'error')
        return redirect('/sponsor/games')

    name = str(game.get('name') or '').strip()[:100]

    if not name:
        flash('RAWG did not return a game name.', 'error')
        return redirect('/sponsor/games')

    description = str(game.get('description_raw') or '')[:255]

    image_url = str(game.get('background_image') or '')[:500]

    if not image_url.startswith('https://'):
        image_url = None

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:

            sponsor_id = sponsor_id_for_user(
                cur,
                g.user['user_id']
            )

            if sponsor_id is None:
                abort(403)

            cur.execute("""
                INSERT INTO ProductCatalog
                    (sponsor_id, product_name, description,
                     point_cost, availability, image_url)
                VALUES (%s, %s, %s, %s, TRUE, %s)
            """, (
                sponsor_id,
                name,
                description,
                int(raw_cost),
                image_url
            ))

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    flash(f'{name} added to your reward catalog.', 'success')

    return redirect('/dashboard')

@rewards_bp.post('/sponsor/catalog')
@login_required
def add_catalog_reward():
    require_role('Sponsor')
    check_csrf()
    name = request.form.get('product_name', '').strip()
    raw_cost = request.form.get('point_cost', '').strip()
    if not name or len(name) > 100 or not raw_cost.isdigit() or not 1 <= int(raw_cost) <= 1000000:
        flash('Enter a product name (100 characters max) and a point cost from 1 to 1,000,000.', 'error')
        return redirect('/dashboard')

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sponsor_id = sponsor_id_for_user(cur, g.user['user_id'])
            if sponsor_id is None:
                abort(403)
            cur.execute("""
                INSERT INTO ProductCatalog (sponsor_id, product_name, point_cost, availability)
                VALUES (%s, %s, %s, TRUE)
            """, (sponsor_id, name, int(raw_cost)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    flash('Reward added to your catalog.', 'success')
    return redirect('/dashboard')


@rewards_bp.post('/sponsor/catalog/<int:product_id>/availability')
@login_required
def toggle_catalog_reward(product_id):
    require_role('Sponsor')
    check_csrf()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sponsor_id = sponsor_id_for_user(cur, g.user['user_id'])
            if sponsor_id is None:
                abort(403)
            # A toggle can only affect this sponsor's own rewards.
            cur.execute("""
                UPDATE ProductCatalog SET availability = NOT COALESCE(availability, FALSE)
                WHERE product_id = %s AND sponsor_id = %s
            """, (product_id, sponsor_id))
            if cur.rowcount != 1:
                abort(404)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return redirect('/dashboard')


@rewards_bp.get('/sponsor/redemptions')
@login_required
def sponsor_redemptions():
    require_role('Sponsor')
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sponsor_id = sponsor_id_for_user(cur, g.user['user_id'])
            if sponsor_id is None:
                abort(403)
            cur.execute("""
                SELECT r.redemption_id, r.product_name, r.points_spent,
                       r.status, r.redeemed_at, r.fulfilled_at,
                       d.first_name, d.last_name
                FROM RewardRedemption r
                JOIN DriverUser d ON r.driver_id = d.driver_id
                WHERE r.sponsor_id = %s
                ORDER BY r.redeemed_at DESC, r.redemption_id DESC
            """, (sponsor_id,))
            orders = cur.fetchall()
    finally:
        conn.close()
    return render_template('sponsor_redemptions.html', orders=orders)


@rewards_bp.post('/sponsor/redemptions/<int:redemption_id>/fulfill')
@login_required
def fulfill_redemption(redemption_id):
    require_role('Sponsor')
    check_csrf()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sponsor_id = sponsor_id_for_user(cur, g.user['user_id'])
            if sponsor_id is None:
                abort(403)
            # The sponsor can only fulfill their own outstanding orders.
            cur.execute("""
                UPDATE RewardRedemption
                SET status = 'Fulfilled', fulfilled_at = CURRENT_TIMESTAMP
                WHERE redemption_id = %s AND sponsor_id = %s AND status = 'Requested'
            """, (redemption_id, sponsor_id))
            if cur.rowcount != 1:
                abort(404)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    flash(f'Reward #{redemption_id} marked fulfilled.', 'success')
    return redirect('/sponsor/redemptions')
