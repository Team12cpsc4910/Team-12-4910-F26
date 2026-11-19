import hashlib
import json
import os
import re
import secrets

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone

from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import pymysql
from flask import Blueprint, abort, flash, g, redirect, request, render_template, session

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


def charge_driver(cur, driver, product, request_key):
    """Record one reward order and deduct its points, inside the caller's transaction.

    The caller must already hold the driver and product row locks and have checked
    the product is available, priced, and affordable. Returns the redemption ID.
    """
    cost = product['point_cost']

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
    """, (driver['driver_id'], driver['sponsor_id'], product['product_id'],
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

    return redemption_id


def redeem_reward(user_id, product_id, request_key, reviewed_cost=None):
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
                WHERE product_id = %s AND sponsor_id = %s
                  AND catalog_enabled = TRUE AND content_rating IN ('G', 'PG')
                FOR UPDATE
            """, (product_id, driver['sponsor_id']))
            product = cur.fetchone()
            if not product:
                raise RedemptionError('This reward is not available from your sponsor.')
            if not product['availability']:
                raise RedemptionError('This reward is currently unavailable.')
            cost = product['point_cost']
            if not isinstance(cost, int) or cost <= 0:
                raise RedemptionError('This reward has an invalid points price.')
            if reviewed_cost is not None and cost != reviewed_cost:
                raise RedemptionError('The points price changed. Please review the new total.')
            if driver['points'] < cost:
                raise RedemptionError(f'You need {cost} points, but only have {driver["points"]}.')

            redemption_id = charge_driver(cur, driver, product, request_key)

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
    reviewed_cost = request.form.get('reviewed_cost', type=int)
    if reviewed_cost is None:
        flash('Review your order before confirming.', 'error')
        return redirect(f'/driver/catalog/{product_id}/checkout')
    try:
        redemption_id = redeem_reward(g.user['user_id'], product_id,
                                      request.form.get('redemption_key', ''), reviewed_cost)
    except RedemptionError as exc:
        flash(str(exc), 'error')
        return redirect('/driver/catalog')
    else:
        # A reward bought from the cart no longer needs to sit in it
        cart = cart_ids()
        if product_id in cart:
            save_cart([i for i in cart if i != product_id])
        flash(f'Reward order #{redemption_id} confirmed. Check My Reward Orders for its status.', 'success')
    return redirect('/dashboard')


# Order status: one of the logged-in driver's own reward orders. Dropped drivers can still
# look up orders they placed before, so this doesn't require an approved application.
@rewards_bp.get('/driver/orders/<int:redemption_id>')
@login_required
def driver_order(redemption_id):
    require_role('Driver')
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.redemption_id, r.product_id, r.product_name, r.points_spent,
                       r.status, r.redeemed_at, r.fulfilled_at,
                       s.sponsor_name
                FROM RewardRedemption r
                JOIN DriverUser d ON r.driver_id = d.driver_id
                LEFT JOIN Sponsor s ON r.sponsor_id = s.sponsor_id
                WHERE r.redemption_id = %s AND d.user_id = %s
            """, (redemption_id, g.user['user_id']))
            order = cur.fetchone()
    finally:
        conn.close()

    # Someone else's order looks the same as one that doesn't exist
    if not order:
        abort(404)
    return render_template('driver_order.html', order=order)


def sponsor_id_for_user(cur, user_id):
    cur.execute('SELECT sponsor_id FROM SponsorUser WHERE user_id = %s', (user_id,))
    sponsor = cur.fetchone()
    return sponsor['sponsor_id'] if sponsor else None

CATEGORIES = ('Games', 'Gift Cards', 'Electronics', 'Entertainment', 'Other')

CONTENT_RATINGS = ('G', 'PG')

CATALOG_MAX_POINTS = 1000000

DEFAULT_CATALOG_RULES = {
    'min_points': 1,
    'max_points': CATALOG_MAX_POINTS,
    'allowed_categories': ','.join(CATEGORIES),
    'allow_g': 1,
    'allow_pg': 1,
}


class CatalogValidationError(ValueError):
    pass


def parse_points(value, *, optional=False):
    value = str(value or '').strip()

    if optional and not value:
        return None

    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= CATALOG_MAX_POINTS:
        raise CatalogValidationError(
            'Points must be whole numbers from 1 to 1,000,000.'
        )

    return int(value)


def validate_rating_category(category, rating):
    if category not in CATEGORIES:
        raise CatalogValidationError('Select a valid reward category.')

    if rating not in CONTENT_RATINGS:
        raise CatalogValidationError(
            'A sponsor must confirm the reward is rated G or PG.'
        )


def get_catalog_rules(cur, sponsor_id):
    cur.execute("""
        SELECT min_points, max_points, allowed_categories, allow_g, allow_pg
        FROM CatalogGenerationRules WHERE sponsor_id = %s
    """, (sponsor_id,))

    return cur.fetchone() or DEFAULT_CATALOG_RULES.copy()


def matches_catalog_rules(points, category, rating, rules):
    if rating not in CONTENT_RATINGS or category not in CATEGORIES:
        return False

    allowed = set(str(rules['allowed_categories']).split(','))

    return (
        rules['min_points'] <= points <= rules['max_points']
        and category in allowed
        and (rating != 'G' or bool(rules['allow_g']))
        and (rating != 'PG' or bool(rules['allow_pg']))
    )

def get_sponsor_catalog(user_id, *, search='', availability='', min_points='',
                        max_points='', sort='name'):
    """Sponsor-only catalog: server-side keyword, stock, price filters and points sort."""

    if availability not in ('', 'in_stock', 'out_of_stock'):
        raise CatalogValidationError('Choose a valid availability filter.')

    sort_clause = {
        'name': 'product_name ASC, product_id ASC',
        'points_asc': 'point_cost ASC, product_id ASC',
        'points_desc': 'point_cost DESC, product_id ASC'
    }.get(sort)

    if not sort_clause:
        raise CatalogValidationError('Choose a valid points sorting option.')

    low = parse_points(min_points, optional=True)
    high = parse_points(max_points, optional=True)

    if low is not None and high is not None and low > high:
        raise CatalogValidationError('Minimum points cannot exceed maximum points.')

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            sponsor_id = sponsor_id_for_user(cur, user_id)

            if sponsor_id is None:
                return []

            query = """
                SELECT product_id, product_name, point_cost, image_url,
                       availability AS available, category, content_rating,
                       auto_managed, catalog_enabled
                FROM ProductCatalog WHERE sponsor_id = %s
            """

            params = [sponsor_id]

            if search:
                query += ' AND (product_name LIKE %s OR description LIKE %s)'
                params.extend([like_pattern(search)] * 2)

            if availability:
                query += ' AND COALESCE(availability, FALSE) = %s'
                params.append(availability == 'in_stock')

            if low is not None:
                query += ' AND point_cost >= %s'
                params.append(low)

            if high is not None:
                query += ' AND point_cost <= %s'
                params.append(high)

            cur.execute(query + ' ORDER BY ' + sort_clause, params)

            return cur.fetchall()

    finally:
        conn.close()

# Longest keyword the driver catalog search accepts
MAX_CATALOG_SEARCH_LENGTH = 100


def like_pattern(keyword):
    """Match keyword anywhere, treating % and _ as plain characters instead of SQL wildcards."""
    escaped = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


# The logged-in driver's catalog access info (sponsor, status, points), shared by the catalog list
# and the product details page so both apply the same access rules.
def get_catalog_driver(cur, user_id):
    cur.execute("""
        SELECT d.sponsor_id,
               d.application_status,
               d.points,
               s.sponsor_name
        FROM DriverUser d
        LEFT JOIN Sponsor s
            ON s.sponsor_id = d.sponsor_id
        WHERE d.user_id = %s
        LIMIT 1
    """, (user_id,))
    return cur.fetchone()


def can_view_catalog(driver):
    return bool(driver and driver['application_status'] == 'Approved' and driver['sponsor_id'] is not None)


# Driver catalog sort choices: value -> (label, ORDER BY clause). Only these clauses reach SQL.
DRIVER_CATALOG_SORTS = {
    'name': ('Name: A to Z', 'product_name'),
    'price_asc': ('Points: low to high', 'point_cost, product_name'),
    'price_desc': ('Points: high to low', 'point_cost DESC, product_name'),
}


@rewards_bp.get('/driver/catalog')
@login_required
def driver_catalog():
    require_role('Driver')

    sort = request.args.get('sort', 'name')
    if sort not in DRIVER_CATALOG_SORTS:
        sort = 'name'

    # Keyword search over the driver's own sponsor's catalog (product name and description)
    search = request.args.get('search', '').strip()[:MAX_CATALOG_SEARCH_LENGTH]
    
    category = request.args.get('category', '').strip()
    
    if category and category not in CATEGORIES:
        abort(400)

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:

            driver = get_catalog_driver(cur, g.user['user_id'])

            catalog = []
            categories = []

            if can_view_catalog(driver):

                cur.execute("""
                    SELECT DISTINCT category FROM ProductCatalog
                    WHERE sponsor_id = %s AND catalog_enabled = TRUE
                      AND content_rating IN ('G', 'PG')
                    ORDER BY category
                """, (driver['sponsor_id'],))
                
                categories = [row['category'] for row in cur.fetchall()]

                query = """
                    SELECT product_id,
                           product_name,
                           description,
                           point_cost,
                           image_url,
                           category,
                           availability AS available
                    FROM ProductCatalog
                    WHERE sponsor_id = %s AND catalog_enabled = TRUE
                      AND content_rating IN ('G', 'PG')
                """
                params = [driver['sponsor_id']]

                if category:
                    query += ' AND category = %s'
                    params.append(category)

                if search:
                    query += " AND (product_name LIKE %s OR description LIKE %s)"
                    params += [like_pattern(search)] * 2

                cur.execute(query + " ORDER BY " + DRIVER_CATALOG_SORTS[sort][1], params)

                catalog = cur.fetchall()

    finally:
        conn.close()

    return render_template(
        'driver_catalog.html',
        driver=driver,
        catalog=catalog,
        search=search,
        selected_category=category,
        categories=categories,
        sort=sort,
        sort_options=DRIVER_CATALOG_SORTS,
        in_cart=set(cart_ids()),
        max_search_length=MAX_CATALOG_SEARCH_LENGTH
)


# Product Details: one product from the driver's own sponsor's catalog, with everything the
# driver needs to decide whether to redeem it.
@rewards_bp.get('/driver/catalog/<int:product_id>')
@login_required
def driver_product(product_id):
    return render_driver_reward(product_id, 'driver_product.html')


@rewards_bp.get('/driver/catalog/<int:product_id>/checkout')
@login_required
def driver_checkout(product_id):
    return render_driver_reward(product_id, 'driver_checkout.html')


# Driver cart: kept in the login session (no database table), so it's per-browser and is cleared
# on logout. Each entry is one product ID (quantity 1, like the Review order page).
MAX_CART_ITEMS = 25


def cart_ids():
    cart = session.get('cart')
    # A cart left in this browser by a different account never carries over
    if not isinstance(cart, dict) or cart.get('user_id') != g.user['user_id']:
        return []
    return [i for i in cart.get('items', []) if isinstance(i, int)]


def cart_count():
    """Number of rewards in the logged-in driver's cart, for the header's cart badge."""
    return len(cart_ids()) if g.get('user') else 0


def save_cart(ids):
    session['cart'] = {'user_id': g.user['user_id'], 'items': ids}


@rewards_bp.post('/driver/cart/<int:product_id>/add')
@login_required
def add_to_cart(product_id):
    require_role('Driver')
    check_csrf()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            driver = get_catalog_driver(cur, g.user['user_id'])
            if not can_view_catalog(driver):
                return redirect('/driver/catalog')
            # Only products from the driver's own sponsor's catalog
            cur.execute("""
                SELECT product_name, availability AS available
                FROM ProductCatalog
                WHERE product_id = %s AND sponsor_id = %s
                  AND catalog_enabled = TRUE AND content_rating IN ('G', 'PG')
            """, (product_id, driver['sponsor_id']))
            product = cur.fetchone()
    finally:
        conn.close()

    if not product:
        abort(404)

    cart = cart_ids()
    name = product['product_name']
    if not product['available']:
        flash(f"{name} is currently unavailable, so it can't be added to your cart.", 'error')
    elif product_id in cart:
        flash(f'{name} is already in your cart.', 'success')
    elif len(cart) >= MAX_CART_ITEMS:
        flash(f'Your cart can hold up to {MAX_CART_ITEMS} rewards. Remove one to add another.', 'error')
    else:
        save_cart(cart + [product_id])
        flash(f'{name} added to your cart.', 'success')

    # The catalog grid sends where to come back to, so the driver can keep shopping.
    # Only same-site catalog paths are followed (never another site).
    next_page = request.form.get('next', '')
    if next_page.startswith('/driver/catalog') and '//' not in next_page and '\\' not in next_page:
        return redirect(next_page)
    return redirect('/driver/cart')


@rewards_bp.post('/driver/cart/<int:product_id>/remove')
@login_required
def remove_from_cart(product_id):
    require_role('Driver')
    check_csrf()
    cart = cart_ids()
    if product_id in cart:
        save_cart([i for i in cart if i != product_id])
        flash('Removed from your cart.', 'success')
    return redirect('/driver/cart')


@rewards_bp.get('/driver/cart')
@login_required
def driver_cart():
    require_role('Driver')
    cart = cart_ids()
    items = []

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            driver = get_catalog_driver(cur, g.user['user_id'])
            if not can_view_catalog(driver):
                return redirect('/driver/catalog')
            if cart:
                placeholders = ', '.join(['%s'] * len(cart))
                cur.execute(f"""
                    SELECT product_id,
                           product_name,
                           point_cost,
                           image_url,
                           category,
                           availability AS available
                    FROM ProductCatalog
                    WHERE sponsor_id = %s AND product_id IN ({placeholders})
                      AND catalog_enabled = TRUE AND content_rating IN ('G', 'PG')
                """, [driver['sponsor_id'], *cart])
                found = {row['product_id']: row for row in cur.fetchall()}
                items = [found[i] for i in cart if i in found]
    finally:
        conn.close()

    # Drop rewards the sponsor removed (or from a sponsor the driver no longer belongs to)
    if len(items) != len(cart):
        save_cart([item['product_id'] for item in items])

    total = sum(item['point_cost'] for item in items if item['available'])
    return render_template('driver_cart.html', driver=driver, items=items, total=total)


def item_request_key(request_key, product_id):
    """Each order row needs its own unique request_key; derive one per reward from the
    checkout form's key so a resubmitted form maps back to the same orders."""
    return hashlib.sha256(f'{request_key}:{product_id}'.encode()).hexdigest()[:32]


def redeem_cart(user_id, product_ids, request_key, reviewed_total):
    """Order every reward in product_ids in ONE transaction; returns the redemption IDs.

    Either every reward is ordered and its points deducted, or nothing changes. Uses
    the same locks and checks as redeem_reward, one order (and point log) per reward.
    """
    if not re.fullmatch(r'[0-9a-f]{32}', request_key or ''):
        raise RedemptionError('Please reload the page and try again.')
    if not product_ids:
        raise RedemptionError('There are no rewards to order.')
    keys = {pid: item_request_key(request_key, pid) for pid in product_ids}

    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT driver_id, sponsor_id, points, application_status
                FROM DriverUser WHERE user_id = %s FOR UPDATE
            """, (user_id,))
            driver = cur.fetchone()
            if not driver or driver['application_status'] != 'Approved':
                raise RedemptionError('Your sponsor must approve you before you can redeem rewards.')

            # A resubmitted checkout form returns the orders it already placed
            placeholders = ', '.join(['%s'] * len(product_ids))
            cur.execute(f"""
                SELECT redemption_id, product_id FROM RewardRedemption
                WHERE driver_id = %s AND request_key IN ({placeholders})
            """, [driver['driver_id'], *keys.values()])
            existing = cur.fetchall()
            if existing:
                if {row['product_id'] for row in existing} != set(product_ids):
                    raise RedemptionError('This order request does not match your cart. Please review it again.')
                conn.rollback()
                return [row['redemption_id'] for row in existing]

            # Only the driver's own sponsor's products; locked in ID order so two
            # checkouts can't deadlock each other
            cur.execute(f"""
                SELECT product_id, product_name, point_cost, availability
                FROM ProductCatalog
                WHERE sponsor_id = %s AND product_id IN ({placeholders})
                ORDER BY product_id FOR UPDATE
            """, [driver['sponsor_id'], *product_ids])
            products = {row['product_id']: row for row in cur.fetchall()}

            for pid in product_ids:
                product = products.get(pid)
                if not product:
                    raise RedemptionError('A reward in your cart is no longer offered by your sponsor.')
                if not product['availability']:
                    raise RedemptionError(f'{product["product_name"]} is currently unavailable. '
                                          'Remove it from your cart or try again later.')
                if not isinstance(product['point_cost'], int) or product['point_cost'] <= 0:
                    raise RedemptionError(f'{product["product_name"]} has an invalid points price.')

            total = sum(products[pid]['point_cost'] for pid in product_ids)
            if total != reviewed_total:
                raise RedemptionError('A points price changed. Please review the new total.')
            if driver['points'] < total:
                raise RedemptionError(f'This order costs {total} points, but you only have {driver["points"]}.')

            redemption_ids = [charge_driver(cur, driver, products[pid], keys[pid])
                              for pid in product_ids]

        conn.commit()
        return redemption_ids
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@rewards_bp.get('/driver/cart/checkout')
@login_required
def driver_cart_checkout():
    require_role('Driver')
    cart = cart_ids()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            driver = get_catalog_driver(cur, g.user['user_id'])
            if not can_view_catalog(driver):
                return redirect('/driver/catalog')
            found = {}
            if cart:
                placeholders = ', '.join(['%s'] * len(cart))
                cur.execute(f"""
                    SELECT product_id,
                           product_name,
                           point_cost,
                           image_url,
                           availability AS available
                    FROM ProductCatalog
                    WHERE sponsor_id = %s AND product_id IN ({placeholders})
                """, [driver['sponsor_id'], *cart])
                found = {row['product_id']: row for row in cur.fetchall()}
    finally:
        conn.close()

    # Only available, priced rewards can be ordered; the rest stay in the cart
    items = [found[i] for i in cart if i in found and found[i]['available'] and found[i]['point_cost'] > 0]
    ordered = {item['product_id'] for item in items}
    skipped = [found[i] for i in cart if i in found and i not in ordered]
    if not items:
        flash('Your cart has no rewards that can be ordered right now.', 'error')
        return redirect('/driver/cart')

    total = sum(item['point_cost'] for item in items)
    return render_template('driver_cart_checkout.html', driver=driver, items=items,
                           skipped=skipped, total=total)


@rewards_bp.post('/driver/cart/checkout')
@login_required
def confirm_cart_checkout():
    require_role('Driver')
    check_csrf()

    # Order exactly the rewards the driver reviewed on the checkout page
    product_ids = []
    for pid in request.form.getlist('product_id', type=int):
        if pid not in product_ids:
            product_ids.append(pid)
    reviewed_total = request.form.get('reviewed_total', type=int)
    if not product_ids or len(product_ids) > MAX_CART_ITEMS or reviewed_total is None:
        flash('Review your order before confirming.', 'error')
        return redirect('/driver/cart/checkout')

    try:
        redemption_ids = redeem_cart(g.user['user_id'], product_ids,
                                     request.form.get('redemption_key', ''), reviewed_total)
    except RedemptionError as exc:
        flash(str(exc), 'error')
        return redirect('/driver/cart')

    save_cart([i for i in cart_ids() if i not in product_ids])
    count = len(redemption_ids)
    flash(f'Order confirmed: {count} reward{"s" if count != 1 else ""} for {reviewed_total} points '
          f'(order{"s" if count != 1 else ""} #{", #".join(str(i) for i in sorted(redemption_ids))}). '
          'Check My Reward Orders for status.', 'success')
    return redirect('/dashboard')


def render_driver_reward(product_id, template):
    require_role('Driver')

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            driver = get_catalog_driver(cur, g.user['user_id'])
            # Not approved yet, dropped, or rejected: the catalog page explains why
            if not can_view_catalog(driver):
                return redirect('/driver/catalog')

            # Same visibility rules as the catalog list, so a hidden reward can't be opened by URL
            cur.execute("""
                SELECT product_id,
                       product_name,
                       description,
                       point_cost,
                       image_url,
                       category,
                       availability AS available
                FROM ProductCatalog
                WHERE product_id = %s AND sponsor_id = %s
                  AND catalog_enabled = TRUE AND content_rating IN ('G', 'PG')
            """, (product_id, driver['sponsor_id']))
            product = cur.fetchone()
    finally:
        conn.close()

    if not product:
        abort(404)
    return render_template(template, driver=driver, item=product, in_cart=product_id in cart_ids())

# GG.deals API configuration

GGDEALS_PRICES_URL = "https://api.gg.deals/v1/prices/by-slug/"

POINTS_PER_USD = Decimal("100")

MAX_REWARD_POINTS = CATALOG_MAX_POINTS


class GameLookupError(ValueError):
    pass


def ggdeals_slug(value):
    """
    Convert a game name or GG.deals URL into a valid game slug.
    """

    value = (value or "").strip()

    if not value or len(value) > 180:
        raise GameLookupError(
            "Enter a game title, slug, or GG.deals game link."
        )

    # Handle full GG.deals URLs
    if value.startswith(("https://", "http://")):

        try:
            parsed = urlsplit(value)

        except ValueError:
            raise GameLookupError(
                "Use a valid GG.deals game link."
            ) from None

        if (
            parsed.scheme != "https"
            or parsed.hostname not in ("gg.deals", "www.gg.deals")
            or parsed.username
            or parsed.password
        ):
            raise GameLookupError(
                "Use an HTTPS game link from gg.deals."
            )

        match = re.fullmatch(
            r"/game/([a-z0-9]+(?:-[a-z0-9]+)*)/?",
            parsed.path
        )

        if not match:
            raise GameLookupError(
                "Paste a valid GG.deals game link."
            )

        return match.group(1)

    # Reject invalid input
    if "/" in value or "?" in value or "#" in value:
        raise GameLookupError(
            "Enter a game name or a valid GG.deals game link."
        )

    # Convert simple game names into slugs
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        value.lower()
    ).strip("-")

    if not slug or len(slug) > 100:
        raise GameLookupError(
            "Please use the full GG.deals game link instead."
        )

    return slug


def fetch_ggdeals_game(slug):
    """
    Retrieve game pricing from the GG.deals API.
    Convert USD pricing into driver reward points.
    """

    if not re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)*",
        slug or ""
    ):
        raise GameLookupError("Invalid game slug.")

    # Retrieve API key from environment
    api_key = os.environ.get(
        "GGDEALS_API_KEY", ""
    ).strip()

    if not api_key:
        raise GameLookupError(
            "GGDEALS_API_KEY is not configured on the backend server."
        )

    # Build GG.deals API request
    url = GGDEALS_PRICES_URL + "?" + urlencode({
        "key": api_key,
        "slugs": slug,
        "region": "us"
    })

    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Team12-Game-Catalog/1.0"
        }
    )

    # Make API request
    try:
        with urlopen(req, timeout=8) as response:
            payload = json.load(response)

    except HTTPError as exc:

        if exc.code == 429:
            raise GameLookupError(
                "GG.deals rate limit reached. Please try again later."
            ) from None

        if exc.code in (401, 403):
            raise GameLookupError(
                "GG.deals rejected the API key."
            ) from None

        raise GameLookupError(
            "GG.deals is currently unavailable."
        ) from None

    except (URLError, TimeoutError, OSError, ValueError):
        raise GameLookupError(
            "GG.deals is currently unavailable."
        ) from None

    # Validate response
    if (
        not isinstance(payload, dict)
        or payload.get("success") is not True
    ):
        raise GameLookupError(
            "GG.deals returned an error."
        )

    data = payload.get("data")

    result = (
        data.get(slug)
        if isinstance(data, dict)
        else None
    )

    if not isinstance(result, dict):
        return None

    # Get game title
    title = str(
        result.get("title") or ""
    ).strip()[:100]

    if not title:
        raise GameLookupError(
            "GG.deals did not return a game title."
        )

    # Get game prices
    prices = result.get("prices") or {}

    if not isinstance(prices, dict):
        raise GameLookupError(
            "GG.deals returned invalid price data."
        )

    # Use current retail price
    raw_price = prices.get("currentRetail")

    price_usd = None
    points = None

    if raw_price is not None:

        if prices.get("currency") != "USD":
            raise GameLookupError(
                "GG.deals did not return prices in USD."
            )

        try:
            amount = Decimal(str(raw_price))

            if not amount.is_finite() or amount < 0:
                raise InvalidOperation()

            if amount > 0:

                # Convert dollars into points
                computed = int(
                    (
                        amount * POINTS_PER_USD
                    ).to_integral_value(
                        rounding=ROUND_HALF_UP
                    )
                )

                if not 1 <= computed <= MAX_REWARD_POINTS:
                    raise GameLookupError(
                        "This game price is outside the supported points range."
                    )

                price_usd = amount
                points = computed

        except (InvalidOperation, ValueError, OverflowError):
            raise GameLookupError(
                "GG.deals returned an invalid game price."
            ) from None

    # Create game URL
    canonical = (
        "https://gg.deals/game/" + slug + "/"
    )

    supplied_url = str(
        result.get("url") or ""
    )

    parsed_link = urlsplit(supplied_url)

    game_url = (
        supplied_url
        if (
            parsed_link.scheme == "https"
            and parsed_link.hostname in (
                "gg.deals",
                "www.gg.deals"
            )
            and parsed_link.path.startswith("/game/")
        )
        else canonical
    )

    return {
        "slug": slug,
        "name": title,
        "url": game_url,
        "price_usd": price_usd,
        "point_cost": points
    }


# Sponsor game lookup page

@rewards_bp.get("/sponsor/games")
@login_required
def search_ggdeals_games():

    require_role("Sponsor")

    term = request.args.get(
        "search", ""
    ).strip()[:180]

    game = None
    error = None

    if term:

        try:
            slug = ggdeals_slug(term)

            game = fetch_ggdeals_game(slug)

            if game is None:
                error = (
                    "No matching GG.deals game was found. "
                    "Try pasting its full GG.deals game page link."
                )

        except GameLookupError as exc:
            error = str(exc)

    return render_template(
        "sponsor_games.html",
        game=game,
        search=term,
        error=error
    )


# Add GG.deals game to sponsor catalog

@rewards_bp.post("/sponsor/catalog/ggdeals")
@login_required
def add_ggdeals_reward():

    require_role("Sponsor")

    check_csrf()

    # Sponsor attestation is required before calling the pricing API.
    category = 'Games'
    rating = request.form.get('content_rating', '').strip()
    auto_managed = request.form.get('auto_managed') == 'on'
    
    try:
        validate_rating_category(category, rating)
    
    except CatalogValidationError as exc:
        flash(str(exc), 'error')
        return redirect('/sponsor/games')
    
    try:
        slug = ggdeals_slug(request.form.get('game_slug', ''))
    
        # Recheck current US retail price. GG.deals price data does NOT certify ratings.
        game = fetch_ggdeals_game(slug)
    
        if not game:
            raise GameLookupError('The selected game could not be found on GG.deals.')
    
        if game['point_cost'] is None:
            raise GameLookupError('No positive US retail price is available for this game.')
    
    except GameLookupError as exc:
        flash(str(exc), 'error')
        return redirect('/sponsor/games')

    # Record when the price was retrieved
    checked_at = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    description = (
        f'GG.deals US retail price: ${game["price_usd"]:.2f} '
        f'({checked_at}); 100 points per USD. '
        f'Sponsor fulfillment required.'
    )

    # Save game to existing database
    conn = get_db_connection()

    try:

        with conn.cursor() as cur:

            sponsor_id = sponsor_id_for_user(
                cur,
                g.user["user_id"]
            )

            if sponsor_id is None:
                abort(403)

            rules = get_catalog_rules(cur, sponsor_id) if auto_managed else None

            enabled = (
                matches_catalog_rules(game['point_cost'], category, rating, rules)
                if auto_managed else True
            )
            
            cur.execute("""
                INSERT INTO ProductCatalog
                    (sponsor_id, product_name, description, point_cost,
                     availability, image_url, category, content_rating,
                     auto_managed, catalog_enabled)
                VALUES (%s, %s, %s, %s, TRUE, NULL, %s, %s, %s, %s)
            """, (
                sponsor_id,
                game['name'],
                description,
                game['point_cost'],
                category,
                rating,
                auto_managed,
                enabled
            ))

        conn.commit()

    except Exception:

        conn.rollback()
        raise

    finally:

        conn.close()

    flash(
        f'{game["name"]} added for '
        f'{game["point_cost"]} points '
        f'(based on ${game["price_usd"]:.2f} USD).',
        "success"
    )

    return redirect("/dashboard")
    
@rewards_bp.post('/sponsor/catalog')
@login_required
def add_catalog_reward():
    require_role('Sponsor')
    check_csrf()

    name = request.form.get('product_name', '').strip()
    category = request.form.get('category', '').strip()
    rating = request.form.get('content_rating', '').strip()
    auto_managed = request.form.get('auto_managed') == 'on'

    try:
        point_cost = parse_points(request.form.get('point_cost', ''))

        if not name or len(name) > 100:
            raise CatalogValidationError('Enter a product name (100 characters max).')

        validate_rating_category(category, rating)

    except CatalogValidationError as exc:
        flash(str(exc), 'error')
        return redirect('/dashboard')

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            sponsor_id = sponsor_id_for_user(cur, g.user['user_id'])

            if sponsor_id is None:
                abort(403)

            rules = get_catalog_rules(cur, sponsor_id) if auto_managed else None

            enabled = (
                matches_catalog_rules(point_cost, category, rating, rules)
                if auto_managed else True
            )

            cur.execute("""
                INSERT INTO ProductCatalog
                    (sponsor_id, product_name, point_cost, availability,
                     category, content_rating, auto_managed, catalog_enabled)
                VALUES (%s, %s, %s, TRUE, %s, %s, %s, %s)
            """, (
                sponsor_id,
                name,
                point_cost,
                category,
                rating,
                auto_managed,
                enabled
            ))

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    flash(
        'Reward added to your catalog.' +
        (' Automatic rules currently hide it from drivers.' if not enabled else ''),
        'success'
    )

    return redirect('/dashboard')

@rewards_bp.post('/sponsor/catalog/<int:product_id>/settings')
@login_required
def update_catalog_reward_settings(product_id):
    """Classify legacy rewards and choose whether generation rules manage them."""

    require_role('Sponsor')
    check_csrf()

    category = request.form.get('category', '').strip()
    rating = request.form.get('content_rating', '').strip()
    auto_managed = request.form.get('auto_managed') == 'on'

    try:
        # Sponsors may withdraw a prior G/PG attestation.
        if rating == 'Unrated':
            if category not in CATEGORIES:
                raise CatalogValidationError('Select a valid reward category.')
        else:
            validate_rating_category(category, rating)

    except CatalogValidationError as exc:
        flash(str(exc), 'error')
        return redirect('/dashboard')

    conn = get_db_connection()

    try:
        conn.begin()

        with conn.cursor() as cur:
            sponsor_id = sponsor_id_for_user(cur, g.user['user_id'])

            if sponsor_id is None:
                abort(403)

            cur.execute("""
                SELECT point_cost FROM ProductCatalog
                WHERE product_id = %s AND sponsor_id = %s FOR UPDATE
            """, (product_id, sponsor_id))

            product = cur.fetchone()

            if product is None:
                abort(404)

            rules = get_catalog_rules(cur, sponsor_id) if auto_managed else None

            enabled = (
                matches_catalog_rules(product['point_cost'], category, rating, rules)
                if auto_managed else True
            )

            cur.execute("""
                UPDATE ProductCatalog SET category = %s, content_rating = %s,
                    auto_managed = %s, catalog_enabled = %s
                WHERE product_id = %s AND sponsor_id = %s
            """, (
                category,
                rating,
                auto_managed,
                enabled,
                product_id,
                sponsor_id
            ))

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    flash('Reward settings saved.', 'success')
    return redirect('/dashboard')


@rewards_bp.route('/sponsor/catalog/rules', methods=['GET', 'POST'])
@login_required
def sponsor_catalog_generation_rules():
    """Configure sponsor-specific rules and regenerate all auto-managed offers."""

    require_role('Sponsor')

    if request.method == 'POST':
        check_csrf()

        selected = set(request.form.getlist('allowed_categories'))
        allow_g = request.form.get('allow_g') == 'on'
        allow_pg = request.form.get('allow_pg') == 'on'

        try:
            low = parse_points(request.form.get('min_points', ''))
            high = parse_points(request.form.get('max_points', ''))

            if low > high:
                raise CatalogValidationError('Minimum points cannot exceed maximum points.')

            if not selected or not selected.issubset(CATEGORIES):
                raise CatalogValidationError('Choose at least one valid category.')

            if not allow_g and not allow_pg:
                raise CatalogValidationError('Allow at least one content rating (G or PG).')

        except CatalogValidationError as exc:
            flash(str(exc), 'error')
            return redirect('/sponsor/catalog/rules')

        allowed = ','.join(cat for cat in CATEGORIES if cat in selected)

        conn = get_db_connection()

        try:
            conn.begin()

            with conn.cursor() as cur:
                sponsor_id = sponsor_id_for_user(cur, g.user['user_id'])

                if sponsor_id is None:
                    abort(403)

                cur.execute("""
                    INSERT INTO CatalogGenerationRules
                        (sponsor_id, min_points, max_points, allowed_categories,
                         allow_g, allow_pg)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        min_points = VALUES(min_points),
                        max_points = VALUES(max_points),
                        allowed_categories = VALUES(allowed_categories),
                        allow_g = VALUES(allow_g),
                        allow_pg = VALUES(allow_pg)
                """, (
                    sponsor_id,
                    low,
                    high,
                    allowed,
                    allow_g,
                    allow_pg
                ))

                # Availability is stock status and is NEVER overwritten here.
                # Only automatically managed products change rule-based visibility.
                cur.execute("""
                    UPDATE ProductCatalog
                    SET catalog_enabled = CASE
                        WHEN point_cost BETWEEN %s AND %s
                          AND FIND_IN_SET(category, %s) > 0
                          AND ((content_rating = 'G' AND %s)
                               OR (content_rating = 'PG' AND %s))
                        THEN TRUE ELSE FALSE END
                    WHERE sponsor_id = %s AND auto_managed = TRUE
                """, (
                    low,
                    high,
                    allowed,
                    allow_g,
                    allow_pg,
                    sponsor_id
                ))

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

        flash(
            'Catalog rules saved. Automatically managed rewards were regenerated.',
            'success'
        )

        return redirect('/sponsor/catalog/rules')

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            sponsor_id = sponsor_id_for_user(cur, g.user['user_id'])

            if sponsor_id is None:
                abort(403)

            rules = get_catalog_rules(cur, sponsor_id)

    finally:
        conn.close()

    return render_template(
        'sponsor_catalog_rules.html',
        rules=rules,
        categories=CATEGORIES,
        selected_categories=set(rules['allowed_categories'].split(','))
    )


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


# Remove Reward: retires a product from the sponsor's catalog. A product drivers have already
# redeemed can't be deleted (RewardRedemption keeps a foreign key to it), so it stays and the
# sponsor is told to disable it instead, which keeps drivers' order history intact.
@rewards_bp.post('/sponsor/catalog/<int:product_id>/remove')
@login_required
def remove_catalog_reward(product_id):
    require_role('Sponsor')
    check_csrf()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sponsor_id = sponsor_id_for_user(cur, g.user['user_id'])
            if sponsor_id is None:
                abort(403)
            # A removal can only affect this sponsor's own rewards.
            cur.execute("""
                SELECT product_name FROM ProductCatalog
                WHERE product_id = %s AND sponsor_id = %s
                FOR UPDATE
            """, (product_id, sponsor_id))
            product = cur.fetchone()
            if not product:
                abort(404)

            cur.execute('SELECT COUNT(*) AS orders FROM RewardRedemption WHERE product_id = %s', (product_id,))
            orders = cur.fetchone()['orders']
            if not orders:
                cur.execute('DELETE FROM ProductCatalog WHERE product_id = %s AND sponsor_id = %s',
                            (product_id, sponsor_id))
        conn.commit()
    except pymysql.IntegrityError as exc:
        conn.rollback()
        # A driver redeemed it between the check and the delete
        if exc.args[0] != 1451:
            raise
        orders = 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if orders:
        flash(f"{product['product_name']} has reward orders, so it can't be removed. "
              'Disable it instead to stop new redemptions.', 'error')
    else:
        flash(f"{product['product_name']} was removed from your catalog.", 'success')
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
