import os
import sys

import pytest

# Let pytest import app.py, accounts.py, and routes/ from Backend/
BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app import app
from accounts import validate_account, AccountValidationError
import routes.login as login_routes
import routes.accounts as account_routes


class FakeCursor:
    def __init__(self, fetchone_value=None, fetchall_value=None):
        self.fetchone_value = fetchone_value
        self.fetchall_value = fetchall_value or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchone(self):
        return self.fetchone_value

    def fetchall(self):
        return self.fetchall_value


class FakeConnection:
    def __init__(self, fetchone_value=None, fetchall_value=None):
        self.fetchone_value = fetchone_value
        self.fetchall_value = fetchall_value or []
        self.open = True

    def cursor(self):
        return FakeCursor(self.fetchone_value, self.fetchall_value)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.open = False


@pytest.fixture
def client():
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    with app.test_client() as client:
        yield client


def valid_driver_data():
    return {
        'username': 'newdriver',
        'email': 'newdriver@example.com',
        'first_name': 'New',
        'last_name': 'Driver',
        'password': 'longpassword123',
        'confirm_password': 'longpassword123',
        'sponsor_id': '1',
        'company_name': '',
    }


# AT-01: The deployed backend should report that it is running.
def test_health_endpoint_returns_ok(client):
    response = client.get('/api/health')

    assert response.status_code == 200
    assert response.get_json() == {'status': 'ok'}


# AT-02: A user who is not logged in cannot open the dashboard.
def test_dashboard_requires_login(client):
    response = client.get('/dashboard', follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/')


# AT-03: The temporary driver test credentials still log in successfully.
def test_driver_test_login_redirects_to_dashboard(client):
    response = client.post(
        '/login',
        data={'username': 'driver', 'password': 'test'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/dashboard')

    with client.session_transaction() as session:
        assert session['username'] == 'driver'


# AT-04: Bad credentials are rejected instead of creating a session.
def test_invalid_login_is_rejected(client, monkeypatch):
    monkeypatch.setattr(
        login_routes,
        'get_db_connection',
        lambda: FakeConnection(fetchone_value=None),
    )

    response = client.post(
        '/login',
        data={'username': 'not-a-user', 'password': 'wrong-password'},
    )

    assert response.status_code == 200
    assert b'Invalid username or password' in response.data

    with client.session_transaction() as session:
        assert 'username' not in session


# AT-05: Public registration creates only a Driver account, even if someone
# changes the HTML and submits another role manually.
def test_public_registration_forces_driver_role(client, monkeypatch):
    sponsors = [{'sponsor_id': 1, 'sponsor_name': 'Test Sponsor'}]
    monkeypatch.setattr(
        account_routes,
        'get_db_connection',
        lambda: FakeConnection(fetchall_value=sponsors),
    )

    captured = {}

    def fake_create_account(data, role, *, allow_new_company=False):
        captured['data'] = data
        captured['role'] = role
        captured['allow_new_company'] = allow_new_company
        return 123

    monkeypatch.setattr(account_routes, 'create_account', fake_create_account)

    with client.session_transaction() as session:
        session['_csrf_token'] = 'test-token'

    form = valid_driver_data()
    form.update({'csrf_token': 'test-token', 'role': 'Admin'})

    response = client.post('/register', data=form)

    assert response.status_code == 201
    assert captured['role'] == 'Driver'
    assert captured['allow_new_company'] is False


# AT-06: Driver registration rejects passwords shorter than 15 characters.
def test_driver_password_must_be_at_least_15_characters():
    data = valid_driver_data()
    data['password'] = 'shortpassword'
    data['confirm_password'] = 'shortpassword'

    with pytest.raises(AccountValidationError, match='15'):
        validate_account(data, 'Driver')


# AT-07: Registration rejects mismatched password confirmation.
def test_registration_rejects_mismatched_passwords():
    data = valid_driver_data()
    data['confirm_password'] = 'differentpassword123'

    with pytest.raises(AccountValidationError, match='Passwords do not match'):
        validate_account(data, 'Driver')


# AT-08: Registration rejects malformed email addresses.
def test_registration_rejects_invalid_email():
    data = valid_driver_data()
    data['email'] = 'not-an-email'

    with pytest.raises(AccountValidationError, match='valid email'):
        validate_account(data, 'Driver')


# AT-09: Logging out removes the current username from the session.
def test_logout_clears_session(client):
    with client.session_transaction() as session:
        session['username'] = 'driver'

    response = client.get('/logout', follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/')

    with client.session_transaction() as session:
        assert 'username' not in session
