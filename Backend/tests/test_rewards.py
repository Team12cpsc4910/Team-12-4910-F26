"""Unit tests for atomic redemptions. Run from Backend: pytest -q tests/test_rewards.py"""

import copy
import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from routes import rewards


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.lastrowid = None
        self.rowcount = 0
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        sql = ' '.join(sql.split()).lower()
    
        if self.conn.fail_on_sql and sql.startswith(self.conn.fail_on_sql):
            raise RuntimeError('Simulated database failure')
    
        d = self.conn.data
        self.rowcount = 0
        self.result = None

        if 'from driveruser where user_id = ' in sql:
            self.result = d['driver'].copy() if params[0] == 42 else None

        elif 'from productcatalog' in sql and 'for update' in sql:
            product = d['product']
            self.result = product.copy() if (params[0] == product['product_id']
                       and params[1] == d['driver']['sponsor_id']) else None

        elif 'from rewardredemption' in sql and 'request_key' in sql:
            existing = [o for o in d['orders'] if o['driver_id'] == params[0]
                        and o['request_key'] == params[1]]
            self.result = ({'redemption_id': existing[0]['redemption_id'],
                            'product_id': existing[0]['product_id']}
                           if existing else None)

        elif 'coalesce(max(point_log_id)' in sql:
            self.result = {'last_id': len(d['logs'])}

        elif sql.startswith('insert into rewardredemption'):
            d['orders'].append({'driver_id': params[0], 'product_id': params[2],
                                'points_spent': params[4],
                                'request_key': params[5],
                                'redemption_id': len(d['orders']) + 1})
            self.lastrowid = len(d['orders'])
            self.rowcount = 1

        elif sql.startswith('update driveruser set points'):
            cost, driver_id, required = params
            if (d['driver']['driver_id'] == driver_id and
                    d['driver']['application_status'] == 'Approved' and
                    d['driver']['points'] >= required):
                d['driver']['points'] -= cost
                self.rowcount = 1
                if d['trigger_enabled']:
                    d['logs'].append({'point_log_id': len(d['logs']) + 1,
                                      'points_change': -cost,
                                      'reason': 'Points removed'})

        elif 'from pointchangelog' in sql and 'point_log_id >' in sql:
            later = [l for l in d['logs'] if l['point_log_id'] > params[1]]
            self.result = {'point_log_id': later[-1]['point_log_id']} if later else None

        elif sql.startswith('update pointchangelog set reason'):
            for log in d['logs']:
                if log['point_log_id'] == params[1]:
                    log['reason'] = params[0]
                    self.rowcount = 1

        elif sql.startswith('insert into pointchangelog'):
            d['logs'].append({'point_log_id': len(d['logs']) + 1,
                              'points_change': params[1], 'reason': params[2]})
            self.rowcount = 1

        else:
            raise AssertionError(f'Unexpected SQL in test: {sql}')

    def fetchone(self):
        return self.result


class FakeConnection:
    def __init__(self, *, balance=500, status='Approved', availability=1,
             sponsor_id=7, price=200, trigger_enabled=True, fail_on_sql=None):
        self.data = {
            'driver': {'driver_id': 4, 'sponsor_id': sponsor_id,
                       'points': balance, 'application_status': status},
            'product': {'product_id': 12, 'product_name': 'Gas card',
                        'point_cost': price, 'availability': availability},
            'trigger_enabled': trigger_enabled,
            'orders': [],
            'logs': []
        }
        self.before = copy.deepcopy(self.data)
        self.fail_on_sql = fail_on_sql
        self.transactions_started = 0
        self.commits = 0
        self.rollbacks = 0
        self.committed = False
        self.rolled_back = False
        
    def cursor(self):
    return FakeCursor(self)

    def begin(self):
        self.transactions_started += 1
        self.before = copy.deepcopy(self.data)
    
    def commit(self):
        self.committed = True
        self.commits += 1
        self.before = copy.deepcopy(self.data)
    
    def rollback(self):
        self.rolled_back = True
        self.rollbacks += 1
        self.data = copy.deepcopy(self.before)

    def close(self):
        pass


@pytest.mark.parametrize('trigger_enabled', [True, False])
def test_redeem_deducts_once_and_records_purchase_and_log(monkeypatch, trigger_enabled):
    db = FakeConnection(trigger_enabled=trigger_enabled)
    monkeypatch.setattr(rewards, 'get_db_connection', lambda: db)

    assert rewards.redeem_reward(user_id=42, product_id=12, request_key='a' * 32) == 1
    assert db.committed
    assert db.transactions_started == 1
    assert db.commits == 1
    assert db.rollbacks == 0
    assert db.data['driver']['points'] == 300
    assert len(db.data['orders']) == 1
    assert db.data['orders'][0]['points_spent'] == 200
    assert len(db.data['logs']) == 1
    assert db.data['logs'][0]['points_change'] == -200
    assert db.data['logs'][0]['reason'] == 'Reward #1: Gas card'


@pytest.mark.parametrize('kwargs, expected_message', [
    ({'balance': 100}, 'need 200 points'),
    ({'status': 'Pending'}, 'approve'),
    ({'status': 'Dropped'}, 'approve'),
    ({'availability': 0}, 'unavailable'),
    ({'price': 0}, 'invalid points'),
    ({'price': -1}, 'invalid points'),
])
def test_invalid_redemptions_roll_back_everything(monkeypatch, kwargs, expected_message):
    db = FakeConnection(**kwargs)
    monkeypatch.setattr(rewards, 'get_db_connection', lambda: db)

    with pytest.raises(rewards.RedemptionError, match=expected_message):
        rewards.redeem_reward(42, 12, 'a' * 32)

    assert db.rolled_back
    assert db.transactions_started == 1
    assert db.commits == 0
    assert db.data == db.before


def test_other_sponsors_product_is_not_accessible(monkeypatch):
    db = FakeConnection()
    monkeypatch.setattr(rewards, 'get_db_connection', lambda: db)

    with pytest.raises(rewards.RedemptionError, match='not available from your sponsor'):
        rewards.redeem_reward(42, 999, 'a' * 32)

    assert db.rolled_back
    assert db.data == db.before


def test_wrong_user_cannot_redeem(monkeypatch):
    db = FakeConnection()
    monkeypatch.setattr(rewards, 'get_db_connection', lambda: db)
    with pytest.raises(rewards.RedemptionError, match='approve'):
        rewards.redeem_reward(99, 12, 'a' * 32)
    assert db.data == db.before


def test_retrying_same_form_does_not_charge_twice(monkeypatch):
    db = FakeConnection()
    monkeypatch.setattr(rewards, 'get_db_connection', lambda: db)

    assert rewards.redeem_reward(42, 12, 'b' * 32) == 1
    assert rewards.redeem_reward(42, 12, 'b' * 32) == 1
    assert db.data['driver']['points'] == 300
    assert len(db.data['orders']) == 1
    assert len(db.data['logs']) == 1
    assert db.transactions_started == 2
    assert db.commits == 1
    assert db.rollbacks == 1


def test_invalid_request_key_is_rejected_before_db_access(monkeypatch):
    def fail_if_db_called():
        raise AssertionError('Should not connect to database')
    monkeypatch.setattr(rewards, 'get_db_connection', fail_if_db_called)
    with pytest.raises(rewards.RedemptionError, match='reload'):
        rewards.redeem_reward(42, 12, '')


def test_reused_request_key_cannot_switch_products(monkeypatch):
    db = FakeConnection()
    monkeypatch.setattr(rewards, 'get_db_connection', lambda: db)
    rewards.redeem_reward(42, 12, 'c' * 32)
    with pytest.raises(rewards.RedemptionError, match='another reward'):
        rewards.redeem_reward(42, 13, 'c' * 32)
    assert len(db.data['orders']) == 1
    assert db.data['driver']['points'] == 300

@pytest.mark.parametrize('failure_point, trigger_enabled', [
    ('update driveruser set points', True),
    ('update pointchangelog set reason', True),
    ('insert into pointchangelog', False),
])
def test_db_write_failure_rolls_back_order_points_and_history(
        monkeypatch, failure_point, trigger_enabled):

    db = FakeConnection(
        trigger_enabled=trigger_enabled,
        fail_on_sql=failure_point
    )

    monkeypatch.setattr(rewards, 'get_db_connection', lambda: db)

    with pytest.raises(RuntimeError, match='Simulated database failure'):
        rewards.redeem_reward(42, 12, 'd' * 32)

    assert db.transactions_started == 1
    assert db.commits == 0
    assert db.rollbacks == 1

    assert db.data == db.before
    assert db.data['driver']['points'] == 500
    assert db.data['orders'] == []
    assert db.data['logs'] == []
