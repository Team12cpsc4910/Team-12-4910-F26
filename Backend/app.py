import os

from flask import Flask, render_template, request, send_from_directory
from database import get_db_connection
from security import load_user, csrf_token
from routes.about import about_bp
from routes.login import auth_bp
from routes.accounts import accounts_bp
from routes.applications import applications_bp

app = Flask(__name__, template_folder='../frontend', static_folder='../frontend')

app.secret_key = 'cookiecheesecake'

app.config.update(
    SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE', 'true').lower() == 'true',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    MAX_CONTENT_LENGTH=16 * 1024,
    TRUSTED_FORM_ORIGINS=set(os.getenv(
        'TRUSTED_FORM_ORIGINS',
        'https://team12-backend-api.duckdns.org,https://main.d13wzj1s6istn6.amplifyapp.com'
    ).split(',')),
)
app.jinja_env.globals['csrf_token'] = csrf_token
app.before_request(load_user)
app.register_blueprint(about_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(accounts_bp)
app.register_blueprint(applications_bp)

@app.route('/')
def home():
    return render_template('index.html')

@app.get('/style.css')
def stylesheet():
    return send_from_directory(app.static_folder, 'style.css')
    
@app.get("/api/health")
def health():
    return {
        "status": "ok"
    }

@app.after_request
def response_headers(response):
    response.headers['Access-Control-Allow-Origin'] = 'https://main.d13wzj1s6istn6.amplifyapp.com'
    if request.endpoint != 'static':
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@app.errorhandler(500)
def unavailable(error):
    return 'The service is temporarily unavailable. Please try again later.', 503

if __name__ == "__main__":
    app.run(debug=True)
