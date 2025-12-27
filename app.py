from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from datetime import datetime
import os

app = Flask(__name__, template_folder='.')
app.secret_key = 'your-secret-key-change-in-production'

# === Database ===
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(basedir, 'site.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# === Flask-Login ===
login_manager = LoginManager()
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# === OAuth ===
oauth = OAuth(app)

# GitHub
oauth.register(
    name='github',
    client_id='Ov23liHby6GJPj6xCBtq',
    client_secret='d1738a5c699b5ee3e237ee7b39858990d04a1ed6',
    access_token_url='https://github.com/login/oauth/access_token',
    authorize_url='https://github.com/login/oauth/authorize',
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'user:email'},
)

# Yandex
oauth.register(
    name='yandex',
    client_id='ad9c7c8bb7c142cda7ee9d7be8ea86eb',
    client_secret='666be5424b534005a545287d0539d3ce',
    access_token_url='https://oauth.yandex.ru/token',
    authorize_url='https://oauth.yandex.ru/authorize',
    api_base_url='https://login.yandex.ru/info/',
    client_kwargs={'scope': 'login:email'},
)

# === Models ===
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

# === Routes ===
@app.route('/')
def index():
    comments = Comment.query.order_by(Comment.timestamp.desc()).all()
    return render_template('index.html', comments=comments)

@app.route('/login/<provider>')
def login(provider):
    if provider not in ['github', 'yandex']:
        return "Unsupported provider", 400
    redirect_uri = url_for('auth', provider=provider, _external=True)
    return oauth.create_client(provider).authorize_redirect(redirect_uri)

@app.route('/auth/<provider>')
def auth(provider):
    if provider not in ['github', 'yandex']:
        return "Unsupported provider", 400

    client = oauth.create_client(provider)
    token = client.authorize_access_token()

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
        user = User(
            provider=provider,
            provider_user_id=provider_user_id,
            name=name,
            email=email
        )
        db.session.add(user)
        db.session.commit()

    login_user(user)
    return redirect(url_for('index'))

@app.route('/comment', methods=['POST'])
def add_comment():
    if not current_user.is_authenticated:
        return redirect(url_for('index'))
    text = request.form.get('text')
    if text and text.strip():
        comment = Comment(text=text.strip(), user_id=current_user.id)
        db.session.add(comment)
        db.session.commit()
    return redirect(url_for('index'))

@app.route('/test-photo')
def test_photo():
    url = url_for('static', filename='photo.jpg')
    return f'<img src="{url}" style="width:200px;"> <p>URL: {url}</p>'

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)