from flask import Blueprint, request, render_template, session, redirect
from database import get_db_connection

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
  
    if username == "admin" and password == "test":
        session['username'] = username
        return redirect('/dashboard')
        
    elif username == "driver" and password == "test":
        session['username'] = username
        return redirect('/dashboard')
        
    elif username == "sponsor" and password == "test":
        session['username'] = username
        return redirect('/dashboard')
        
    else:
        return "Invalid credentials. Please press back and try again."

# Driver Point Report: finds the logged-in sponsor user's company, then returns every
# point change for that company's drivers, including each driver's name and who made the change.
def get_sponsor_point_report(username):
    report = []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT su.sponsor_id
                FROM SponsorUser su
                JOIN UserAccount u ON su.user_id = u.user_id
                WHERE u.username = %s
            """, (username,))
            sponsor = cur.fetchone()

            if sponsor:
                cur.execute("""
                    SELECT d.first_name, d.last_name, p.datetime, p.points_change, p.reason,
                           COALESCE(CONCAT(su.first_name, ' ', su.last_name), 'Unknown') AS changed_by
                    FROM PointChangeLog p
                    JOIN DriverUser d ON p.driver_id = d.driver_id
                    LEFT JOIN SponsorUser su ON p.sponsor_user_id = su.sponsor_user_id
                    WHERE d.sponsor_id = %s
                    ORDER BY p.datetime DESC
                """, (sponsor['sponsor_id'],))
                report = cur.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if conn and conn.open:
            conn.close()
    return report

@auth_bp.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect('/')
    
    current_user = session['username']
    
    if current_user == "admin":
        return render_template('admin_dashboard.html')
    elif current_user == "sponsor":
        return render_template('sponsor_dashboard.html', report=get_sponsor_point_report(current_user))
    else:
        points = 0
        history = []
        
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT d.driver_id, d.points 
                    FROM DriverUser d
                    JOIN UserAccount u ON d.user_id = u.user_id
                    WHERE u.username = %s
                """, (current_user,))
                driver = cur.fetchone()
                
                if driver:
                    points = driver['points']
                    
                    # Sponsor column shows the sponsor user who made the change,
                    # falling back to the driver's sponsor company for rows without one
                    cur.execute("""
                        SELECT p.datetime, p.points_change, p.reason,
                               COALESCE(CONCAT(su.first_name, ' ', su.last_name), s.sponsor_name) AS sponsor_name
                        FROM PointChangeLog p
                        JOIN DriverUser d ON p.driver_id = d.driver_id
                        JOIN Sponsor s ON d.sponsor_id = s.sponsor_id
                        LEFT JOIN SponsorUser su ON p.sponsor_user_id = su.sponsor_user_id
                        WHERE p.driver_id = %s
                        ORDER BY p.datetime DESC
                    """, (driver['driver_id'],))
                    history = cur.fetchall()
        except Exception as e:
            print(f"Database error: {e}")
        finally:
            if 'conn' in locals() and conn.open:
                conn.close()

        return render_template('driver_dashboard.html', points=points, history=history)

@auth_bp.route('/profile')
def profile():
    if 'username' not in session:
        return render_template('index.html') 
    
    current_user = session['username']
    
    mock_user_data = {
        "username": current_user,
        "email": f"{current_user}@example.com",
        "user_type": current_user.capitalize()
    }
    
    return render_template('profile.html', user=mock_user_data)

@auth_bp.route('/logout')
def logout():
    session.pop('username', None)
    return redirect('/')