"""Catalog filters, sponsor generation rules, and content rating gate."""
import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from routes import rewards


def test_rules_honor_price_category_and_content_rating():
    rules = {
        'min_points': 200, 'max_points': 1000,
        'allowed_categories': 'Games,Gift Cards',
        'allow_g': 1, 'allow_pg': 0,
    }
    assert rewards.matches_catalog_rules(250, 'Games', 'G', rules)
    for cost, category, rating in [
        (199, 'Games', 'G'), (1001, 'Games', 'G'),
        (250, 'Electronics', 'G'), (250, 'Games', 'PG'),
        (250, 'Games', 'R'), (250, 'Games', 'Unrated'),
    ]:
        assert not rewards.matches_catalog_rules(cost, category, rating, rules)


@pytest.mark.parametrize('category,rating', [
    ('Games', 'R'), ('Games', ''), ('Games', 'Unrated'),
    ('Invalid', 'G'), ('Other', 'E'),
])
def test_unknown_or_non_g_pg_rating_cannot_be_added(category, rating):
    with pytest.raises(rewards.CatalogValidationError):
        rewards.validate_rating_category(category, rating)


@pytest.mark.parametrize('value', ['', '0', '-1', '1.5', '1000001', '1 OR 1=1'])
def test_invalid_point_value_rejected(value):
    with pytest.raises(rewards.CatalogValidationError):
        rewards.parse_points(value)


class RecordingCursor:
    def __init__(self):
        self.executed = []
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.executed.append((' '.join(sql.split()), params))

    def fetchone(self):
        return {'sponsor_id': 9}

    def fetchall(self):
        return self.result


class RecordingConnection:
    def __init__(self):
        self.current = RecordingCursor()

    def cursor(self):
        return self.current

    def close(self):
        pass


def test_sponsor_catalog_filters_are_scoped_and_parameterized(monkeypatch):
    conn = RecordingConnection()
    monkeypatch.setattr(rewards, 'get_db_connection', lambda: conn)
    rewards.get_sponsor_catalog(
        123, search='test%', availability='in_stock',
        min_points='250', max_points='2000', sort='points_desc'
    )
    sql, params = conn.current.executed[-1]
    assert 'WHERE sponsor_id = %s' in sql
    assert 'product_name LIKE %s OR description LIKE %s' in sql
    assert 'COALESCE(availability, FALSE) = %s' in sql
    assert 'point_cost >= %s' in sql and 'point_cost <= %s' in sql
    assert 'ORDER BY point_cost DESC' in sql
    assert params == [9, '%test\\%%', '%test\\%%', True, 250, 2000]


@pytest.mark.parametrize('filters', [
    {'sort': 'price; DELETE FROM ProductCatalog'},
    {'availability': 'other'},
    {'min_points': '500', 'max_points': '100'},
    {'max_points': '-1'},
])
def test_bad_catalog_filters_rejected_before_sql(monkeypatch, filters):
    monkeypatch.setattr(rewards, 'get_db_connection',
                        lambda: (_ for _ in ()).throw(AssertionError('DB should not be opened')))
    with pytest.raises(rewards.CatalogValidationError):
        rewards.get_sponsor_catalog(123, **filters)
