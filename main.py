import os
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from datetime import datetime
from werkzeug.middleware.proxy_fix import ProxyFix  # ← важно для TimeWeb

# === ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ (.env) ===
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__, template_folder='.')

# === ДОВЕРЯТЬ ЗАГОЛОВКАМ ОТ ПРОКСИ (TimeWeb использует nginx) ===
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# === НАСТРОЙКА SECRET KEY ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===
secret_key = os.environ.get('SECRET_KEY')
if not secret_key:
    print("⚠️ ВНИМАНИЕ: SECRET_KEY не найден в переменных окружения!")
    secret_key = 'dev-fallback-key-change-in-production'

app.secret_key = secret_key

# === НАСТРОЙКА БАЗЫ ДАННЫХ ===
basedir = os.path.abspath(os.path.dirname(__file__))

# На TimeWeb вы можете использовать либо SQLite (если разрешено), либо внешнюю БД
# Здесь предполагается, что вы используете SQLite (site.db в корне)
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(basedir, 'site.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ===
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

# GitHub OAuth — УБРАНЫ ЛИШНИЕ ПРОБЕЛЫ!
github_client_id = os.environ.get('GITHUB_CLIENT_ID')
github_client_secret = os.environ.get('GITHUB_CLIENT_SECRET')

oauth.register(
    name='github',
    client_id=github_client_id or 'not-set',
    client_secret=github_client_secret or 'not-set',
    access_token_url='https://github.com/login/oauth/access_token',
    authorize_url='https://github.com/login/oauth/authorize',
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'user:email'},
)

# Yandex OAuth — УБРАНЫ ЛИШНИЕ ПРОБЕЛЫ!
yandex_client_id = os.environ.get('YANDEX_CLIENT_ID')
yandex_client_secret = os.environ.get('YANDEX_CLIENT_SECRET')

oauth.register(
    name='yandex',
    client_id=yandex_client_id or 'not-set',
    client_secret=yandex_client_secret or 'not-set',
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

# === МАРШРУТЫ ===
@app.route('/')
def index():
    comments = Comment.query.order_by(Comment.timestamp.desc()).all()
    return render_template('index.html', comments=comments)

@app.route('/login/<provider>')
def login(provider):
    if provider not in ['github', 'yandex']:
        return "Неподдерживаемый провайдер", 400

    # Явно указываем redirect_uri для продакшена на churinnick.ru
    redirect_uri = f"https://churinnick.ru/auth/{provider}"
    print(f"🔧 Используем redirect_uri: {redirect_uri}")

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
        # Яндекс требует ?format=json
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

@app.route('/debug-env')
def debug_env():
    import sys
    return {
        'cwd': os.getcwd(),
        'files': os.listdir('.'),
        'secret_key_set': bool(os.environ.get('SECRET_KEY')),
        'github_id_set': bool(os.environ.get('GITHUB_CLIENT_ID')),
        'yandex_id_set': bool(os.environ.get('YANDEX_CLIENT_ID')),
        'python_version': sys.version,
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