import re
import pymysql
from werkzeug.security import generate_password_hash
from database import get_db_connection

class AccountValidationError(ValueError):
    pass


def validate_account(data, role):
    if role not in ('Driver', 'Sponsor', 'Admin'):
        raise AccountValidationError('Choose a valid account type.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,50}', data.get('username', '')):
        raise AccountValidationError('Username must be 3–50 letters, numbers, periods, underscores, or hyphens.')
    if not all(1 <= len(data.get(k, '')) <= 50 for k in ('first_name','last_name')):
        raise AccountValidationError('Enter first and last names of up to 50 characters each.')
    email = data.get('email', '')
    if len(email) > 100 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise AccountValidationError('Enter a valid email address.')
    password = data.get('password', '')
    if not 8 <= len(password) <= 128:
        raise AccountValidationError('Password must be 8–128 characters.')
    if not any(c.isupper() for c in password):
        raise AccountValidationError('Password must contain at least 1 uppercase letter.')
    if not any(c.isdigit() for c in password):
        raise AccountValidationError('Password must contain at least 1 number.')
    if password != data.get('confirm_password'):
        raise AccountValidationError('Passwords do not match.')

def create_account(data, role, *, allow_new_company=False):
    validate_account(data, role)
    company = data.get('company_name', '').strip()
    if company and (not allow_new_company or role != 'Sponsor'):
        raise AccountValidationError('New companies can only be created with a sponsor account by an administrator.')
    if len(company) > 100:
        raise AccountValidationError('Company name must be at most 100 characters.')
    sponsor_id = data.get('sponsor_id', '')
    if company and sponsor_id:
        raise AccountValidationError('Choose an existing sponsor or enter a new company, not both.')
    if role != 'Admin' and not company and not str(sponsor_id).isdigit():
        raise AccountValidationError('Choose a sponsor company.')
    hashed = generate_password_hash(data['password'])
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if company:
                cur.execute('INSERT INTO Sponsor (sponsor_name, point_value) VALUES (%s, %s)', (company, '0.01'))
                sponsor_id = cur.lastrowid
            elif role != 'Admin':
                cur.execute('SELECT sponsor_id FROM Sponsor WHERE sponsor_id = %s', (sponsor_id,))
                if not cur.fetchone():
                    raise AccountValidationError('Choose an available sponsor company.')
            cur.execute('INSERT INTO UserAccount (username, email, password_hash, user_type) VALUES (%s, %s, %s, %s)',
                        (data['username'], data['email'], hashed, role))
            user_id = cur.lastrowid
            if role == 'Driver':
                cur.execute("INSERT INTO DriverUser (user_id, sponsor_id, points, application_status, first_name, last_name) VALUES (%s, %s, 0, 'Pending', %s, %s)",
                            (user_id, sponsor_id, data['first_name'], data['last_name']))
            elif role == 'Sponsor':
                cur.execute('INSERT INTO SponsorUser (user_id, sponsor_id, first_name, last_name) VALUES (%s, %s, %s, %s)',
                            (user_id, sponsor_id, data['first_name'], data['last_name']))
            else:
                cur.execute(
                    'INSERT INTO AdminUser (user_id, first_name, last_name) VALUES (%s, %s, %s)',
                    (user_id, data['first_name'], data['last_name'])
                )
        conn.commit()
        return user_id
    except pymysql.IntegrityError as exc:
        conn.rollback()
        if exc.args[0] == 1062:
            raise AccountValidationError('That username or email is already in use.') from None
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
