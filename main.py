import os
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from datetime import datetime

# === ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ (.env) ===
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__, template_folder='.')

# === НАСТРОЙКА SECRET KEY ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===
secret_key = os.environ.get('SECRET_KEY')
if not secret_key:
    print("⚠️ ВНИМАНИЕ: SECRET_KEY не найден в переменных окружения!")
    secret_key = 'dev-fallback-key-change-in-production'

app.secret_key = secret_key

# === НАСТРОЙКА БАЗЫ ДАННЫХ ===
basedir = os.path.abspath(os.path.dirname(__file__))

# Автоматическое определение окружения
if os.environ.get('DATABASE_URL'):
    # На Render - PostgreSQL
    db_url = os.environ.get('DATABASE_URL')
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    print("✅ Использую PostgreSQL базу данных (Render)")
else:
    # Локально - SQLite
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(basedir, 'site.db')}"
    print(f"✅ Использую SQLite базу данных: site.db")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ (НОВЫЙ СПОСОБ ДЛЯ Flask-SQLAlchemy 3.x) ===
db = SQLAlchemy()
db.init_app(app)

# === FLASK-LOGIN ===
login_manager = LoginManager()
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# === OAuth (ключи из переменных окружения) ===
oauth = OAuth(app)

# GitHub OAuth
github_client_id = os.environ.get('GITHUB_CLIENT_ID')
github_client_secret = os.environ.get('GITHUB_CLIENT_SECRET')

oauth.register(
    name='github',
    client_id=github_client_id if github_client_id else 'not-set',
    client_secret=github_client_secret if github_client_secret else 'not-set',
    access_token_url='https://github.com/login/oauth/access_token',
    authorize_url='https://github.com/login/oauth/authorize',
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'user:email'},
)

# Yandex OAuth
yandex_client_id = os.environ.get('YANDEX_CLIENT_ID')
yandex_client_secret = os.environ.get('YANDEX_CLIENT_SECRET')

oauth.register(
    name='yandex',
    client_id=yandex_client_id if yandex_client_id else 'not-set',
    client_secret=yandex_client_secret if yandex_client_secret else 'not-set',
    access_token_url='https://oauth.yandex.ru/token',
    authorize_url='https://oauth.yandex.ru/authorize',
    api_base_url='https://login.yandex.ru/info/',
    client_kwargs={'scope': 'login:email'},
)

# === МОДЕЛИ БАЗЫ ДАННЫХ ===
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

# Создание таблиц при запуске
with app.app_context():
    db.create_all()
    print("✅ Таблицы базы данных созданы/проверены")

# === МАРШРУТЫ (ROUTES) ===
@app.route('/')
def index():
    comments = Comment.query.order_by(Comment.timestamp.desc()).all()
    return render_template('index.html', comments=comments)

@app.route('/login/<provider>')
def login(provider):
    if provider not in ['github', 'yandex']:
        return "Неподдерживаемый провайдер", 400
    
    # Динамическое определение redirect_uri
    if os.environ.get('RENDER'):
        # На Render (продакшен)
        hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '')
        if not hostname:
            return "Ошибка конфигурации: не найден hostname", 500
        redirect_uri = f"https://{hostname}/auth/{provider}"
        print(f"🌐 Продакшен redirect_uri: {redirect_uri}")
    else:
        # Локальная разработка
        redirect_uri = url_for('auth', provider=provider, _external=True)
        print(f"💻 Локальный redirect_uri: {redirect_uri}")
    
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
        return f"Ошибка OAuth авторизации: {str(e)}", 400

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

    # Поиск или создание пользователя
    user = User.query.filter_by(provider=provider, provider_user_id=provider_user_id).first()
    if not user:
        user = User(
            provider=provider,
            provider_user_id=provider_user_id,
            name=name,
            email=email
        )
        db.session.add(user)
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return f"Ошибка базы данных: {str(e)}", 500

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
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return f"Ошибка сохранения комментария: {str(e)}", 500
    
    return redirect(url_for('index'))

@app.route('/test')
def test():
    return "✅ Flask приложение работает! Версия 1.0"

@app.route('/test-photo')
def test_photo():
    url = url_for('static', filename='photo.jpg')
    return f'''
    <h1>Тест статических файлов</h1>
    <img src="{url}" style="width:300px; border-radius:10px;">
    <p>URL фото: {url}</p>
    <p>Все работает!</p>
    '''

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

# === ДИАГНОСТИЧЕСКИЙ МАРШРУТ ===
@app.route('/debug-env')
def debug_env():
    import sys
    return {
        'port_env': os.environ.get('PORT', 'not set'),
        'database_url': 'set' if os.environ.get('DATABASE_URL') else 'not set',
        'python_version': sys.version,
        'cwd': os.getcwd(),
        'files': os.listdir('.'),
        'app_name': 'app',
        'sqlalchemy_version': SQLAlchemy.__version__ if hasattr(SQLAlchemy, '__version__') else 'unknown'
    }

@app.route('/health')
def health():
    return "OK", 200

# === ЗАПУСК ПРИЛОЖЕНИЯ ===
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    
    print(f"🚀 Запуск Flask приложения на порту {port}")
    print(f"🔧 Режим отладки: {'ВКЛ' if debug_mode else 'ВЫКЛ'}")
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)