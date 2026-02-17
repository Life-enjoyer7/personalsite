import os
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from datetime import datetime
from werkzeug.middleware.proxy_fix import ProxyFix


from dotenv import load_dotenv
load_dotenv()


app = Flask(__name__, template_folder='.')


app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


secret_key = os.environ.get('SECRET_KEY')
if not secret_key:
    raise RuntimeError("SECRET_KEY не задан! Обязателен для сессий.")

app.secret_key = secret_key


app.config.update(
    SESSION_COOKIE_SECURE=True,      
    SESSION_COOKIE_HTTPONLY=True,    
    SESSION_COOKIE_SAMESITE='Lax',   
    PREFERRED_URL_SCHEME='https'     
)


basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(basedir, 'site.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy()
db.init_app(app)


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'index'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


oauth = OAuth(app)


oauth.register(
    name='github',
    client_id=os.environ.get('GITHUB_CLIENT_ID') or 'not-set',
    client_secret=os.environ.get('GITHUB_CLIENT_SECRET') or 'not-set',
    access_token_url='https://github.com/login/oauth/access_token',
    authorize_url='https://github.com/login/oauth/authorize',
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'user:email'},
)


oauth.register(
    name='yandex',
    client_id=os.environ.get('YANDEX_CLIENT_ID') or 'not-set',
    client_secret=os.environ.get('YANDEX_CLIENT_SECRET') or 'not-set',
    access_token_url='https://oauth.yandex.ru/token',
    authorize_url='https://oauth.yandex.ru/authorize',
    api_base_url='https://login.yandex.ru/info/',
    client_kwargs={'scope': 'login:info login:email'},
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(20), nullable=False)
    provider_user_id = db.Column(db.String(100), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100))

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('comments', lazy=True))

with app.app_context():
    db.create_all()


@app.route('/')
def index():
    comments = Comment.query.order_by(Comment.timestamp.desc()).all()
    return render_template('index.html', comments=comments)

@app.route('/login/<provider>')
def login(provider):
    if provider not in ['github', 'yandex']:
        return "Неподдерживаемый провайдер", 400

    
    redirect_uri = f"https://churinnick.ru/auth/{provider}"
    print(f"[DEBUG] Redirect URI: {redirect_uri}")

    client = oauth.create_client(provider)
    if not client:
        return f"OAuth клиент {provider} не настроен", 400

    return client.authorize_redirect(redirect_uri)

@app.route('/auth/<provider>')
def auth(provider):
    if provider not in ['github', 'yandex']:
        return "Неподдерживаемый провайдер", 400

    client = oauth.create_client(provider)
    if not client:
        return f"OAuth клиент {provider} не настроен", 400

    try:
        token = client.authorize_access_token()
    except Exception as e:
        
        print(f"[ERROR] OAuth error for {provider}: {repr(e)}")
        return f"Ошибка авторизации: {str(e)}", 400

    if provider == 'github':
        resp = client.get('user').json()
        provider_user_id = str(resp['id'])
        name = resp.get('name') or resp['login']
        email = resp.get('email')
    elif provider == 'yandex':
        resp = client.get('', params={'format': 'json'}).json()
        provider_user_id = str(resp['id'])
        name = resp.get('display_name') or resp['login']
        email = resp.get('default_email')

    user = User.query.filter_by(provider=provider, provider_user_id=provider_user_id).first()
    if not user:
        user = User(provider=provider, provider_user_id=provider_user_id, name=name, email=email)
        db.session.add(user)
        db.session.commit()

    login_user(user)
    return redirect(url_for('index'))

@app.route('/comment', methods=['POST'])
def add_comment():
    if not current_user.is_authenticated:
        return redirect(url_for('index'))
    text = request.form.get('text', '').strip()
    if text:
        comment = Comment(text=text, user_id=current_user.id)
        db.session.add(comment)
        db.session.commit()
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/debug')
def debug():
    return {
        'session_cookie_domain': request.cookies.get('session', 'no session'),
        'host': request.host,
        'url_scheme': request.scheme,
        'is_secure': request.is_secure,
        'client_id_yandex': bool(os.environ.get('YANDEX_CLIENT_ID')),
        'client_secret_yandex': len(os.environ.get('YANDEX_CLIENT_SECRET', '')) > 0,
    }

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)