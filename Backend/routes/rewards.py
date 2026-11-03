import json
import os
import re
import secrets

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone

from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
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

@rewards_bp.get('/driver/catalog')
@login_required
def driver_catalog():
    require_role('Driver')

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:

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
            """, (g.user['user_id'],))

            driver = cur.fetchone()

            catalog = []

            if (
                driver
                and driver['application_status'] == 'Approved'
                and driver['sponsor_id'] is not None
            ):

                cur.execute("""
                    SELECT product_id,
                           product_name,
                           description,
                           point_cost,
                           image_url,
                           availability AS available
                    FROM ProductCatalog
                    WHERE sponsor_id = %s
                    ORDER BY product_name
                """, (driver['sponsor_id'],))

                catalog = cur.fetchall()

    finally:
        conn.close()

    return render_template(
        'driver_catalog.html',
        driver=driver,
        catalog=catalog
    )

# GG.deals API configuration

GGDEALS_PRICES_URL = "https://api.gg.deals/v1/prices/by-slug/"

POINTS_PER_USD = Decimal("100")

MAX_REWARD_POINTS = 1000000


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

    try:
        slug = ggdeals_slug(
            request.form.get("game_slug", "")
        )

        # Fetch the current price again
        game = fetch_ggdeals_game(slug)

        if not game:
            raise GameLookupError(
                "The selected game could not be found on GG.deals."
            )

        if game["point_cost"] is None:
            raise GameLookupError(
                "No positive US retail price is available for this game."
            )

    except GameLookupError as exc:

        flash(str(exc), "error")

        return redirect("/sponsor/games")

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

            cur.execute("""
                INSERT INTO ProductCatalog
                    (
                        sponsor_id,
                        product_name,
                        description,
                        point_cost,
                        availability,
                        image_url
                    )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    TRUE,
                    NULL
                )
            """, (
                sponsor_id,
                game["name"],
                description,
                game["point_cost"]
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
