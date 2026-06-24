from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from functools import wraps
import sqlite3
import hashlib
import os
import qrcode
import io
import csv
from datetime import datetime
import requests as _requests
import threading
import json
from pywebpush import webpush, WebPushException
import textwrap
from py_vapid import Vapid02 as Vapid
import tempfile, base64
app = Flask(__name__)
app.secret_key = 'edge2systems_inventory_secret_2024'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 43200  # 12 hrs effectively
app.config['SESSION_COOKIE_SECURE'] = True

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'inventory.db')

# ── GPIO pin pool (order = assignment priority) ───────────────────────────────
ALLOWED_GPIO_PINS = [17, 27, 22, 23, 24, 25]

def get_next_gpio_pin(conn):
    """Return the first GPIO pin from ALLOWED_GPIO_PINS not yet assigned to any column.
    Returns None if all pins are taken."""
    used = {row[0] for row in conn.execute(
        "SELECT gpio_pin FROM columns WHERE gpio_pin IS NOT NULL"
    ).fetchall()}
    for pin in ALLOWED_GPIO_PINS:
        if pin not in used:
            return pin
    return None
VAPID_PRIVATE_KEY  = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_PUBLIC_KEY   = os.environ.get('VAPID_PUBLIC_KEY', '')
def _vapid_pem():

    try:
        key_body = "\n".join(textwrap.wrap(VAPID_PRIVATE_KEY, 64))
        return f"-----BEGIN EC PRIVATE KEY-----\n{key_body}\n-----END EC PRIVATE KEY-----"
    except Exception:
        return VAPID_PRIVATE_KEY
VAPID_CLAIMS_EMAIL = os.environ.get('VAPID_CLAIMS_EMAIL', 'admin@edge2.com')

# ─── DB HELPERS ───────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()
# ─── PI LED HELPER ────────────────────────────────────────────────────────────
def trigger_led(column_name):
    """Fire-and-forget POST to Raspberry Pi LED API using gpio_pin from DB."""
    pi_url   = os.environ.get('PI_API_URL', '').rstrip('/')
    pi_token = os.environ.get('PI_API_TOKEN', '')
    if not pi_url:
        return  # silently skip if not configured

    # Look up the gpio_pin assigned to this column
    conn = get_db()
    row = conn.execute(
        "SELECT gpio_pin FROM columns WHERE column_name=?", (column_name,)
    ).fetchone()
    conn.close()

    if not row or row['gpio_pin'] is None:
        return  # no GPIO assigned to this column — skip silently

    gpio_pin = row['gpio_pin']

    def _send():
        try:
            _requests.post(
                f'{pi_url}/led',
                json={'gpio_pin': gpio_pin},
                headers={'Authorization': f'Bearer {pi_token}'},
                timeout=4
            )
        except Exception:
            pass  # Pi offline — don't break the app

    threading.Thread(target=_send, daemon=True).start()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ─── AUTH DECORATORS ──────────────────────────────────────────────────────────

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('role') not in roles:
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator

@app.route('/api/trigger-led', methods=['POST'])
@login_required
def api_trigger_led():
    data        = request.get_json() or {}
    column_name = data.get('column', '')
    if column_name:
        trigger_led(column_name)
    return jsonify({'ok': True})

@app.route('/api/column/preview-gpio')
@login_required
@role_required('admin', 'storekeeper')
def api_column_preview_gpio():
    """Return the GPIO pin that would be auto-assigned to the next new column."""
    conn = get_db()
    pin = get_next_gpio_pin(conn)
    conn.close()
    if pin is None:
        return jsonify({'pin': None, 'error': 'No GPIO pins available (all 6 slots used).'})
    return jsonify({'pin': pin})

# ─── CONTEXT PROCESSOR: LOW STOCK BADGE ──────────────────────────────────────

@app.context_processor
def inject_globals():
    ctx = {'low_stock_alert_count': 0, 'vapid_public_key': VAPID_PUBLIC_KEY}
    if 'user_id' in session and session.get('role') in ('admin', 'storekeeper'):
        conn = get_db()
        count = conn.execute(
            "SELECT COUNT(*) FROM inventory_items WHERE quantity <= min_stock"
        ).fetchone()[0]
        conn.close()
        ctx['low_stock_alert_count'] = count
    return ctx

# ─── INIT DB ──────────────────────────────────────────────────────────────────

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    c = conn.cursor()

    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'employee',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS columns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            column_name TEXT NOT NULL UNIQUE,
            gpio_pin INTEGER,
            qr_code_path TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS boxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            column_id INTEGER NOT NULL,
            box_name TEXT NOT NULL,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(column_id) REFERENCES columns(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            box_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            min_stock INTEGER NOT NULL DEFAULT 5,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(box_id) REFERENCES boxes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            type TEXT NOT NULL,
            project_id INTEGER,
            is_returnable INTEGER NOT NULL DEFAULT 1,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(item_id) REFERENCES inventory_items(id),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subscription_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, subscription_json)
        );

        CREATE TABLE IF NOT EXISTS temp_sensors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            location_label TEXT NOT NULL,
            i2c_channel INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS temp_sensor_columns (
            sensor_id INTEGER NOT NULL,
            column_id INTEGER NOT NULL,
            PRIMARY KEY (sensor_id, column_id),
            FOREIGN KEY(sensor_id) REFERENCES temp_sensors(id) ON DELETE CASCADE,
            FOREIGN KEY(column_id) REFERENCES columns(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS temperature_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            temperature REAL NOT NULL,
            humidity REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS active_borrowings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            quantity_borrowed INTEGER NOT NULL DEFAULT 0,
            is_returnable INTEGER NOT NULL DEFAULT 1,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, item_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(item_id) REFERENCES inventory_items(id)
        );
    ''')

    # Migrate existing tables if columns are missing (safe to run on existing DB)
    for migration in [
        "ALTER TABLE transactions ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL",
        "ALTER TABLE transactions ADD COLUMN is_returnable INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE active_borrowings ADD COLUMN is_returnable INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE temperature_logs ADD COLUMN sensor_id INTEGER REFERENCES temp_sensors(id) ON DELETE SET NULL",
        "ALTER TABLE columns ADD COLUMN gpio_pin INTEGER",
    ]:
        try:
            c.execute(migration)
        except sqlite3.OperationalError:
            pass  # column already exists
    # Migrate push_subscriptions to use endpoint as unique key
    try:
        conn.execute("ALTER TABLE push_subscriptions ADD COLUMN endpoint TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Backfill gpio_pin for the three seed columns if not yet set
    _seed_gpio = [('Column A', 17), ('Column B', 27), ('Column C', 22)]
    for _col_name, _pin in _seed_gpio:
        c.execute(
            "UPDATE columns SET gpio_pin=? WHERE column_name=? AND gpio_pin IS NULL",
            (_pin, _col_name)
        )

    # Backfill endpoint from existing subscription_json rows
    rows = conn.execute("SELECT id, subscription_json FROM push_subscriptions").fetchall()
    for r in rows:
        try:
            ep = json.loads(r['subscription_json']).get('endpoint', '')
            conn.execute("UPDATE push_subscriptions SET endpoint=? WHERE id=?", (ep, r['id']))
        except Exception:
            pass

    conn.commit()
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_push_endpoint ON push_subscriptions(endpoint)")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    # Seed users
    users = [
        ('Admin User',     'admin@edge2.com', hash_password('admin123'),  'admin'),
        ('Store Keeper',   'store@edge2.com', hash_password('store123'),  'storekeeper'),
        ('Alice Engineer', 'alice@edge2.com', hash_password('alice123'),  'employee'),
        ('Bob Intern',     'bob@edge2.com',   hash_password('bob123'),    'employee'),
    ]
    for u in users:
        c.execute("INSERT OR IGNORE INTO users (name,email,password_hash,role) VALUES (?,?,?,?)", u)

    for col in [('Column A',), ('Column B',), ('Column C',)]:
        c.execute("INSERT OR IGNORE INTO columns (column_name) VALUES (?)", col)

    conn.commit()

    col_a = c.execute("SELECT id FROM columns WHERE column_name='Column A'").fetchone()
    col_b = c.execute("SELECT id FROM columns WHERE column_name='Column B'").fetchone()
    col_c = c.execute("SELECT id FROM columns WHERE column_name='Column C'").fetchone()

    if col_a and col_b and col_c:
        boxes = [
            (col_a['id'], 'Box 1', 'Microcontrollers & Dev Boards'),
            (col_a['id'], 'Box 2', 'Sensors'),
            (col_b['id'], 'Box 3', 'Prototyping Components'),
            (col_b['id'], 'Box 4', 'Displays & Modules'),
            (col_c['id'], 'Box 5', 'Power & Cables'),
        ]
        for b in boxes:
            existing = c.execute(
                "SELECT id FROM boxes WHERE column_id=? AND box_name=?", (b[0], b[1])
            ).fetchone()
            if not existing:
                c.execute("INSERT INTO boxes (column_id, box_name, description) VALUES (?,?,?)", b)

        conn.commit()

        box1 = c.execute("SELECT id FROM boxes WHERE box_name='Box 1'").fetchone()
        box2 = c.execute("SELECT id FROM boxes WHERE box_name='Box 2'").fetchone()
        box3 = c.execute("SELECT id FROM boxes WHERE box_name='Box 3'").fetchone()
        box4 = c.execute("SELECT id FROM boxes WHERE box_name='Box 4'").fetchone()
        box5 = c.execute("SELECT id FROM boxes WHERE box_name='Box 5'").fetchone()

        items = [
            (box1['id'], 'Arduino Uno',            24, 5,  'ATmega328P based dev board'),
            (box1['id'], 'ESP32 DevKit',             8, 5,  'WiFi + Bluetooth dev board'),
            (box2['id'], 'Ultrasonic HC-SR04',      30, 10, 'Distance sensor'),
            (box2['id'], 'DHT22 Temp/Humidity',      4, 5,  'Temperature and humidity sensor'),
            (box3['id'], 'Breadboard 830pt',         15, 5,  'Full-size solderless breadboard'),
            (box3['id'], 'Jumper Wires M-M 40pc',   20, 10, 'Male to male jumper wires'),
            (box4['id'], '16x2 LCD Display',         12, 5,  'Character LCD with I2C backpack'),
            (box4['id'], 'OLED 0.96" SSD1306',        3, 5,  '128x64 I2C OLED display'),
            (box5['id'], 'L298N Motor Driver',         9, 3,  'Dual H-bridge motor driver'),
            (box5['id'], 'Buck Converter',             6, 3,  'DC-DC step down converter'),
        ]
        for item in items:
            existing = c.execute(
                "SELECT id FROM inventory_items WHERE box_id=? AND item_name=?",
                (item[0], item[1])
            ).fetchone()
            if not existing:
                c.execute(
                    "INSERT INTO inventory_items (box_id,item_name,quantity,min_stock,description) VALUES (?,?,?,?,?)",
                    item
                )

    conn.commit()
    conn.close()

# ─── QR GENERATION ────────────────────────────────────────────────────────────

def generate_qr_for_column(column_id):
    qr_dir = os.path.join(os.path.dirname(__file__), 'static', 'qrcodes')
    os.makedirs(qr_dir, exist_ok=True)
    path = os.path.join(qr_dir, f'column_{column_id}.png')
    data = f'COLUMN:{column_id}'
    img = qrcode.make(data)
    img.save(path)
    return f'qrcodes/column_{column_id}.png'
# ─── SERVICE WORKER ───────────────────────────────────────────────────────────

@app.route('/service-worker.js')
def service_worker():
    return app.send_static_file('service-worker.js'), 200, {
        'Content-Type': 'application/javascript',
        'Service-Worker-Allowed': '/'
    }
# ─── ROUTES: AUTH ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

_login_attempts = {}

@app.route('/login', methods=['GET', 'POST'])
def login():
    from datetime import datetime, timedelta
    error = None
    ip = request.remote_addr

    if ip in _login_attempts:
        locked_until = _login_attempts[ip].get('locked_until')
        if locked_until and datetime.now() > locked_until:
            _login_attempts[ip] = {'count': 0, 'locked_until': None}

    if request.method == 'POST':
        if ip in _login_attempts and _login_attempts[ip].get('locked_until'):
            if datetime.now() < _login_attempts[ip]['locked_until']:
                remaining = int((_login_attempts[ip]['locked_until'] - datetime.now()).total_seconds())
                error = f'Too many failed attempts. Try again in {remaining} seconds.'
                return render_template('login.html', error=error)

        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        conn = get_db()
        try:
            user = conn.execute(
                "SELECT * FROM users WHERE email=? AND password_hash=?",
                (email, hash_password(password))
            ).fetchone()
        except sqlite3.Error:
            conn.close()
            return render_template('login.html', error='Database error. Please try again.')
        conn.close()

        if user:
            _login_attempts.pop(ip, None)
            session.permanent=True
            session['user_id']   = user['id']
            session['user_name'] = user['name']
            session['role']      = user['role']
            return redirect(url_for('dashboard'))

        if ip not in _login_attempts:
            _login_attempts[ip] = {'count': 0, 'locked_until': None}
        _login_attempts[ip]['count'] += 1
        if _login_attempts[ip]['count'] >= 5:
            _login_attempts[ip]['locked_until'] = datetime.now() + timedelta(seconds=60)
            error = 'Too many failed attempts. Account locked for 60 seconds.'
        else:
            remaining_attempts = 5 - _login_attempts[ip]['count']
            error = f'Invalid email or password. {remaining_attempts} attempt(s) remaining.'

    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─── ROUTES: DASHBOARD ────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    stats = {}
    low_stock_items = []
    out_of_stock_items = []
    recent_txns = []
    most_active_items = []

    if session['role'] in ('admin', 'storekeeper'):
        stats['total_items']        = conn.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0]
        stats['total_boxes']        = conn.execute("SELECT COUNT(*) FROM boxes").fetchone()[0]
        stats['total_columns']      = conn.execute("SELECT COUNT(*) FROM columns").fetchone()[0]
        stats['total_transactions'] = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        stats['active_borrowings']  = conn.execute(
            "SELECT COALESCE(SUM(quantity_borrowed),0) FROM active_borrowings WHERE quantity_borrowed > 0"
        ).fetchone()[0]

        low_stock_items = conn.execute("""
            SELECT ii.*, b.box_name, col.column_name
            FROM inventory_items ii
            JOIN boxes b ON ii.box_id = b.id
            JOIN columns col ON b.column_id = col.id
            WHERE ii.quantity <= ii.min_stock
            ORDER BY ii.quantity ASC LIMIT 6
        """).fetchall()

        stats['low_stock_count'] = conn.execute(
            "SELECT COUNT(*) FROM inventory_items WHERE quantity <= min_stock"
        ).fetchone()[0]

        out_of_stock_items = conn.execute("""
            SELECT ii.*, b.box_name, col.column_name
            FROM inventory_items ii
            JOIN boxes b ON ii.box_id = b.id
            JOIN columns col ON b.column_id = col.id
            WHERE ii.quantity = 0
            ORDER BY ii.item_name
        """).fetchall()

        recent_txns = conn.execute("""
            SELECT t.*, u.name as user_name, ii.item_name, b.box_name, col.column_name,
                   p.name as project_name
            FROM transactions t
            JOIN users u ON t.user_id = u.id
            JOIN inventory_items ii ON t.item_id = ii.id
            JOIN boxes b ON ii.box_id = b.id
            JOIN columns col ON b.column_id = col.id
            LEFT JOIN projects p ON t.project_id = p.id
            ORDER BY t.timestamp DESC LIMIT 8
        """).fetchall()

        most_active_items = conn.execute("""
            SELECT ii.item_name, COUNT(t.id) as txn_count
            FROM transactions t
            JOIN inventory_items ii ON t.item_id = ii.id
            WHERE t.type = 'take'
              AND t.timestamp >= datetime('now', '-7 days')
            GROUP BY t.item_id
            ORDER BY txn_count DESC LIMIT 6
        """).fetchall()

    elif session['role'] == 'employee':
        recent_txns = conn.execute("""
            SELECT t.*, u.name as user_name, ii.item_name, b.box_name, col.column_name,
                   p.name as project_name
            FROM transactions t
            JOIN users u ON t.user_id = u.id
            JOIN inventory_items ii ON t.item_id = ii.id
            JOIN boxes b ON ii.box_id = b.id
            JOIN columns col ON b.column_id = col.id
            LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.user_id = ?
            ORDER BY t.timestamp DESC LIMIT 8
        """, (session['user_id'],)).fetchall()

    conn.close()
    return render_template('dashboard.html',
        stats=stats,
        low_stock_items=low_stock_items,
        out_of_stock_items=out_of_stock_items,
        recent_txns=recent_txns,
        most_active_items=most_active_items
    )

# ─── ROUTES: USER MANAGEMENT ──────────────────────────────────────────────────

@app.route('/users')
@login_required
@role_required('admin')
def users():
    conn = get_db()
    all_users = conn.execute(
        "SELECT id,name,email,role,created_at FROM users ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return render_template('users.html', users=all_users)

@app.route('/users/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def add_user():
    error = None
    if request.method == 'POST':
        name     = request.form['name'].strip()
        email    = request.form['email'].strip()
        password = request.form['password']
        role     = request.form['role']
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)",
                (name, email, hash_password(password), role)
            )
            conn.commit()
            conn.close()
            return redirect(url_for('users'))
        except sqlite3.IntegrityError:
            error = 'Email already exists.'
            conn.close()
    return render_template('add_user.html', error=error)

@app.route('/users/delete/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def delete_user(user_id):
    if user_id == session['user_id']:
        return redirect(url_for('users'))
    try:
        conn = get_db()
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    return redirect(url_for('users'))

@app.route('/users/<int:user_id>')
@login_required
@role_required('admin')
def user_profile(user_id):
    conn = get_db()
    profile_user = conn.execute(
        "SELECT id, name, email, role, created_at FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not profile_user:
        conn.close()
        return redirect(url_for('users'))

    transactions = conn.execute("""
        SELECT t.id, t.item_id, t.quantity, t.type, t.timestamp, t.project_id, t.is_returnable,
               ii.item_name, b.box_name, col.column_name,
               COALESCE(ab.quantity_borrowed, 0) as currently_borrowed,
               p.name as project_name
        FROM transactions t
        JOIN inventory_items ii ON t.item_id = ii.id
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        LEFT JOIN active_borrowings ab ON ab.user_id = t.user_id AND ab.item_id = t.item_id
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE t.user_id = ?
        ORDER BY t.timestamp DESC
    """, (user_id,)).fetchall()

    total_taken = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM transactions WHERE user_id=? AND type='take'",
        (user_id,)
    ).fetchone()[0]
    currently_borrowed = conn.execute(
        "SELECT COALESCE(SUM(quantity_borrowed),0) FROM active_borrowings WHERE user_id=? AND quantity_borrowed > 0",
        (user_id,)
    ).fetchone()[0]
    total_transactions = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE user_id=?", (user_id,)
    ).fetchone()[0]

    conn.close()
    return render_template('user_profile.html',
        profile_user=profile_user, transactions=transactions,
        total_taken=total_taken, currently_borrowed=currently_borrowed,
        total_transactions=total_transactions)

@app.route('/api/admin/user/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def api_edit_user(user_id):
    data     = request.get_json()
    name     = data.get('name', '').strip()
    email    = data.get('email', '').strip()
    role     = data.get('role', '').strip()
    password = data.get('password', '').strip()

    if not name or not email:
        return jsonify({'error': 'Name and email are required.'}), 400
    if role not in ('employee', 'storekeeper', 'admin'):
        return jsonify({'error': 'Invalid role.'}), 400

    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM users WHERE email=? AND id != ?", (email, user_id)
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({'error': 'Email already in use by another user.'}), 400

    if password:
        conn.execute(
            "UPDATE users SET name=?, email=?, role=?, password_hash=? WHERE id=?",
            (name, email, role, hash_password(password), user_id)
        )
    else:
        conn.execute(
            "UPDATE users SET name=?, email=?, role=? WHERE id=?",
            (name, email, role, user_id)
        )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ─── ROUTES: MY ITEMS ─────────────────────────────────────────────────────────

# ─── ROUTES: MY ITEMS ─────────────────────────────────────────────────────────


@app.route('/my-items')
@login_required
def my_items():
    conn = get_db()
    borrowings = conn.execute("""
        SELECT ab.*, ii.item_name, ii.quantity as current_stock,
        b.box_name, col.column_name, ii.id as inventory_item_id,
        p.name as project_name
        FROM active_borrowings ab
        JOIN inventory_items ii ON ab.item_id = ii.id
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        LEFT JOIN (
            SELECT item_id, project_id
            FROM transactions
            WHERE user_id = ? AND type = 'take'
            GROUP BY item_id
            HAVING MAX(timestamp)
        ) last_txn ON last_txn.item_id = ab.item_id
        LEFT JOIN projects p ON p.id = last_txn.project_id
        WHERE ab.user_id = ? AND ab.quantity_borrowed > 0
        ORDER BY ab.updated_at DESC
    """, (session['user_id'], session['user_id'],)).fetchall()
    projects = conn.execute("SELECT id, name FROM projects ORDER BY name").fetchall()
    conn.close()
    return render_template('my_items.html', borrowings=borrowings, projects=projects)

# ─── ROUTES: TRANSACTIONS ─────────────────────────────────────────────────────

@app.route('/transactions')
@login_required
@role_required('admin', 'storekeeper')
def transactions():
    conn = get_db()
    txns = conn.execute("""
        SELECT t.*, u.name as user_name, ii.item_name, b.box_name, col.column_name,
               p.name as project_name
        FROM transactions t
        JOIN users u ON t.user_id = u.id
        JOIN inventory_items ii ON t.item_id = ii.id
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        LEFT JOIN projects p ON t.project_id = p.id
        ORDER BY t.timestamp DESC LIMIT 200
    """).fetchall()
    conn.close()
    return render_template('transactions.html', txns=txns)

# ─── ROUTES: PROJECTS ─────────────────────────────────────────────────────────

@app.route('/projects')
@login_required
@role_required('admin', 'storekeeper')
def projects():
    conn = get_db()
    all_projects = conn.execute("""
        SELECT p.*, COUNT(t.id) as txn_count
        FROM projects p
        LEFT JOIN transactions t ON t.project_id = p.id
        GROUP BY p.id
        ORDER BY p.name
    """).fetchall()

    # For each project, get users and their items
    project_details = {}
    for p in all_projects:
        users = conn.execute("""
            SELECT DISTINCT u.id, u.name
            FROM transactions t
            JOIN users u ON t.user_id = u.id
            WHERE t.project_id = ? AND t.type = 'take'
            ORDER BY u.name
        """, (p['id'],)).fetchall()

        user_items = {}
        for u in users:
            items = conn.execute("""
                SELECT ii.item_name, t.quantity, t.is_returnable, t.timestamp
                FROM transactions t
                JOIN inventory_items ii ON t.item_id = ii.id
                WHERE t.project_id = ? AND t.user_id = ? AND t.type = 'take'
                ORDER BY t.timestamp DESC
            """, (p['id'], u['id'])).fetchall()
            user_items[u['id']] = [dict(i) for i in items]

        project_details[p['id']] = {
            'users': [dict(u) for u in users],
            'user_items': user_items
        }

    conn.close()
    return render_template('projects.html', projects=all_projects, project_details=project_details)

@app.route('/projects/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'storekeeper')
def add_project():
    error = None
    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        if not name:
            error = 'Project name is required.'
        else:
            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO projects (name, description) VALUES (?,?)",
                    (name, description)
                )
                conn.commit()
                conn.close()
                return redirect(url_for('projects'))
            except sqlite3.IntegrityError:
                error = 'A project with that name already exists.'
                conn.close()
    return render_template('add_project.html', error=error)

@app.route('/projects/edit/<int:project_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'storekeeper')
def edit_project(project_id):
    conn = get_db()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return redirect(url_for('projects'))
    error = None
    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        if not name:
            error = 'Project name is required.'
        else:
            try:
                conn.execute(
                    "UPDATE projects SET name=?, description=? WHERE id=?",
                    (name, description, project_id)
                )
                conn.commit()
                conn.close()
                return redirect(url_for('projects'))
            except sqlite3.IntegrityError:
                error = 'A project with that name already exists.'
    conn.close()
    return render_template('add_project.html', error=error, project=project)

@app.route('/projects/delete/<int:project_id>', methods=['POST'])
@login_required
@role_required('admin')
def delete_project(project_id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    return redirect(url_for('projects'))

# ─── ROUTES: COLUMNS ──────────────────────────────────────────────────────────

@app.route('/columns')
@login_required
@role_required('admin', 'storekeeper')
def columns():
    conn = get_db()
    cols = conn.execute('''
            SELECT c.*, COUNT(b.id) as box_count,
                ts.name as sensor_name, ts.location_label as sensor_location
            FROM columns c
            LEFT JOIN boxes b ON b.column_id = c.id
            LEFT JOIN temp_sensor_columns tsc ON tsc.column_id = c.id
            LEFT JOIN temp_sensors ts ON ts.id = tsc.sensor_id
            GROUP BY c.id
            ORDER BY c.column_name
        ''').fetchall()
    conn.close()
    gpio_assigned = request.args.get('gpio_assigned')
    col_name      = request.args.get('col_name', '')
    return render_template('columns.html', columns=cols,
                           gpio_assigned=gpio_assigned, col_name=col_name)

@app.route('/columns/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'storekeeper')
def add_column():
    error = None
    assigned_pin = None  # shown in GET preview before save

    if request.method == 'GET':
        # Preview: what GPIO would be assigned next?
        conn = get_db()
        assigned_pin = get_next_gpio_pin(conn)
        conn.close()

    if request.method == 'POST':
        column_name = request.form.get('column_name', '').strip()
        if not column_name:
            error = 'Column name is required.'
        else:
            conn = get_db()
            gpio_pin = get_next_gpio_pin(conn)
            if gpio_pin is None:
                error = 'No GPIO pins available. Maximum of 6 columns supported with physical LEDs. Remove an existing column to free a pin.'
                conn.close()
            else:
                try:
                    conn.execute(
                        "INSERT INTO columns (column_name, gpio_pin) VALUES (?, ?)",
                        (column_name, gpio_pin)
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for('columns', _anchor='', gpio_assigned=gpio_pin, col_name=column_name))
                except sqlite3.IntegrityError:
                    error = 'A column with that name already exists.'
                    conn.close()

    return render_template('add_column.html', error=error, assigned_pin=assigned_pin)

@app.route('/columns/edit/<int:column_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'storekeeper')
def edit_column(column_id):
    conn = get_db()
    col = conn.execute("SELECT * FROM columns WHERE id=?", (column_id,)).fetchone()
    if not col:
        conn.close()
        return redirect(url_for('columns'))

    error = None
    if request.method == 'POST':
        column_name = request.form.get('column_name', '').strip()
        if not column_name:
            error = 'Column name is required.'
        else:
            try:
                conn.execute(
                    "UPDATE columns SET column_name=? WHERE id=?", (column_name, column_id)
                )
                conn.commit()
                conn.close()
                return redirect(url_for('columns'))
            except sqlite3.IntegrityError:
                error = 'A column with that name already exists.'

    conn.close()
    return render_template('edit_column.html', column=col, error=error)

@app.route('/columns/delete/<int:column_id>', methods=['POST'])
@login_required
@role_required('admin')
def delete_column(column_id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM columns WHERE id=?", (column_id,))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    return redirect(url_for('columns'))

@app.route('/columns/<int:column_id>')
@login_required
@role_required('admin', 'storekeeper')
def column_detail(column_id):
    conn = get_db()
    col = conn.execute("SELECT * FROM columns WHERE id=?", (column_id,)).fetchone()
    if not col:
        conn.close()
        return redirect(url_for('columns'))

    boxes = conn.execute("""
        SELECT b.*,
               COUNT(ii.id) as item_count,
               SUM(CASE WHEN ii.quantity <= ii.min_stock THEN 1 ELSE 0 END) as low_stock_count
        FROM boxes b
        LEFT JOIN inventory_items ii ON ii.box_id = b.id
        WHERE b.column_id = ?
        GROUP BY b.id
        ORDER BY b.box_name
    """, (column_id,)).fetchall()

    conn.close()
    return render_template('column_detail.html', column=col, boxes=boxes)

@app.route('/qr/generate/<int:column_id>')
@login_required
@role_required('admin', 'storekeeper')
def generate_column_qr(column_id):
    conn = get_db()
    col = conn.execute("SELECT * FROM columns WHERE id=?", (column_id,)).fetchone()
    if not col:
        conn.close()
        return jsonify({'error': 'Column not found'}), 404
    qr_path = generate_qr_for_column(column_id)
    conn.execute("UPDATE columns SET qr_code_path=? WHERE id=?", (qr_path, column_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'qr_path': qr_path})

@app.route('/qr/download/<int:column_id>')
@login_required
@role_required('admin', 'storekeeper')
def download_column_qr(column_id):
    conn = get_db()
    col = conn.execute("SELECT * FROM columns WHERE id=?", (column_id,)).fetchone()
    conn.close()
    if not col or not col['qr_code_path']:
        return redirect(url_for('columns'))
    file_path = os.path.join(os.path.dirname(__file__), 'static', col['qr_code_path'])
    return send_file(file_path, as_attachment=True,
                     download_name=f"column_{column_id}_qr.png")

# ─── ROUTES: BOXES ────────────────────────────────────────────────────────────

@app.route('/boxes')
@login_required
@role_required('admin', 'storekeeper')
def boxes():
    conn = get_db()
    filter_col = request.args.get('column_id', '', type=str)
    query = """
        SELECT b.*, col.column_name, COUNT(ii.id) as item_count
        FROM boxes b
        JOIN columns col ON b.column_id = col.id
        LEFT JOIN inventory_items ii ON ii.box_id = b.id
    """
    params = []
    if filter_col:
        query += " WHERE b.column_id = ?"
        params.append(filter_col)
    query += " GROUP BY b.id ORDER BY col.column_name, b.box_name"
    all_boxes = conn.execute(query, params).fetchall()
    all_columns = conn.execute("SELECT * FROM columns ORDER BY column_name").fetchall()
    conn.close()
    return render_template('boxes.html', boxes=all_boxes, columns=all_columns,
                           filter_col=filter_col)

@app.route('/boxes/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'storekeeper')
def add_box():
    conn = get_db()
    all_columns = conn.execute("SELECT * FROM columns ORDER BY column_name").fetchall()
    prefill_column_id = request.args.get('column_id', '')
    error = None
    if request.method == 'POST':
        column_id   = request.form.get('column_id', '').strip()
        box_name    = request.form.get('box_name', '').strip()
        description = request.form.get('description', '').strip()
        if not column_id or not box_name:
            error = 'Column and box name are required.'
        else:
            try:
                conn.execute(
                    "INSERT INTO boxes (column_id, box_name, description) VALUES (?,?,?)",
                    (column_id, box_name, description)
                )
                conn.commit()
                next_url = request.form.get('next', '')
                conn.close()
                if next_url:
                    return redirect(next_url)
                return redirect(url_for('boxes'))
            except sqlite3.IntegrityError:
                error = 'Failed to add box. Please check inputs.'
    conn.close()
    return render_template('add_box.html', columns=all_columns, error=error,
                           prefill_column_id=prefill_column_id)

@app.route('/boxes/edit/<int:box_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'storekeeper')
def edit_box(box_id):
    conn = get_db()
    box = conn.execute("SELECT * FROM boxes WHERE id=?", (box_id,)).fetchone()
    if not box:
        conn.close()
        return redirect(url_for('boxes'))
    all_columns = conn.execute("SELECT * FROM columns ORDER BY column_name").fetchall()
    error = None
    if request.method == 'POST':
        column_id   = request.form.get('column_id', '').strip()
        box_name    = request.form.get('box_name', '').strip()
        description = request.form.get('description', '').strip()
        if not column_id or not box_name:
            error = 'Column and box name are required.'
        else:
            conn.execute(
                "UPDATE boxes SET column_id=?, box_name=?, description=? WHERE id=?",
                (column_id, box_name, description, box_id)
            )
            conn.commit()
            conn.close()
            return redirect(url_for('boxes'))
    conn.close()
    return render_template('edit_box.html', box=box, columns=all_columns, error=error)

@app.route('/boxes/delete/<int:box_id>', methods=['POST'])
@login_required
@role_required('admin')
def delete_box(box_id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM boxes WHERE id=?", (box_id,))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    return redirect(url_for('boxes'))

@app.route('/boxes/<int:box_id>')
@login_required
@role_required('admin', 'storekeeper')
def box_detail(box_id):
    conn = get_db()
    box = conn.execute("""
        SELECT b.*, col.column_name, col.id as column_id
        FROM boxes b
        JOIN columns col ON b.column_id = col.id
        WHERE b.id = ?
    """, (box_id,)).fetchone()
    if not box:
        conn.close()
        return redirect(url_for('boxes'))

    items = conn.execute("""
        SELECT * FROM inventory_items
        WHERE box_id = ?
        ORDER BY item_name
    """, (box_id,)).fetchall()

    conn.close()
    return render_template('box_detail.html', box=box, items=items)

# ─── ROUTES: ITEMS ────────────────────────────────────────────────────────────

@app.route('/items')
@login_required
@role_required('admin', 'storekeeper')
def items():
    conn = get_db()
    filter_col = request.args.get('column_id', '', type=str)
    filter_box = request.args.get('box_id', '', type=str)
    filter_low = request.args.get('low_stock', '', type=str)

    query = """
        SELECT ii.*, b.box_name, col.column_name, col.id as col_id
        FROM inventory_items ii
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        WHERE 1=1
    """
    params = []
    if filter_col:
        query += " AND col.id = ?"
        params.append(filter_col)
    if filter_box:
        query += " AND b.id = ?"
        params.append(filter_box)
    if filter_low:
        query += " AND ii.quantity <= ii.min_stock"
    query += " ORDER BY col.column_name, b.box_name, ii.item_name"

    all_items   = conn.execute(query, params).fetchall()
    all_columns = conn.execute("SELECT * FROM columns ORDER BY column_name").fetchall()
    all_boxes   = conn.execute("SELECT * FROM boxes ORDER BY box_name").fetchall()
    conn.close()
    return render_template('items.html', items=all_items, columns=all_columns,
                           boxes=all_boxes, filter_col=filter_col,
                           filter_box=filter_box, filter_low=filter_low)

@app.route('/items/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'storekeeper')
def add_item():
    conn = get_db()
    all_boxes = conn.execute("""
        SELECT b.*, col.column_name FROM boxes b
        JOIN columns col ON b.column_id = col.id
        ORDER BY col.column_name, b.box_name
    """).fetchall()
    prefill_box_id = request.args.get('box_id', '')
    error = None
    if request.method == 'POST':
        box_id      = request.form.get('box_id', '').strip()
        item_name   = request.form.get('item_name', '').strip()
        quantity    = request.form.get('quantity', '0').strip()
        min_stock   = request.form.get('min_stock', '5').strip()
        description = request.form.get('description', '').strip()
        if not box_id or not item_name:
            error = 'Box and item name are required.'
        else:
            try:
                conn.execute(
                    "INSERT INTO inventory_items (box_id,item_name,quantity,min_stock,description) VALUES (?,?,?,?,?)",
                    (box_id, item_name, int(quantity), int(min_stock), description)
                )
                conn.commit()
                next_url = request.form.get('next', '')
                conn.close()
                if next_url:
                    return redirect(next_url)
                return redirect(url_for('items'))
            except (ValueError, sqlite3.Error):
                error = 'Invalid input. Please check quantities.'
    conn.close()
    return render_template('add_item.html', boxes=all_boxes, error=error,
                           prefill_box_id=prefill_box_id)

@app.route('/items/edit/<int:item_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'storekeeper')
def edit_item(item_id):
    conn = get_db()
    item = conn.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        conn.close()
        return redirect(url_for('items'))
    all_boxes = conn.execute("""
        SELECT b.*, col.column_name FROM boxes b
        JOIN columns col ON b.column_id = col.id
        ORDER BY col.column_name, b.box_name
    """).fetchall()
    error = None
    if request.method == 'POST':
        box_id      = request.form.get('box_id', '').strip()
        item_name   = request.form.get('item_name', '').strip()
        quantity    = request.form.get('quantity', '0').strip()
        min_stock   = request.form.get('min_stock', '5').strip()
        description = request.form.get('description', '').strip()
        if not box_id or not item_name:
            error = 'Box and item name are required.'
        else:
            try:
                conn.execute(
                    "UPDATE inventory_items SET box_id=?,item_name=?,quantity=?,min_stock=?,description=? WHERE id=?",
                    (box_id, item_name, int(quantity), int(min_stock), description, item_id)
                )
                conn.commit()
                conn.close()
                return redirect(url_for('items'))
            except (ValueError, sqlite3.Error):
                error = 'Invalid input. Please check quantities.'
    conn.close()
    return render_template('edit_item.html', item=item, boxes=all_boxes, error=error)

@app.route('/items/delete/<int:item_id>', methods=['POST'])
@login_required
@role_required('admin')
def delete_item(item_id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM inventory_items WHERE id=?", (item_id,))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    return redirect(url_for('items'))

@app.route('/items/restock/<int:item_id>', methods=['POST'])
@login_required
@role_required('admin', 'storekeeper')
def restock_item(item_id):
    qty = request.form.get('quantity', '0')
    try:
        qty = int(qty)
        if qty <= 0:
            raise ValueError
    except ValueError:
        return jsonify({'error': 'Invalid quantity'}), 400

    conn = get_db()
    item = conn.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        conn.close()
        return jsonify({'error': 'Item not found'}), 404

    new_qty = item['quantity'] + qty
    conn.execute("UPDATE inventory_items SET quantity=? WHERE id=?", (new_qty, item_id))
    conn.execute(
        "INSERT INTO transactions (user_id, item_id, quantity, type) VALUES (?,?,?,'restock')",
        (session['user_id'], item_id, qty)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'new_quantity': new_qty})

@app.route('/items/bulk-restock', methods=['POST'])
@login_required
@role_required('admin', 'storekeeper')
def bulk_restock():
    data     = request.get_json()
    item_ids = data.get('item_ids', [])
    quantity = data.get('quantity', 0)

    try:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({'error': 'Quantity must be a positive integer'}), 400

    if not item_ids or not isinstance(item_ids, list):
        return jsonify({'error': 'No items selected'}), 400

    conn = get_db()
    updated = 0
    errors  = []
    for item_id in item_ids:
        try:
            item_id = int(item_id)
            item = conn.execute(
                "SELECT * FROM inventory_items WHERE id=?", (item_id,)
            ).fetchone()
            if not item:
                errors.append(f'Item {item_id} not found')
                continue
            new_qty = item['quantity'] + quantity
            conn.execute(
                "UPDATE inventory_items SET quantity=? WHERE id=?", (new_qty, item_id)
            )
            conn.execute(
                "INSERT INTO transactions (user_id, item_id, quantity, type) VALUES (?,?,?,'restock')",
                (session['user_id'], item_id, quantity)
            )
            updated += 1
        except (ValueError, sqlite3.Error) as e:
            errors.append(str(e))

    conn.commit()
    conn.close()
    return jsonify({
        'success': True,
        'updated': updated,
        'errors': errors,
        'message': f'Successfully restocked {updated} item(s).'
    })

@app.route('/items/restock-all', methods=['POST'])
@login_required
@role_required('admin', 'storekeeper')
def restock_all_low():
    data     = request.get_json()
    quantity = data.get('quantity', 0)

    try:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({'error': 'Quantity must be a positive integer'}), 400

    conn = get_db()
    low_items = conn.execute(
        "SELECT * FROM inventory_items WHERE quantity <= min_stock"
    ).fetchall()

    if not low_items:
        conn.close()
        return jsonify({'error': 'No low-stock items found'}), 400

    updated = 0
    for item in low_items:
        new_qty = item['quantity'] + quantity
        conn.execute(
            "UPDATE inventory_items SET quantity=? WHERE id=?", (new_qty, item['id'])
        )
        conn.execute(
            "INSERT INTO transactions (user_id, item_id, quantity, type) VALUES (?,?,?,'restock')",
            (session['user_id'], item['id'], quantity)
        )
        updated += 1

    conn.commit()
    conn.close()
    return jsonify({
        'success': True,
        'updated': updated,
        'message': f'Successfully restocked {updated} low-stock item(s).'
    })

# ─── ROUTES: INVENTORY EXPORT/IMPORT ─────────────────────────────────────────

@app.route('/inventory/export')
@login_required
@role_required('admin', 'storekeeper')
def inventory_export():
    conn = get_db()
    filter_col = request.args.get('column_id', '')
    filter_box = request.args.get('box_id', '')
    filter_low = request.args.get('low_stock', '')

    query = """
        SELECT col.column_name, b.box_name, ii.item_name,
               ii.quantity, ii.min_stock, ii.description,
               CASE
                   WHEN ii.quantity = 0 THEN 'Out of Stock'
                   WHEN ii.quantity <= ii.min_stock THEN 'Low Stock'
                   ELSE 'Healthy'
               END as status
        FROM inventory_items ii
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        WHERE 1=1
    """
    params = []
    if filter_col:
        query += " AND col.id = ?"
        params.append(filter_col)
    if filter_box:
        query += " AND b.id = ?"
        params.append(filter_box)
    if filter_low:
        query += " AND ii.quantity <= ii.min_stock"
    query += " ORDER BY col.column_name, b.box_name, ii.item_name"

    all_items = conn.execute(query, params).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Column', 'Box', 'Item', 'Quantity', 'Min Stock', 'Description', 'Status'])
    for item in all_items:
        writer.writerow([
            item['column_name'], item['box_name'], item['item_name'],
            item['quantity'], item['min_stock'],
            item['description'] or '',
            item['status']
        ])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'inventory_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )

@app.route('/inventory/import', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'storekeeper')
def inventory_import():
    if request.method == 'GET':
        return render_template('import_items.html')

    action = request.form.get('action', 'preview')

    if action == 'preview':
        file = request.files.get('csv_file')
        if not file or not file.filename.endswith('.csv'):
            return render_template('import_items.html',
                                   error='Please upload a valid CSV file.')
        try:
            content = file.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(content))
            required_cols = {'column_name', 'box_name', 'item_name', 'quantity', 'min_stock'}
            if not reader.fieldnames:
                return render_template('import_items.html', error='CSV file is empty.')
            actual_cols = {c.strip().lower() for c in reader.fieldnames}
            missing = required_cols - actual_cols
            if missing:
                return render_template('import_items.html',
                    error=f'Missing required columns: {", ".join(missing)}')

            rows = []
            errors = []
            for i, row in enumerate(reader, start=2):
                row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
                column_name = row.get('column_name', '').strip()
                box_name    = row.get('box_name', '').strip()
                item_name   = row.get('item_name', '').strip()
                quantity    = row.get('quantity', '').strip()
                min_stock   = row.get('min_stock', '').strip()
                description = row.get('description', '').strip()
                row_errors = []
                if not column_name: row_errors.append('column_name is empty')
                if not box_name:    row_errors.append('box_name is empty')
                if not item_name:   row_errors.append('item_name is empty')
                try:
                    quantity = int(quantity)
                    if quantity < 0: raise ValueError
                except (ValueError, TypeError):
                    row_errors.append('quantity must be a non-negative integer')
                    quantity = None
                try:
                    min_stock = int(min_stock)
                    if min_stock < 0: raise ValueError
                except (ValueError, TypeError):
                    row_errors.append('min_stock must be a non-negative integer')
                    min_stock = None
                if row_errors:
                    errors.append({'row': i, 'errors': row_errors, 'data': row})
                else:
                    rows.append({
                        'column_name': column_name, 'box_name': box_name,
                        'item_name': item_name, 'quantity': quantity,
                        'min_stock': min_stock, 'description': description, 'valid': True
                    })

            session['import_preview'] = rows
            return render_template('import_items.html',
                                   preview_rows=rows, error_rows=errors, preview_ready=True)
        except Exception as e:
            return render_template('import_items.html',
                                   error=f'Failed to parse CSV: {str(e)}')

    elif action == 'confirm':
        rows = session.pop('import_preview', None)
        if not rows:
            return render_template('import_items.html',
                                   error='Preview session expired. Please re-upload.')
        conn = get_db()
        imported = 0; skipped = 0; created_columns = 0; created_boxes = 0
        for row in rows:
            try:
                col = conn.execute(
                    "SELECT id FROM columns WHERE column_name=?", (row['column_name'],)
                ).fetchone()
                if col:
                    col_id = col['id']
                else:
                    cur = conn.execute(
                        "INSERT INTO columns (column_name) VALUES (?)", (row['column_name'],)
                    )
                    col_id = cur.lastrowid
                    created_columns += 1

                box = conn.execute(
                    "SELECT id FROM boxes WHERE column_id=? AND box_name=?",
                    (col_id, row['box_name'])
                ).fetchone()
                if box:
                    box_id = box['id']
                else:
                    cur = conn.execute(
                        "INSERT INTO boxes (column_id, box_name) VALUES (?,?)",
                        (col_id, row['box_name'])
                    )
                    box_id = cur.lastrowid
                    created_boxes += 1

                existing_item = conn.execute(
                    "SELECT id FROM inventory_items WHERE box_id=? AND item_name=?",
                    (box_id, row['item_name'])
                ).fetchone()
                if existing_item:
                    conn.execute(
                        "UPDATE inventory_items SET quantity=?, min_stock=?, description=? WHERE id=?",
                        (row['quantity'], row['min_stock'], row['description'], existing_item['id'])
                    )
                    skipped += 1
                else:
                    conn.execute(
                        "INSERT INTO inventory_items (box_id,item_name,quantity,min_stock,description) VALUES (?,?,?,?,?)",
                        (box_id, row['item_name'], row['quantity'], row['min_stock'], row['description'])
                    )
                    imported += 1
            except sqlite3.Error:
                pass
        conn.commit()
        conn.close()
        return render_template('import_items.html',
                               import_done=True, imported=imported, updated=skipped,
                               created_columns=created_columns, created_boxes=created_boxes)

    return redirect(url_for('inventory_import'))

# ─── ROUTES: ITEM DETAIL ──────────────────────────────────────────────────────

@app.route('/items/<int:item_id>')
@login_required
@role_required('admin', 'storekeeper')
def item_detail(item_id):
    conn = get_db()
    item = conn.execute("""
        SELECT ii.*, b.box_name, b.id as box_id, col.column_name, col.id as column_id
        FROM inventory_items ii
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        WHERE ii.id = ?
    """, (item_id,)).fetchone()
    if not item:
        conn.close()
        return redirect(url_for('items'))

    recent_txns = conn.execute("""
        SELECT t.*, u.name as user_name, p.name as project_name
        FROM transactions t
        JOIN users u ON t.user_id = u.id
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE t.item_id = ?
        ORDER BY t.timestamp DESC
        LIMIT 20
    """, (item_id,)).fetchall()

    total_taken = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM transactions WHERE item_id=? AND type='take'",
        (item_id,)
    ).fetchone()[0]
    total_returned = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM transactions WHERE item_id=? AND type='return'",
        (item_id,)
    ).fetchone()[0]
    total_restocked = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM transactions WHERE item_id=? AND type='restock'",
        (item_id,)
    ).fetchone()[0]
    currently_out = conn.execute(
        "SELECT COALESCE(SUM(quantity_borrowed),0) FROM active_borrowings WHERE item_id=? AND quantity_borrowed > 0",
        (item_id,)
    ).fetchone()[0]

    conn.close()
    return render_template('item_detail.html',
        item=item, recent_txns=recent_txns,
        total_taken=total_taken, total_returned=total_returned,
        total_restocked=total_restocked, currently_out=currently_out)

# ─── ROUTES: LOW STOCK ────────────────────────────────────────────────────────

@app.route('/low-stock')
@login_required
@role_required('admin', 'storekeeper')
def low_stock():
    conn = get_db()
    low_items = conn.execute("""
        SELECT ii.*, b.box_name, col.column_name
        FROM inventory_items ii
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        WHERE ii.quantity <= ii.min_stock
        ORDER BY ii.quantity ASC
    """).fetchall()
    conn.close()
    return render_template('low_stock.html', low_items=low_items)

# ─── ROUTES: SCANNER ──────────────────────────────────────────────────────────

@app.route('/scanner')
@login_required
def scanner():
    return render_template('scanner.html')

# ─── ROUTES: ANALYTICS & REPORTS ─────────────────────────────────────────────

@app.route('/analytics')
@login_required
@role_required('admin')
def analytics():
    return render_template('analytics.html')

@app.route('/reports')
@login_required
@role_required('admin', 'storekeeper')
def reports():
    conn = get_db()
    all_users   = conn.execute("SELECT id, name FROM users ORDER BY name").fetchall()
    all_columns = conn.execute("SELECT id, column_name FROM columns ORDER BY column_name").fetchall()

    from_date = request.args.get('from_date', '')
    to_date   = request.args.get('to_date', '')
    txn_type  = request.args.get('type', '')
    user_id   = request.args.get('user_id', '')
    column_id = request.args.get('column_id', '')

    query = """
        SELECT t.*, u.name as user_name, ii.item_name, b.box_name, col.column_name,
               p.name as project_name
        FROM transactions t
        JOIN users u ON t.user_id = u.id
        JOIN inventory_items ii ON t.item_id = ii.id
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE 1=1
    """
    params = []
    if from_date:
        query += " AND DATE(t.timestamp) >= ?"
        params.append(from_date)
    if to_date:
        query += " AND DATE(t.timestamp) <= ?"
        params.append(to_date)
    if txn_type:
        query += " AND t.type = ?"
        params.append(txn_type)
    if user_id:
        query += " AND t.user_id = ?"
        params.append(user_id)
    if column_id:
        query += " AND col.id = ?"
        params.append(column_id)
    query += " ORDER BY t.timestamp DESC LIMIT 500"

    txns = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('reports.html',
        txns=txns, all_users=all_users, all_columns=all_columns,
        from_date=from_date, to_date=to_date,
        txn_type=txn_type, user_id=user_id, column_id=column_id,
        filters={'from_date': from_date, 'to_date': to_date,
                 'type': txn_type, 'user_id': user_id, 'column_id': column_id})

@app.route('/reports/export')
@login_required
@role_required('admin', 'storekeeper')
def reports_export():
    conn = get_db()
    from_date = request.args.get('from_date', '')
    to_date   = request.args.get('to_date', '')
    txn_type  = request.args.get('type', '')
    user_id   = request.args.get('user_id', '')
    column_id = request.args.get('column_id', '')

    query = """
        SELECT t.timestamp, u.name as user_name, ii.item_name,
               col.column_name, b.box_name, t.type, t.quantity,
               COALESCE(p.name, 'Default') as project_name,
               CASE WHEN t.is_returnable = 1 THEN 'Yes' ELSE 'No' END as returnable
        FROM transactions t
        JOIN users u ON t.user_id = u.id
        JOIN inventory_items ii ON t.item_id = ii.id
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE 1=1
    """
    params = []
    if from_date:
        query += " AND DATE(t.timestamp) >= ?"
        params.append(from_date)
    if to_date:
        query += " AND DATE(t.timestamp) <= ?"
        params.append(to_date)
    if txn_type:
        query += " AND t.type = ?"
        params.append(txn_type)
    if user_id:
        query += " AND t.user_id = ?"
        params.append(user_id)
    if column_id:
        query += " AND col.id = ?"
        params.append(column_id)
    query += " ORDER BY t.timestamp DESC"

    txns = conn.execute(query, params).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Timestamp', 'User', 'Item', 'Column', 'Box', 'Type', 'Quantity', 'Project', 'Returnable'])
    for t in txns:
        writer.writerow([t['timestamp'], t['user_name'], t['item_name'],
                         t['column_name'], t['box_name'], t['type'], t['quantity'],
                         t['project_name'], t['returnable']])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'transactions_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )

# ─── ROUTES: INVENTORY ────────────────────────────────────────────────────────

@app.route('/inventory')
@login_required
@role_required('admin', 'storekeeper')
def inventory():
    import json
    conn = get_db()
    all_items = conn.execute("""
        SELECT ii.id, ii.item_name, ii.quantity, ii.min_stock, ii.description,
               b.id as box_id, b.box_name,
               col.id as column_id, col.column_name
        FROM inventory_items ii
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        ORDER BY col.column_name, b.box_name, ii.item_name
    """).fetchall()

    boxes_with_stats = conn.execute("""
        SELECT b.id, b.box_name, b.description,
               col.id as column_id, col.column_name,
               COUNT(ii.id) as item_count,
               COALESCE(SUM(ii.quantity), 0) as total_quantity,
               SUM(CASE WHEN ii.quantity = 0 THEN 1 ELSE 0 END) as out_of_stock_count,
               SUM(CASE WHEN ii.quantity > 0 AND ii.quantity <= ii.min_stock THEN 1 ELSE 0 END) as low_stock_count
        FROM boxes b
        JOIN columns col ON b.column_id = col.id
        LEFT JOIN inventory_items ii ON ii.box_id = b.id
        GROUP BY b.id
        ORDER BY col.column_name, b.box_name
    """).fetchall()

    all_columns = conn.execute("SELECT id, column_name FROM columns ORDER BY column_name").fetchall()
    all_boxes   = conn.execute("""
        SELECT b.id, b.box_name, b.column_id, col.column_name
        FROM boxes b JOIN columns col ON b.column_id = col.id
        ORDER BY col.column_name, b.box_name
    """).fetchall()

    total_items     = len(all_items)
    out_of_stock    = sum(1 for i in all_items if i['quantity'] == 0)
    low_stock_count = sum(1 for i in all_items if 0 < i['quantity'] <= i['min_stock'])
    healthy_count   = sum(1 for i in all_items if i['quantity'] > i['min_stock'])

    items_by_box = {}
    for item in all_items:
        bid = item['box_id']
        if bid not in items_by_box:
            items_by_box[bid] = []
        qty = item['quantity']
        min_s = item['min_stock']
        status = 'out' if qty == 0 else ('low' if qty <= min_s else 'ok')
        items_by_box[bid].append({
            'id': item['id'], 'name': item['item_name'] or '',
            'description': item['description'] or '',
            'quantity': qty, 'min_stock': min_s,
            'box_id': bid, 'column_id': item['column_id'], 'status': status,
        })

    conn.close()
    return render_template('inventory.html',
        items=all_items, items_json=json.dumps(items_by_box),
        boxes_with_stats=boxes_with_stats,
        all_columns=all_columns, all_boxes=all_boxes,
        total_items=total_items, out_of_stock=out_of_stock,
        low_stock_count=low_stock_count, healthy_count=healthy_count)

# ─── API: PROJECTS ────────────────────────────────────────────────────────────

@app.route('/api/projects')
@login_required
def api_projects():
    conn = get_db()
    projs = conn.execute("SELECT id, name FROM projects ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(p) for p in projs])

# ─── API: SCANNER ENDPOINTS ───────────────────────────────────────────────────

@app.route('/api/column/<int:column_id>/boxes')
@login_required
def api_column_boxes(column_id):
    conn = get_db()
    col = conn.execute("SELECT * FROM columns WHERE id=?", (column_id,)).fetchone()
    if not col:
        conn.close()
        return jsonify({'error': 'Column not found'}), 404
    boxes = conn.execute("""
        SELECT b.id, b.box_name, b.description, COUNT(ii.id) as item_count
        FROM boxes b
        LEFT JOIN inventory_items ii ON ii.box_id = b.id
        WHERE b.column_id = ?
        GROUP BY b.id
        ORDER BY b.box_name
    """, (column_id,)).fetchall()
    conn.close()
    return jsonify({'column_name': col['column_name'], 'boxes': [dict(b) for b in boxes]})

@app.route('/api/box/<int:box_id>/items')
@login_required
def api_box_items(box_id):
    conn = get_db()
    box = conn.execute("""
        SELECT b.*, col.column_name FROM boxes b
        JOIN columns col ON b.column_id = col.id
        WHERE b.id = ?
    """, (box_id,)).fetchone()
    if not box:
        conn.close()
        return jsonify({'error': 'Box not found'}), 404
    items = conn.execute("""
        SELECT id, item_name, quantity, min_stock, description
        FROM inventory_items WHERE box_id = ?
        ORDER BY item_name
    """, (box_id,)).fetchall()
    conn.close()
    return jsonify({'box_name': box['box_name'], 'column_name': box['column_name'],
                    'items': [dict(i) for i in items]})

@app.route('/api/item/<int:item_id>')
@login_required
def api_item(item_id):
    conn = get_db()
    item = conn.execute("""
        SELECT ii.*, b.box_name, col.column_name
        FROM inventory_items ii
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        WHERE ii.id = ?
    """, (item_id,)).fetchone()
    if not item:
        conn.close()
        return jsonify({'error': 'Item not found'}), 404
    borrowed = conn.execute(
        "SELECT COALESCE(quantity_borrowed, 0) as qty, COALESCE(is_returnable, 1) as is_returnable FROM active_borrowings WHERE user_id=? AND item_id=?",
        (session['user_id'], item_id)
    ).fetchone()
    conn.close()
    d = dict(item)
    d['quantity_borrowed'] = borrowed['qty'] if borrowed else 0
    d['is_returnable']     = borrowed['is_returnable'] if borrowed else 1
    return jsonify(d)

@app.route('/api/transaction', methods=['POST'])
@login_required
def api_transaction():
    data         = request.get_json()
    item_id      = data.get('item_id')
    quantity     = data.get('quantity')
    txn_type     = data.get('type')
    project_id   = data.get('project_id') or None   # None means "Default / no project"
    is_returnable = int(data.get('is_returnable', 1))

    if not item_id or not quantity or txn_type not in ('take', 'return'):
        return jsonify({'error': 'Invalid request'}), 400

    try:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({'error': 'Quantity must be a positive integer'}), 400

    conn = get_db()
    item = conn.execute(
        "SELECT * FROM inventory_items WHERE id=?", (item_id,)
    ).fetchone()
    if not item:
        conn.close()
        return jsonify({'error': 'Item not found'}), 404

    user_id = session['user_id']

    if txn_type == 'take':
        if quantity > item['quantity']:
            conn.close()
            return jsonify({'error': f'Only {item["quantity"]} units available'}), 400

        new_qty = item['quantity'] - quantity
        conn.execute(
            "UPDATE inventory_items SET quantity=? WHERE id=?", (new_qty, item_id)
        )
        existing = conn.execute(
            "SELECT * FROM active_borrowings WHERE user_id=? AND item_id=?",
            (user_id, item_id)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE active_borrowings SET quantity_borrowed=quantity_borrowed+?, is_returnable=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND item_id=?",
                (quantity, is_returnable, user_id, item_id)
            )
        else:
            conn.execute(
                "INSERT INTO active_borrowings (user_id, item_id, quantity_borrowed, is_returnable) VALUES (?,?,?,?)",
                (user_id, item_id, quantity, is_returnable)
            )
        conn.execute(
            "INSERT INTO transactions (user_id, item_id, quantity, type, project_id, is_returnable) VALUES (?,?,?,?,?,?)",
            (user_id, item_id, quantity, 'take', project_id, is_returnable)
        )

    elif txn_type == 'return':
        borrowing = conn.execute(
            "SELECT * FROM active_borrowings WHERE user_id=? AND item_id=?",
            (user_id, item_id)
        ).fetchone()
        currently_borrowed = borrowing['quantity_borrowed'] if borrowing else 0
        borrow_returnable  = borrowing['is_returnable'] if borrowing else 1

        if borrow_returnable == 0:
            conn.close()
            return jsonify({'error': 'This item was marked as non-returnable when taken.'}), 400
        if quantity > currently_borrowed:
            conn.close()
            return jsonify({'error': f'You only have {currently_borrowed} units borrowed'}), 400

        new_qty = item['quantity'] + quantity
        conn.execute(
            "UPDATE inventory_items SET quantity=? WHERE id=?", (new_qty, item_id)
        )
        conn.execute(
            "UPDATE active_borrowings SET quantity_borrowed=quantity_borrowed-?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND item_id=?",
            (quantity, user_id, item_id)
        )
        conn.execute(
            "INSERT INTO transactions (user_id, item_id, quantity, type, project_id, is_returnable) VALUES (?,?,?,?,?,?)",
            (user_id, item_id, quantity, 'return', project_id, 1)
        )

    conn.commit()
    updated = conn.execute(
        "SELECT quantity FROM inventory_items WHERE id=?", (item_id,)
    ).fetchone()
    conn.close()

    return jsonify({
        'success': True,
        'new_quantity': updated['quantity'],
        'message': f'Successfully {"took" if txn_type == "take" else "returned"} {quantity} unit(s).'
    })

@app.route('/api/restock', methods=['POST'])
@login_required
@role_required('admin', 'storekeeper')
def api_restock():
    data       = request.get_json()
    item_id    = data.get('item_id')
    quantity   = data.get('quantity')
    project_id = data.get('project_id') or None

    try:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({'error': 'Quantity must be a positive integer'}), 400

    conn = get_db()
    item = conn.execute(
        "SELECT * FROM inventory_items WHERE id=?", (item_id,)
    ).fetchone()
    if not item:
        conn.close()
        return jsonify({'error': 'Item not found'}), 404

    new_qty = item['quantity'] + quantity
    conn.execute(
        "UPDATE inventory_items SET quantity=? WHERE id=?", (new_qty, item_id)
    )
    conn.execute(
        "INSERT INTO transactions (user_id, item_id, quantity, type, project_id) VALUES (?,?,?,'restock',?)",
        (session['user_id'], item_id, quantity, project_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'new_quantity': new_qty})

# ─── ROUTES: PROFILE & HISTORY ────────────────────────────────────────────────

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    conn = get_db()
    user = conn.execute(
        "SELECT id, name, email, role, created_at FROM users WHERE id=?",
        (session['user_id'],)
    ).fetchone()

    success = None
    error   = None

    if request.method == 'POST':
        action = request.form.get('action', '')

        if action == 'update_info':
            name  = request.form.get('name', '').strip()
            email = request.form.get('email', '').strip()
            if not name or not email:
                error = 'Name and email are required.'
            else:
                existing = conn.execute(
                    "SELECT id FROM users WHERE email=? AND id != ?",
                    (email, session['user_id'])
                ).fetchone()
                if existing:
                    error = 'That email is already in use by another account.'
                else:
                    conn.execute(
                        "UPDATE users SET name=?, email=? WHERE id=?",
                        (name, email, session['user_id'])
                    )
                    conn.commit()
                    session['user_name'] = name
                    success = 'Profile updated successfully.'
                    user = conn.execute(
                        "SELECT id, name, email, role, created_at FROM users WHERE id=?",
                        (session['user_id'],)
                    ).fetchone()

        elif action == 'change_password':
            current_pw = request.form.get('current_password', '')
            new_pw     = request.form.get('new_password', '')
            confirm_pw = request.form.get('confirm_password', '')
            if not current_pw or not new_pw or not confirm_pw:
                error = 'All password fields are required.'
            elif new_pw != confirm_pw:
                error = 'New passwords do not match.'
            elif len(new_pw) < 6:
                error = 'New password must be at least 6 characters.'
            else:
                valid = conn.execute(
                    "SELECT id FROM users WHERE id=? AND password_hash=?",
                    (session['user_id'], hash_password(current_pw))
                ).fetchone()
                if not valid:
                    error = 'Current password is incorrect.'
                else:
                    conn.execute(
                        "UPDATE users SET password_hash=? WHERE id=?",
                        (hash_password(new_pw), session['user_id'])
                    )
                    conn.commit()
                    success = 'Password changed successfully.'

    total_taken = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM transactions WHERE user_id=? AND type='take'",
        (session['user_id'],)
    ).fetchone()[0]
    total_returned = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM transactions WHERE user_id=? AND type='return'",
        (session['user_id'],)
    ).fetchone()[0]
    total_txns = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE user_id=?",
        (session['user_id'],)
    ).fetchone()[0]
    currently_borrowed = conn.execute(
        "SELECT COALESCE(SUM(quantity_borrowed),0) FROM active_borrowings WHERE user_id=? AND quantity_borrowed > 0",
        (session['user_id'],)
    ).fetchone()[0]

    borrowings = conn.execute("""
        SELECT ab.*, ii.item_name, ii.quantity as current_stock,
               b.box_name, col.column_name, ii.id as inventory_item_id
        FROM active_borrowings ab
        JOIN inventory_items ii ON ab.item_id = ii.id
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        WHERE ab.user_id = ? AND ab.quantity_borrowed > 0
        ORDER BY ab.updated_at DESC
    """, (session['user_id'],)).fetchall()

    conn.close()
    return render_template('profile.html',
        user=user, success=success, error=error,
        total_taken=total_taken, total_returned=total_returned,
        total_txns=total_txns, currently_borrowed=currently_borrowed,
        borrowings=borrowings)

@app.route('/my-history')
@login_required
def my_history():
    conn = get_db()
    txn_type = request.args.get('type', '')

    query = """
        SELECT t.*, ii.item_name, b.box_name, col.column_name,
               p.name as project_name
        FROM transactions t
        JOIN inventory_items ii ON t.item_id = ii.id
        JOIN boxes b ON ii.box_id = b.id
        JOIN columns col ON b.column_id = col.id
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE t.user_id = ?
    """
    params = [session['user_id']]
    if txn_type in ('take', 'return', 'restock'):
        query += " AND t.type = ?"
        params.append(txn_type)
    query += " ORDER BY t.timestamp DESC"

    txns = conn.execute(query, params).fetchall()

    stats = {
        'total_taken': conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM transactions WHERE user_id=? AND type='take'",
            (session['user_id'],)
        ).fetchone()[0],
        'total_returned': conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM transactions WHERE user_id=? AND type='return'",
            (session['user_id'],)
        ).fetchone()[0],
        'total_restocked': conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM transactions WHERE user_id=? AND type='restock'",
            (session['user_id'],)
        ).fetchone()[0],
    }

    conn.close()
    return render_template('history.html', txns=txns, txn_type=txn_type, stats=stats)

# ─── API: QUICK RETURN ────────────────────────────────────────────────────────

@app.route('/api/quick-return', methods=['POST'])
@login_required
def api_quick_return():
    data       = request.get_json()
    item_id    = data.get('item_id')
    quantity   = data.get('quantity')
    project_id = data.get('project_id') or None

    try:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({'error': 'Quantity must be a positive integer'}), 400

    conn = get_db()
    borrowing = conn.execute(
        "SELECT * FROM active_borrowings WHERE user_id=? AND item_id=?",
        (session['user_id'], item_id)
    ).fetchone()

    if not borrowing or borrowing['quantity_borrowed'] <= 0:
        conn.close()
        return jsonify({'error': 'You have no units of this item borrowed'}), 400

    if borrowing['is_returnable'] == 0:
        conn.close()
        return jsonify({'error': 'This item was marked as non-returnable when taken.'}), 400

    if quantity > borrowing['quantity_borrowed']:
        conn.close()
        return jsonify({'error': f'You only have {borrowing["quantity_borrowed"]} unit(s) borrowed'}), 400

    item = conn.execute(
        "SELECT * FROM inventory_items WHERE id=?", (item_id,)
    ).fetchone()
    if not item:
        conn.close()
        return jsonify({'error': 'Item not found'}), 404

    new_qty = item['quantity'] + quantity
    conn.execute(
        "UPDATE inventory_items SET quantity=? WHERE id=?", (new_qty, item_id)
    )
    conn.execute(
        "UPDATE active_borrowings SET quantity_borrowed=quantity_borrowed-?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND item_id=?",
        (quantity, session['user_id'], item_id)
    )
    conn.execute(
        "INSERT INTO transactions (user_id, item_id, quantity, type, project_id) VALUES (?,?,?,'return',?)",
        (session['user_id'], item_id, quantity, project_id)
    )
    conn.commit()

    remaining = conn.execute(
        "SELECT quantity_borrowed FROM active_borrowings WHERE user_id=? AND item_id=?",
        (session['user_id'], item_id)
    ).fetchone()
    conn.close()

    return jsonify({
        'success': True,
        'new_stock': new_qty,
        'remaining_borrowed': remaining['quantity_borrowed'] if remaining else 0,
        'message': f'Successfully returned {quantity} unit(s).'
    })

# ─── API: TOGGLE RETURNABLE ───────────────────────────────────────────────────

@app.route('/api/toggle-returnable', methods=['POST'])
@login_required
def api_toggle_returnable():
    data    = request.get_json()
    item_id = data.get('item_id')

    if not item_id:
        return jsonify({'error': 'item_id required'}), 400

    conn = get_db()
    borrowing = conn.execute(
        "SELECT * FROM active_borrowings WHERE user_id=? AND item_id=?",
        (session['user_id'], item_id)
    ).fetchone()

    if not borrowing or borrowing['quantity_borrowed'] <= 0:
        conn.close()
        return jsonify({'error': 'No active borrowing found'}), 404

    new_val = 0 if borrowing['is_returnable'] == 1 else 1
    conn.execute(
        "UPDATE active_borrowings SET is_returnable=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND item_id=?",
        (new_val, session['user_id'], item_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'is_returnable': new_val})

# ─── API: BOX ADD ITEM ────────────────────────────────────────────────────────

@app.route('/api/box/<int:box_id>/add-item', methods=['POST'])
@login_required
@role_required('admin', 'storekeeper')
def api_add_item_to_box(box_id):
    data        = request.get_json()
    item_name   = (data.get('item_name') or '').strip()
    quantity    = data.get('quantity', 0)
    min_stock   = data.get('min_stock', 5)
    description = (data.get('description') or '').strip()

    if not item_name:
        return jsonify({'error': 'Item name is required.'}), 400
    try:
        quantity  = int(quantity)
        min_stock = int(min_stock)
        if quantity < 0 or min_stock < 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({'error': 'Quantity and min stock must be non-negative integers.'}), 400

    conn = get_db()
    box = conn.execute("SELECT * FROM boxes WHERE id=?", (box_id,)).fetchone()
    if not box:
        conn.close()
        return jsonify({'error': 'Box not found.'}), 404

    try:
        conn.execute(
            "INSERT INTO inventory_items (box_id, item_name, quantity, min_stock, description) VALUES (?,?,?,?,?)",
            (box_id, item_name, quantity, min_stock, description)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except sqlite3.Error as e:
        conn.close()
        return jsonify({'error': 'Database error: ' + str(e)}), 500

# ─── API: ANALYTICS ───────────────────────────────────────────────────────────

@app.route('/api/analytics/summary')
@login_required
@role_required('admin')
def api_analytics_summary():
    try:
        conn = get_db()
        total_items    = conn.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0]
        total_txns     = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        total_borrowed = conn.execute("SELECT COALESCE(SUM(quantity_borrowed),0) FROM active_borrowings WHERE quantity_borrowed > 0").fetchone()[0]
        active_users   = conn.execute("SELECT COUNT(DISTINCT user_id) FROM transactions WHERE timestamp >= datetime('now', '-30 days')").fetchone()[0]
        out_of_stock   = conn.execute("SELECT COUNT(*) FROM inventory_items WHERE quantity = 0").fetchone()[0]
        low_stock      = conn.execute("SELECT COUNT(*) FROM inventory_items WHERE quantity > 0 AND quantity <= min_stock").fetchone()[0]
        healthy        = conn.execute("SELECT COUNT(*) FROM inventory_items WHERE quantity > min_stock").fetchone()[0]
        conn.close()
        return jsonify({'total_items': total_items, 'total_txns': total_txns,
                        'total_borrowed': total_borrowed, 'active_users': active_users,
                        'out_of_stock': out_of_stock, 'low_stock': low_stock, 'healthy': healthy})
    except sqlite3.Error:
        return jsonify({'error': 'Database error'}), 500

@app.route('/api/analytics/usage')
@login_required
@role_required('admin')
def api_analytics_usage():
    try:
        conn = get_db()
        top_items = conn.execute("""
            SELECT ii.item_name, COUNT(t.id) as take_count
            FROM transactions t
            JOIN inventory_items ii ON t.item_id = ii.id
            WHERE t.type = 'take'
            GROUP BY t.item_id
            ORDER BY take_count DESC LIMIT 8
        """).fetchall()
        user_usage = conn.execute("""
            SELECT u.name, COUNT(t.id) as txn_count
            FROM transactions t
            JOIN users u ON t.user_id = u.id
            GROUP BY t.user_id
            ORDER BY txn_count DESC LIMIT 8
        """).fetchall()
        top_borrowers = conn.execute("""
            SELECT u.name, COUNT(ab.item_id) as item_count,
                   SUM(ab.quantity_borrowed) as total_borrowed
            FROM active_borrowings ab
            JOIN users u ON ab.user_id = u.id
            WHERE ab.quantity_borrowed > 0
            GROUP BY ab.user_id
            ORDER BY total_borrowed DESC LIMIT 6
        """).fetchall()
        conn.close()
        return jsonify({'top_items': [dict(r) for r in top_items],
                        'user_usage': [dict(r) for r in user_usage],
                        'top_borrowers': [dict(r) for r in top_borrowers]})
    except sqlite3.Error:
        return jsonify({'error': 'Database error'}), 500

@app.route('/api/analytics/daily')
@login_required
@role_required('admin')
def api_analytics_daily():
    try:
        conn = get_db()
        rows = conn.execute("""
            SELECT DATE(timestamp) as day,
                   SUM(CASE WHEN type='take'    THEN 1 ELSE 0 END) as takes,
                   SUM(CASE WHEN type='return'  THEN 1 ELSE 0 END) as returns,
                   SUM(CASE WHEN type='restock' THEN 1 ELSE 0 END) as restocks,
                   COUNT(*) as total
            FROM transactions
            WHERE timestamp >= datetime('now', '-30 days')
            GROUP BY DATE(timestamp)
            ORDER BY day ASC
        """).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except sqlite3.Error:
        return jsonify({'error': 'Database error'}), 500
# ─── PUSH NOTIFICATIONS ───────────────────────────────────────────────────────


@app.route('/api/push/vapid-public-key')
def api_vapid_public_key():
    return jsonify({'publicKey': VAPID_PUBLIC_KEY})

@app.route('/api/push/subscribe', methods=['POST'])
@login_required
def api_push_subscribe():
    sub = request.get_json()
    if not sub:
        return jsonify({'error': 'No subscription data'}), 400
    endpoint = sub.get('endpoint', '')
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO push_subscriptions (user_id, endpoint, subscription_json)
            VALUES (?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                subscription_json = excluded.subscription_json,
                user_id = excluded.user_id
        """, (session['user_id'], endpoint, json.dumps(sub)))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'ok': True})

@app.route('/api/push/unsubscribe', methods=['POST'])
@login_required
def api_push_unsubscribe():
    sub = request.get_json()
    conn = get_db()
    conn.execute(
        "DELETE FROM push_subscriptions WHERE user_id=? AND subscription_json=?",
        (session['user_id'], json.dumps(sub))
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})
def send_push_to_all(title, body, url='/', tag='edge2-alert'):
    import tempfile, os as _os
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return

    ec_pem = _vapid_pem()
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False)
    tmp.write(ec_pem + '\n')
    tmp.close()

    conn = get_db()
    subs = conn.execute("SELECT * FROM push_subscriptions").fetchall()
    conn.close()

    payload = json.dumps({'title': title, 'body': body, 'url': url, 'tag': tag})
    dead = []

    try:
        for row in subs:
            try:
                sub = json.loads(row['subscription_json'])
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=tmp.name,
                    vapid_claims={'sub': f'mailto:{VAPID_CLAIMS_EMAIL}'}
                )
                app.logger.info(f'Push sent to sub id {row["id"]}')
            except WebPushException as e:
                resp = e.response
                if resp is not None and resp.status_code in (404, 410):
                    dead.append(row['id'])
                else:
                    app.logger.error(f'WebPush failed: {e}')
            except Exception as e:
                app.logger.error(f'Push error: {e}')
    finally:
        _os.unlink(tmp.name)

    if dead:
        conn = get_db()
        for did in dead:
            conn.execute("DELETE FROM push_subscriptions WHERE id=?", (did,))
        conn.commit()
        conn.close()
# ─── API: TEMPERATURE LOGGING ─────────────────────────────────────────────────


@app.route('/api/temperature', methods=['POST'])
def api_temperature_log():
    token = request.headers.get('Authorization', '')
    if token != f"Bearer {os.environ.get('PI_API_TOKEN', '')}":
        return jsonify({'error': 'Unauthorized'}), 401
 
    data        = request.get_json() or {}
    temperature = data.get('temperature')
    humidity    = data.get('humidity')
    sensor_id   = data.get('sensor_id')      # NEW: which physical sensor
 
    if temperature is None:
        return jsonify({'error': 'temperature required'}), 400
 
    conn = get_db()
 
    # Resolve location label from sensor_id (if provided)
    location_label = None
    if sensor_id:
        row = conn.execute(
            "SELECT location_label FROM temp_sensors WHERE id=?", (sensor_id,)
        ).fetchone()
        if row:
            location_label = row['location_label']
        else:
            # Unknown/stale sensor_id (e.g. sensor was deleted) — don't let it
            # violate the FK constraint, just log it without a sensor link.
            app.logger.warning(f'Unknown sensor_id {sensor_id} in temperature POST, storing without link')
            sensor_id = None
 
    conn.execute(
        "INSERT INTO temperature_logs (temperature, humidity, sensor_id) VALUES (?, ?, ?)",
        (temperature, humidity, sensor_id)
    )
    conn.commit()
    conn.close()
 
    # Threshold alerts — fire in background so Pi gets fast response
    def _check_and_notify(temp, loc):
        loc_str = f' [{loc}]' if loc else ''
        if temp >= 75:
            send_push_to_all(
                f'🔴 CRITICAL FIRE RISK{loc_str}',
                f'Temperature is {temp}°C — immediate action required!',
                url='/dashboard', tag=f'temp-critical-{sensor_id}'
            )
        elif temp >= 65:
            send_push_to_all(
                f'🟠 Possible Fire Warning{loc_str}',
                f'Temperature is {temp}°C — check the inventory room.',
                url='/dashboard', tag=f'temp-fire-{sensor_id}'
            )
        elif temp >= 50:
            send_push_to_all(
                f'🟡 High Temperature Warning{loc_str}',
                f'Temperature is {temp}°C — monitor closely.',
                url='/dashboard', tag=f'temp-warning-{sensor_id}'
            )
 
    threading.Thread(target=_check_and_notify, args=(temperature, location_label), daemon=True).start()
 
    return jsonify({'ok': True})


@app.route('/api/temperature/latest')
@login_required
def api_temperature_latest():
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM temperature_logs ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'No data yet'}), 404
    return jsonify(dict(row))


@app.route('/api/push/test-sync', methods=['POST'])
@login_required
def api_push_test_sync():
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return jsonify({'error': 'VAPID keys missing'}), 500

    ec_pem = (
        "-----BEGIN EC PRIVATE KEY-----\n" +
        VAPID_PRIVATE_KEY +
        "\n-----END EC PRIVATE KEY-----"
    )

    conn = get_db()
    subs = conn.execute("SELECT * FROM push_subscriptions").fetchall()
    conn.close()

    results = []
    payload = json.dumps({'title': '🔴 Sync Test', 'body': 'sync test', 'url': '/dashboard', 'tag': 'test'})

    for row in subs:
        try:
            sub = json.loads(row['subscription_json'])
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=ec_pem,
                vapid_claims={'sub': f'mailto:{VAPID_CLAIMS_EMAIL}'}
            )
            results.append({'id': row['id'], 'status': 201})
        except WebPushException as e:
            resp = e.response
            status = resp.status_code if resp is not None else None
            body = resp.text if resp is not None else str(e)
            results.append({'id': row['id'], 'error': str(e), 'status': status, 'body': body})
        except Exception as e:
            results.append({'id': row['id'], 'error': str(e)})

    return jsonify({'results': results})
@app.route('/api/temperature/history')
@login_required
def api_temperature_history():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM temperature_logs ORDER BY timestamp DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/push/test', methods=['POST'])
@login_required
def api_push_test():
    try:
        send_push_to_all(
            '🔴 Test Alert',
            'This is a test notification from Edge2!',
            url='/dashboard',
            tag='test'
        )
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─── ROUTES: TEMPERATURE SENSORS ─────────────────────────────────────────────
 
@app.route('/temp-sensors')
@login_required
@role_required('admin', 'storekeeper')
def temp_sensors():
    conn = get_db()
    sensors_raw = conn.execute(
        "SELECT * FROM temp_sensors ORDER BY i2c_channel"
    ).fetchall()
    all_columns = conn.execute(
        "SELECT id, column_name FROM columns ORDER BY column_name"
    ).fetchall()
 
    sensors = []
    for s in sensors_raw:
        assigned = conn.execute(
            """SELECT c.id, c.column_name FROM temp_sensor_columns tsc
               JOIN columns c ON c.id = tsc.column_id
               WHERE tsc.sensor_id=?""",
            (s['id'],)
        ).fetchall()
        d = dict(s)
        d['assigned_columns'] = [r['column_name'] for r in assigned]
        d['column_ids']       = [r['id'] for r in assigned]
        sensors.append(d)
 
    conn.close()
    return render_template('temp_sensors.html', sensors=sensors, all_columns=all_columns)
 
 
@app.route('/temp-sensors/save', methods=['POST'])
@login_required
@role_required('admin', 'storekeeper')
def temp_sensor_save():
    sensor_id     = request.form.get('sensor_id', '').strip()
    name          = request.form.get('name', '').strip()
    location_label = request.form.get('location_label', '').strip()
    i2c_channel   = request.form.get('i2c_channel', '0').strip()
    column_ids    = request.form.getlist('column_ids')  # list of str IDs
 
    if not name or not location_label:
        return redirect(url_for('temp_sensors'))
 
    try:
        i2c_channel = int(i2c_channel)
        column_ids  = [int(c) for c in column_ids]
    except ValueError:
        return redirect(url_for('temp_sensors'))
 
    conn = get_db()
    try:
        if sensor_id:
            conn.execute(
                "UPDATE temp_sensors SET name=?, location_label=?, i2c_channel=? WHERE id=?",
                (name, location_label, i2c_channel, int(sensor_id))
            )
            conn.execute(
                "DELETE FROM temp_sensor_columns WHERE sensor_id=?", (int(sensor_id),)
            )
            sid = int(sensor_id)
        else:
            cur = conn.execute(
                "INSERT INTO temp_sensors (name, location_label, i2c_channel) VALUES (?,?,?)",
                (name, location_label, i2c_channel)
            )
            sid = cur.lastrowid
 
        for cid in column_ids:
            conn.execute(
                "INSERT OR IGNORE INTO temp_sensor_columns (sensor_id, column_id) VALUES (?,?)",
                (sid, cid)
            )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()
 
    return redirect(url_for('temp_sensors'))
 
 
@app.route('/temp-sensors/delete/<int:sid>', methods=['POST'])
@login_required
@role_required('admin')
def temp_sensor_delete(sid):
    conn = get_db()
    conn.execute("DELETE FROM temp_sensors WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    return redirect(url_for('temp_sensors'))
 
 
# ─── API: TEMPERATURE BY SENSOR ───────────────────────────────────────────────
 
@app.route('/api/temperature/by-sensor')
@login_required
def api_temperature_by_sensor():
    """Return latest reading per sensor."""
    conn = get_db()
    rows = conn.execute("""
        SELECT tl.sensor_id, tl.temperature, tl.humidity, tl.timestamp,
               ts.name as sensor_name, ts.location_label
        FROM temperature_logs tl
        LEFT JOIN temp_sensors ts ON ts.id = tl.sensor_id
        WHERE tl.id IN (
            SELECT MAX(id) FROM temperature_logs
            WHERE sensor_id IS NOT NULL
            GROUP BY sensor_id
        )
        ORDER BY tl.sensor_id
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# ─── ERROR HANDLERS ───────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

@app.route('/api/debug/vapid')
@login_required
def api_debug_vapid():
    return jsonify({
        'private_key_length': len(VAPID_PRIVATE_KEY),
        'public_key_length': len(VAPID_PUBLIC_KEY),
        'private_start': VAPID_PRIVATE_KEY[:20] if VAPID_PRIVATE_KEY else 'EMPTY',
        'subs_count': get_db().execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
    })
@app.route('/api/debug/push-direct', methods=['GET'])
@login_required  
def api_debug_push_direct():
    conn = get_db()
    row = conn.execute("SELECT subscription_json FROM push_subscriptions LIMIT 1").fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'no subs'})
    
    sub = json.loads(row['subscription_json'])
    
    # Write key to temp file
    key_body = "\n".join(textwrap.wrap(VAPID_PRIVATE_KEY, 64))
    pem = f"-----BEGIN EC PRIVATE KEY-----\n{key_body}\n-----END EC PRIVATE KEY-----\n"
    
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False)
    tmp.write(pem)
    tmp.close()
    
    try:
        webpush(
            subscription_info=sub,
            data=json.dumps({'title': 'test', 'body': 'test', 'url': '/', 'tag': 'test'}),
            vapid_private_key=tmp.name,  # pass file path instead of string
            vapid_claims={'sub': f'mailto:{VAPID_CLAIMS_EMAIL}'}
        )
        return jsonify({'ok': True, 'pem_written': pem[:60]})
    except WebPushException as e:
        resp = e.response
        return jsonify({
            'error': str(e),
            'status': resp.status_code if resp else None,
            'body': resp.text if resp else None
        })
    except Exception as e:
        return jsonify({'error': str(e)})
    finally:
        os.unlink(tmp.name)
    
@app.route('/api/debug/clear-subs', methods=['POST'])
@login_required
def api_clear_subs():
    conn = get_db()
    conn.execute("DELETE FROM push_subscriptions")
    conn.commit()
    conn.close()
    return jsonify({'ok': True})
# ─── MAIN ─────────────────────────────────────────────────────────────────────
init_db()
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)