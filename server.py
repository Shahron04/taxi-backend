import os
import sqlite3
import time
import random
import threading
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template_string, session, redirect

app = Flask(__name__)
app.secret_key = "TAXI3042_SECRET"

# ---------- Конфигурация ----------
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "taxi3042")
DB_PATH = "taxi.db"

# ---------- База данных ----------
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS drivers (
            car_number TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            status TEXT DEFAULT 'offline',
            balance INTEGER DEFAULT 50000,
            pin TEXT NOT NULL,
            last_seen REAL DEFAULT 0,
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT NOT NULL,
            from_address TEXT NOT NULL,
            to_address TEXT NOT NULL,
            distance TEXT DEFAULT '—',
            price INTEGER NOT NULL,
            client TEXT DEFAULT 'Диспетчер',
            status TEXT DEFAULT 'pending',
            created_at REAL,
            completed_at REAL,
            cancelled_at REAL,
            cancel_reason TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT NOT NULL,
            stars INTEGER NOT NULL,
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT NOT NULL,
            amount INTEGER NOT NULL,
            type TEXT NOT NULL,
            comment TEXT,
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT NOT NULL,
            start_time REAL NOT NULL,
            end_time REAL,
            revenue INTEGER DEFAULT 0,
            orders_count INTEGER DEFAULT 0
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            car_number TEXT,
            details TEXT,
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS tariffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone TEXT UNIQUE NOT NULL,
            name TEXT,
            base_fare INTEGER DEFAULT 5000,
            rate_per_km INTEGER DEFAULT 2800,
            wait_rate INTEGER DEFAULT 500
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS pending_pins (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            car_number TEXT NOT NULL,
            phone TEXT NOT NULL,
            pin TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS balance_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT NOT NULL,
            text TEXT NOT NULL,
            sender TEXT DEFAULT 'admin',
            created_at REAL
        )''')
        # Добавим дефолтные тарифы
        default = [
            ("city_day", "🏙️ Город день", 5000, 2800, 500),
            ("city_night", "🌙 Город ночь", 7000, 3500, 700),
            ("suburb_day", "🌳 Загород день", 5000, 3000, 500),
            ("suburb_night", "🌙 Загород ночь", 7000, 3800, 700),
            ("airport", "✈️ Аэропорт", 10000, 3500, 500),
            ("vokzal", "🚉 Вокзал", 8000, 3000, 500)
        ]
        for zone, name, base, km, wait in default:
            c.execute('INSERT OR IGNORE INTO tariffs (zone, name, base_fare, rate_per_km, wait_rate) VALUES (?,?,?,?,?)',
                      (zone, name, base, km, wait))
        conn.commit()
    print("✅ База данных инициализирована")

init_db()

# ---------- Вспомогательные функции ----------
def log_action(action, car_number=None, details=None):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO logs (action, car_number, details, created_at) VALUES (?,?,?,?)",
                         (action, car_number, details, time.time()))
    except:
        pass

def generate_pin():
    return f"{random.randint(1000, 9999):04d}"

def format_time(ts):
    if not ts: return "—"
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")

def fmt_money(n):
    return f"{n:,}".replace(",", " ") + " сум"

# ---------- Фоновые задачи ----------
def background_worker():
    while True:
        try:
            now = time.time()
            with sqlite3.connect(DB_PATH) as conn:
                # Авто-офлайн если нет heartbeat > 2 мин
                conn.execute("UPDATE drivers SET status='offline' WHERE status IN ('free','busy') AND last_seen < ?", (now - 120,))
                # Отмена старых заказов (60 мин)
                conn.execute("UPDATE orders SET status='cancelled', cancelled_at=?, cancel_reason='Таймаут' WHERE status IN ('pending','accepted') AND created_at < ?", (now, now - 3600))
                conn.commit()
        except:
            pass
        time.sleep(30)

threading.Thread(target=background_worker, daemon=True).start()

# ---------- Декоратор для админки ----------
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

# ---------- Страницы админа ----------
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TAXI 3042 — Вход</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}body{background:#0a0a0a;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}
.box{background:#111;border:1px solid #222;border-radius:16px;padding:40px;width:360px;text-align:center}
.logo{font-size:28px;font-weight:900;color:#f5c518;margin-bottom:8px}
.sub{color:#666;font-size:13px;margin-bottom:32px}
input{width:100%;background:#1a1a1a;border:1px solid #333;border-radius:10px;padding:14px;color:#fff;margin-bottom:12px}
button{width:100%;background:#f5c518;color:#000;border:none;border-radius:10px;padding:14px;font-weight:700;cursor:pointer}
button:hover{background:#e6b800}
.error{background:#ff000020;border:1px solid #ff000050;border-radius:8px;padding:10px;margin-bottom:16px;color:#ff6b6b}
</style>
</head>
<body>
<div class="box">
  <div class="logo">🚕 TAXI 3042</div>
  <div class="sub">XAZARASP — Диспетчерская</div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="POST">
    <input type="text" name="login" placeholder="Логин" required>
    <input type="password" name="password" placeholder="Пароль" required>
    <button type="submit">Войти</button>
  </form>
</div>
</body>
</html>
"""

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("login") == ADMIN_LOGIN and request.form.get("password") == ADMIN_PASS:
            session["admin"] = True
            return redirect("/")
        error = "Неверный логин или пароль"
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ---------- API для водителей ----------
@app.route("/api/driver/register", methods=["POST"])
def driver_register():
    data = request.json
    name = data.get("name", "").strip()
    car = data.get("car_number", "").strip().upper()
    phone = data.get("phone", "").strip()
    if not (name and car and phone):
        return jsonify({"success": False, "error": "Заполните все поля"})
    with sqlite3.connect(DB_PATH) as conn:
        # Проверка на существующего водителя
        if conn.execute("SELECT car_number FROM drivers WHERE car_number=?", (car,)).fetchone():
            return jsonify({"success": False, "error": "Водитель уже зарегистрирован"})
        if conn.execute("SELECT id FROM pending_pins WHERE car_number=? AND status='pending'", (car,)).fetchone():
            return jsonify({"success": False, "error": "Заявка уже отправлена"})
        pin = generate_pin()
        req_id = f"REQ_{car}_{int(time.time())}"
        conn.execute("INSERT INTO pending_pins (id, name, car_number, phone, pin, status, created_at) VALUES (?,?,?,?,?,?,?)",
                     (req_id, name, car, phone, pin, "pending", time.time()))
        conn.commit()
    log_action("register", car, f"Заявка от {name}")
    return jsonify({"success": True, "message": "Заявка отправлена"})

@app.route("/api/driver/login", methods=["POST"])
def driver_login():
    data = request.json
    car = data.get("car_number", "").strip().upper()
    pin = data.get("pin", "").strip()
    with sqlite3.connect(DB_PATH) as conn:
        driver = conn.execute("SELECT * FROM drivers WHERE car_number=? AND pin=?", (car, pin)).fetchone()
        if not driver:
            return jsonify({"success": False, "error": "Неверные данные"})
    return jsonify({"success": True, "name": driver["name"], "car_number": driver["car_number"],
                    "phone": driver["phone"], "balance": driver["balance"], "status": driver["status"]})

@app.route("/api/driver/heartbeat", methods=["POST"])
def driver_heartbeat():
    car = request.json.get("car_number", "").strip().upper()
    if not car: return jsonify({"success": False})
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE drivers SET last_seen=? WHERE car_number=?", (time.time(), car))
        conn.commit()
    return jsonify({"success": True})

@app.route("/api/driver/status", methods=["POST"])
def driver_status():
    data = request.json
    car = data.get("car_number", "").strip().upper()
    status = data.get("status")  # 'free' or 'offline'
    if status not in ("free", "offline"):
        return jsonify({"success": False})
    with sqlite3.connect(DB_PATH) as conn:
        if status == "free":
            conn.execute("UPDATE drivers SET status='free', last_seen=? WHERE car_number=?", (time.time(), car))
            # Открыть смену если нет активной
            if not conn.execute("SELECT id FROM shifts WHERE car_number=? AND end_time IS NULL", (car,)).fetchone():
                conn.execute("INSERT INTO shifts (car_number, start_time) VALUES (?,?)", (car, time.time()))
        else:
            conn.execute("UPDATE drivers SET status='offline' WHERE car_number=?", (car,))
            conn.execute("UPDATE shifts SET end_time=? WHERE car_number=? AND end_time IS NULL", (time.time(), car))
        conn.commit()
    log_action(f"status_{status}", car)
    return jsonify({"success": True})

@app.route("/api/driver/order", methods=["GET"])
def driver_get_order():
    car = request.args.get("car_number", "").strip().upper()
    with sqlite3.connect(DB_PATH) as conn:
        order = conn.execute("SELECT * FROM orders WHERE car_number=? AND status IN ('pending','accepted') ORDER BY created_at DESC LIMIT 1", (car,)).fetchone()
        if order:
            return jsonify({"success": True, "order": dict(order)})
    return jsonify({"success": True, "order": None})

@app.route("/api/driver/order/accept", methods=["POST"])
def driver_accept():
    data = request.json
    car = data.get("car_number", "").strip().upper()
    order_id = data.get("order_id")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE orders SET status='accepted' WHERE id=? AND car_number=?", (order_id, car))
        conn.execute("UPDATE drivers SET status='busy' WHERE car_number=?", (car,))
        conn.commit()
    return jsonify({"success": True})

@app.route("/api/driver/order/complete", methods=["POST"])
def driver_complete():
    data = request.json
    car = data.get("car_number", "").strip().upper()
    order_id = data.get("order_id")
    with sqlite3.connect(DB_PATH) as conn:
        order = conn.execute("SELECT price FROM orders WHERE id=? AND car_number=?", (order_id, car)).fetchone()
        if not order:
            return jsonify({"success": False})
        price = order["price"]
        conn.execute("UPDATE orders SET status='completed', completed_at=? WHERE id=?", (time.time(), order_id))
        conn.execute("UPDATE drivers SET status='free', balance=balance+? WHERE car_number=?", (price, car))
        conn.execute("UPDATE shifts SET revenue=revenue+?, orders_count=orders_count+1 WHERE car_number=? AND end_time IS NULL", (price, car))
        conn.execute("INSERT INTO transactions (car_number, amount, type, comment, created_at) VALUES (?,?,?,?,?)",
                     (car, price, "income", f"Заказ #{order_id}", time.time()))
        conn.commit()
    log_action("order_completed", car, f"Заказ #{order_id} +{price}")
    return jsonify({"success": True})

@app.route("/api/driver/order/cancel", methods=["POST"])
def driver_cancel():
    data = request.json
    car = data.get("car_number", "").strip().upper()
    order_id = data.get("order_id")
    reason = data.get("reason", "Отменено водителем")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE orders SET status='cancelled', cancelled_at=?, cancel_reason=? WHERE id=? AND car_number=?", (time.time(), reason, order_id, car))
        conn.execute("UPDATE drivers SET status='free' WHERE car_number=?", (car,))
        conn.commit()
    log_action("order_cancelled", car, f"Заказ #{order_id}")
    return jsonify({"success": True})

@app.route("/api/driver/balance", methods=["GET"])
def driver_balance():
    car = request.args.get("car_number", "").strip().upper()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT balance FROM drivers WHERE car_number=?", (car,)).fetchone()
        if row:
            return jsonify({"success": True, "balance": row["balance"]})
    return jsonify({"success": False})

@app.route("/api/driver/balance/request", methods=["POST"])
def driver_balance_request():
    data = request.json
    car = data.get("car_number", "").strip().upper()
    amount = int(data.get("amount", 0))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO balance_requests (car_number, amount, status, created_at) VALUES (?,?,?,?)",
                     (car, amount, "pending", time.time()))
        conn.commit()
    log_action("balance_request", car, f"Сумма {amount}")
    return jsonify({"success": True})

@app.route("/api/driver/chat", methods=["GET"])
def driver_chat_get():
    car = request.args.get("car_number", "").strip().upper()
    with sqlite3.connect(DB_PATH) as conn:
        msgs = conn.execute("SELECT * FROM chat_messages WHERE car_number=? ORDER BY created_at ASC LIMIT 50", (car,)).fetchall()
        return jsonify({"success": True, "messages": [{"text": m["text"], "sender": m["sender"], "created_at": format_time(m["created_at"])} for m in msgs]})

@app.route("/api/driver/chat", methods=["POST"])
def driver_chat_send():
    data = request.json
    car = data.get("car_number", "").strip().upper()
    text = data.get("text", "").strip()
    if not text: return jsonify({"success": False})
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO chat_messages (car_number, text, sender, created_at) VALUES (?,?,?,?)",
                     (car, text, "driver", time.time()))
        conn.commit()
    return jsonify({"success": True})

# ---------- API для админа ----------
@app.route("/api/admin/dashboard", methods=["GET"])
@admin_required
def admin_dashboard():
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
        online = conn.execute("SELECT COUNT(*) FROM drivers WHERE status IN ('free','busy')").fetchone()[0]
        free = conn.execute("SELECT COUNT(*) FROM drivers WHERE status='free'").fetchone()[0]
        busy = conn.execute("SELECT COUNT(*) FROM drivers WHERE status='busy'").fetchone()[0]
        pending_pins = conn.execute("SELECT COUNT(*) FROM pending_pins WHERE status='pending'").fetchone()[0]
        active_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE status IN ('pending','accepted')").fetchone()[0]
        today_start = datetime.now().replace(hour=0, minute=0, second=0).timestamp()
        today_revenue = conn.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed' AND completed_at>?", (today_start,)).fetchone()[0]
        today_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE status='completed' AND completed_at>?", (today_start,)).fetchone()[0]

        online_drivers = conn.execute("""
            SELECT d.car_number, d.name, d.status, d.balance, COALESCE(AVG(r.stars),0) as rating, COUNT(DISTINCT o.id) as orders_count
            FROM drivers d LEFT JOIN ratings r ON r.car_number=d.car_number LEFT JOIN orders o ON o.car_number=d.car_number AND o.status='completed'
            WHERE d.status IN ('free','busy') GROUP BY d.car_number
        """).fetchall()
        active_orders_list = conn.execute("SELECT * FROM orders WHERE status IN ('pending','accepted') ORDER BY created_at DESC LIMIT 20").fetchall()

        return jsonify({
            "success": True,
            "stats": {
                "total": total, "online": online, "free": free, "busy": busy,
                "pending_pins": pending_pins, "active_orders": active_orders,
                "today_revenue": today_revenue, "today_orders": today_orders
            },
            "online_drivers": [dict(d) for d in online_drivers],
            "active_orders": [dict(o) for o in active_orders_list]
        })

@app.route("/api/admin/drivers", methods=["GET"])
@admin_required
def admin_drivers():
    with sqlite3.connect(DB_PATH) as conn:
        drivers = conn.execute("""
            SELECT d.*, COALESCE(AVG(r.stars),0) as rating, COUNT(DISTINCT o.id) as orders_count
            FROM drivers d LEFT JOIN ratings r ON r.car_number=d.car_number LEFT JOIN orders o ON o.car_number=d.car_number AND o.status='completed'
            GROUP BY d.car_number ORDER BY d.created_at DESC
        """).fetchall()
        pending = conn.execute("SELECT * FROM pending_pins WHERE status='pending' ORDER BY created_at DESC").fetchall()
        return jsonify({
            "success": True,
            "drivers": [dict(d) for d in drivers],
            "pending": [dict(p) for p in pending]
        })

@app.route("/api/admin/approve/<req_id>", methods=["POST"])
@admin_required
def admin_approve(req_id):
    with sqlite3.connect(DB_PATH) as conn:
        req = conn.execute("SELECT * FROM pending_pins WHERE id=? AND status='pending'", (req_id,)).fetchone()
        if not req:
            return jsonify({"success": False})
        conn.execute("INSERT OR IGNORE INTO drivers (car_number, name, phone, status, balance, pin, last_seen, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (req["car_number"], req["name"], req["phone"], "offline", 50000, req["pin"], 0, time.time()))
        conn.execute("UPDATE pending_pins SET status='approved' WHERE id=?", (req_id,))
        conn.commit()
        log_action("driver_approved", req["car_number"])
        return jsonify({"success": True, "pin": req["pin"]})

@app.route("/api/admin/reject/<req_id>", methods=["POST"])
@admin_required
def admin_reject(req_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE pending_pins SET status='rejected' WHERE id=?", (req_id,))
        conn.commit()
    return jsonify({"success": True})

@app.route("/api/admin/order", methods=["POST"])
@admin_required
def admin_create_order():
    data = request.json
    car = data.get("car_number", "").strip().upper()
    from_addr = data.get("from_address", "").strip()
    to_addr = data.get("to_address", "").strip()
    price = int(data.get("price", 0))
    client = data.get("client", "Диспетчер")
    distance = data.get("distance", "—")
    if not (car and from_addr and to_addr and price):
        return jsonify({"success": False, "error": "Не все поля заполнены"})
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO orders (car_number, from_address, to_address, distance, price, client, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (car, from_addr, to_addr, distance, price, client, "pending", time.time()))
        conn.execute("UPDATE drivers SET status='busy' WHERE car_number=?", (car,))
        conn.commit()
    log_action("order_created", car, f"{from_addr}→{to_addr} {price}")
    return jsonify({"success": True})

@app.route("/api/admin/orders", methods=["GET"])
@admin_required
def admin_orders():
    with sqlite3.connect(DB_PATH) as conn:
        orders = conn.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 200").fetchall()
        return jsonify({"success": True, "orders": [dict(o) for o in orders]})

@app.route("/api/admin/shifts", methods=["GET"])
@admin_required
def admin_shifts():
    with sqlite3.connect(DB_PATH) as conn:
        shifts = conn.execute("SELECT s.*, d.name FROM shifts s LEFT JOIN drivers d ON d.car_number=s.car_number ORDER BY s.start_time DESC LIMIT 200").fetchall()
        return jsonify({"success": True, "shifts": [dict(s) for s in shifts]})

@app.route("/api/admin/ratings", methods=["GET"])
@admin_required
def admin_ratings():
    with sqlite3.connect(DB_PATH) as conn:
        ratings = conn.execute("""
            SELECT d.car_number, d.name, COALESCE(AVG(r.stars),0) as avg_rating, COUNT(r.id) as count
            FROM drivers d LEFT JOIN ratings r ON r.car_number=d.car_number GROUP BY d.car_number ORDER BY avg_rating DESC
        """).fetchall()
        return jsonify({"success": True, "ratings": [dict(r) for r in ratings]})

@app.route("/api/admin/finances", methods=["GET"])
@admin_required
def admin_finances():
    with sqlite3.connect(DB_PATH) as conn:
        balance_requests = conn.execute("SELECT * FROM balance_requests WHERE status='pending' ORDER BY created_at DESC").fetchall()
        transactions = conn.execute("SELECT * FROM transactions ORDER BY created_at DESC LIMIT 100").fetchall()
        return jsonify({
            "success": True,
            "balance_requests": [dict(b) for b in balance_requests],
            "transactions": [dict(t) for t in transactions]
        })

@app.route("/api/admin/balance/approve/<int:req_id>", methods=["POST"])
@admin_required
def admin_balance_approve(req_id):
    with sqlite3.connect(DB_PATH) as conn:
        req = conn.execute("SELECT * FROM balance_requests WHERE id=? AND status='pending'", (req_id,)).fetchone()
        if not req:
            return jsonify({"success": False})
        conn.execute("UPDATE drivers SET balance=balance+? WHERE car_number=?", (req["amount"], req["car_number"]))
        conn.execute("UPDATE balance_requests SET status='approved' WHERE id=?", (req_id,))
        conn.execute("INSERT INTO transactions (car_number, amount, type, comment, created_at) VALUES (?,?,?,?,?)",
                     (req["car_number"], req["amount"], "topup", "Пополнение одобрено", time.time()))
        conn.commit()
        log_action("balance_approve", req["car_number"], f"+{req['amount']}")
        return jsonify({"success": True})

@app.route("/api/admin/chat/<car_number>", methods=["GET"])
@admin_required
def admin_chat_get(car_number):
    with sqlite3.connect(DB_PATH) as conn:
        messages = conn.execute("SELECT * FROM chat_messages WHERE car_number=? ORDER BY created_at ASC", (car_number,)).fetchall()
        return jsonify({"success": True, "messages": [{"text": m["text"], "sender": m["sender"], "created_at": format_time(m["created_at"])} for m in messages]})

@app.route("/api/admin/chat/<car_number>", methods=["POST"])
@admin_required
def admin_chat_send(car_number):
    text = request.json.get("text", "").strip()
    if not text:
        return jsonify({"success": False})
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO chat_messages (car_number, text, sender, created_at) VALUES (?,?,?,?)",
                     (car_number, text, "admin", time.time()))
        conn.commit()
    return jsonify({"success": True})

@app.route("/api/admin/tariffs", methods=["GET"])
@admin_required
def admin_tariffs():
    with sqlite3.connect(DB_PATH) as conn:
        tariffs = conn.execute("SELECT * FROM tariffs").fetchall()
        return jsonify({"success": True, "tariffs": [dict(t) for t in tariffs]})

@app.route("/api/admin/tariffs/<int:tariff_id>", methods=["PUT"])
@admin_required
def admin_tariff_update(tariff_id):
    data = request.json
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE tariffs SET base_fare=?, rate_per_km=?, wait_rate=? WHERE id=?", 
                     (data.get("base_fare"), data.get("rate_per_km"), data.get("wait_rate"), tariff_id))
        conn.commit()
    return jsonify({"success": True})

@app.route("/api/admin/driver/<car_number>", methods=["DELETE"])
@admin_required
def admin_delete_driver(car_number):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM drivers WHERE car_number=?", (car_number,))
        conn.commit()
    log_action("driver_deleted", car_number)
    return jsonify({"success": True})

# ---------- Главная страница админки (встроенный HTML) ----------
ADMIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAXI 3042 XAZARASP</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#fff;font-family:'Segoe UI',sans-serif}
.nav{background:#111;border-bottom:1px solid #1e1e1e;padding:0 20px;display:flex;align-items:center;gap:0;position:sticky;top:0;z-index:100}
.nav-logo{font-size:16px;font-weight:900;color:#f5c518;padding:16px 24px 16px 0;border-right:1px solid #222;margin-right:16px}
.nav-tabs{display:flex;gap:0;flex:1;overflow-x:auto}
.tab{padding:16px 20px;cursor:pointer;color:#888;font-size:13px;font-weight:600;border-bottom:3px solid transparent;white-space:nowrap}
.tab:hover{color:#fff}
.tab.active{color:#f5c518;border-bottom-color:#f5c518}
.nav-right{display:flex;align-items:center;gap:16px;margin-left:auto}
.time{color:#888;font-family:monospace;font-size:13px}
.live{color:#4caf50;font-size:12px;display:flex;align-items:center;gap:4px}
.live-dot{width:8px;height:8px;background:#4caf50;border-radius:50%;animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.btn-logout{background:#1a1a1a;border:1px solid #333;color:#888;padding:6px 12px;border-radius:8px;text-decoration:none;font-size:12px}
.btn-logout:hover{color:#fff}
.content{padding:24px;max-width:1400px;margin:0 auto}
.page{display:none}.page.active{display:block}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:24px}
.card{background:#111;border:1px solid #1e1e1e;border-radius:12px;padding:16px;text-align:center}
.card-val{font-size:26px;font-weight:900}
.card-lbl{font-size:11px;color:#666;margin-top:4px}
.c-green{color:#4caf50}.c-yellow{color:#f5c518}.c-blue{color:#2196f3}.c-purple{color:#9c27b0}.c-red{color:#f44336}
.section{background:#111;border:1px solid #1e1e1e;border-radius:12px;padding:20px;margin-bottom:20px}
.section-title{font-size:14px;font-weight:700;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:#666;text-align:left;padding:10px 12px;border-bottom:1px solid #1e1e1e}
td{padding:10px 12px;border-bottom:1px solid #1a1a1a}
tr:hover td{background:#151515}
.badge{padding:4px 10px;border-radius:20px;font-size:11px;font-weight:700}
.badge-free{background:#4caf5020;color:#4caf50}
.badge-busy{background:#ff980020;color:#ff9800}
.badge-offline{background:#66666620;color:#aaa}
.badge-pending{background:#2196f320;color:#2196f3}
.badge-completed{background:#4caf5020;color:#4caf50}
.badge-cancelled{background:#f4433620;color:#f44336}
.btn{padding:6px 12px;border-radius:6px;border:none;cursor:pointer;font-size:11px;font-weight:600}
.btn-primary{background:#f5c518;color:#000}
.btn-primary:hover{background:#e6b800}
.btn-success{background:#4caf50;color:#fff}
.btn-danger{background:#f44336;color:#fff}
.btn-secondary{background:#1e1e1e;color:#aaa;border:1px solid #333}
.btn-sm{padding:4px 8px}
.form-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:12px}
.form-group{display:flex;flex-direction:column;gap:4px}
.form-group label{font-size:11px;color:#888}
.form-group input,.form-group select{background:#1a1a1a;border:1px solid #333;border-radius:6px;padding:8px 10px;color:#fff;font-size:13px}
.pin-card{background:#111;border:2px solid #f5c51830;border-radius:12px;padding:16px;margin-bottom:12px;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px}
.pin-code{background:#f5c51820;border:1px solid #f5c518;border-radius:8px;padding:6px 16px;font-size:20px;font-family:monospace;color:#f5c518;letter-spacing:3px}
.chat-list{display:flex;flex-direction:column;gap:8px}
.chat-item{background:#1a1a1a;border:1px solid #222;border-radius:8px;padding:10px 12px;cursor:pointer}
.chat-item:hover{border-color:#f5c518}
.chat-messages{height:300px;overflow-y:auto;background:#0a0a0a;border-radius:8px;padding:12px;margin-bottom:12px}
.msg{max-width:70%;padding:8px 12px;border-radius:12px;margin-bottom:8px}
.msg-admin{background:#f5c51820;border:1px solid #f5c51840;align-self:flex-end;margin-left:auto}
.msg-driver{background:#1e1e1e;border:1px solid #333}
.chat-input-row{display:flex;gap:8px}
.chat-input{flex:1;background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:10px;color:#fff}
.modal{display:none;position:fixed;inset:0;background:#000000cc;z-index:1000;align-items:center;justify-content:center}
.modal.open{display:flex}
.modal-box{background:#111;border:1px solid #333;border-radius:16px;padding:24px;width:500px;max-width:90vw}
.modal-title{font-size:18px;font-weight:700;margin-bottom:20px}
.modal-actions{display:flex;gap:12px;justify-content:flex-end;margin-top:20px}
.no-data{text-align:center;padding:40px;color:#444}
</style>
</head>
<body>
<div class="nav">
  <div class="nav-logo">🚕 TAXI 3042 <span style="color:#666;font-size:11px">XAZARASP</span></div>
  <div class="nav-tabs">
    <div class="tab active" data-page="dashboard">📊 Дашборд</div>
    <div class="tab" data-page="orders">📋 Заказы</div>
    <div class="tab" data-page="drivers">🚗 Водители</div>
    <div class="tab" data-page="pins">🔑 Заявки</div>
    <div class="tab" data-page="shifts">⏱ Смены</div>
    <div class="tab" data-page="ratings">⭐ Рейтинг</div>
    <div class="tab" data-page="chat">💬 Чат</div>
    <div class="tab" data-page="finances">💰 Финансы</div>
    <div class="tab" data-page="settings">⚙️ Настройки</div>
  </div>
  <div class="nav-right">
    <span class="time" id="clock"></span>
    <span class="live"><span class="live-dot"></span>Live</span>
    <a href="/logout" class="btn-logout">🚪 Выход</a>
  </div>
</div>
<div class="content">
  <!-- Страницы -->
  <div class="page active" id="page-dashboard">...</div>
  <div class="page" id="page-orders">...</div>
  <div class="page" id="page-drivers">...</div>
  <div class="page" id="page-pins">...</div>
  <div class="page" id="page-shifts">...</div>
  <div class="page" id="page-ratings">...</div>
  <div class="page" id="page-chat">...</div>
  <div class="page" id="page-finances">...</div>
  <div class="page" id="page-settings">...</div>
</div>
<div class="modal" id="modal-order"><div class="modal-box"><div class="modal-title">⚡ Новый заказ</div>
  <div class="form-row"><div class="form-group"><label>Водитель (авто)</label><input id="o-car" placeholder="01A123BC"></div>
  <div class="form-group"><label>Клиент</label><input id="o-client" value="Диспетчер"></div></div>
  <div class="form-row"><div class="form-group"><label>Откуда</label><input id="o-from"></div>
  <div class="form-group"><label>Куда</label><input id="o-to"></div></div>
  <div class="form-row"><div class="form-group"><label>Расстояние</label><input id="o-dist" placeholder="—"></div>
  <div class="form-group"><label>Цена (сум)</label><input id="o-price" type="number"></div></div>
  <div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal('modal-order')">Отмена</button><button class="btn btn-primary" onclick="createOrder()">Создать</button></div>
</div></div>
<script>
// Глобальные переменные
let currentPage = "dashboard";
let currentChatCar = null;
let tariffData = [];

// Вспомогательные функции
function fmtMoney(n) { if (!n) return "0 сум"; return n.toLocaleString("ru") + " сум"; }
function formatTime(ts) { if (!ts) return "—"; let d=new Date(ts*1000); return d.toLocaleString(); }
function statusLabel(s) { return {free:"Свободен", busy:"На заказе", offline:"Офлайн"}[s]||s; }
function orderStatus(s) { return {pending:"Ожидает", accepted:"Принят", completed:"Завершён", cancelled:"Отменён"}[s]||s; }

// Навигация
function showPage(page) {
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.getElementById("page-"+page).classList.add("active");
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelector(`.tab[data-page="${page}"]`).classList.add("active");
  currentPage = page;
  if (page === "dashboard") loadDashboard();
  else if (page === "orders") loadOrders();
  else if (page === "drivers") loadDrivers();
  else if (page === "pins") loadPins();
  else if (page === "shifts") loadShifts();
  else if (page === "ratings") loadRatings();
  else if (page === "chat") loadChatList();
  else if (page === "finances") loadFinances();
  else if (page === "settings") loadSettings();
}
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => showPage(tab.dataset.page));
});

// Часы
function updateClock() { document.getElementById("clock").innerText = new Date().toLocaleTimeString(); }
setInterval(updateClock, 1000); updateClock();

// Модалки
function openModal(id) { document.getElementById(id).classList.add("open"); }
function closeModal(id) { document.getElementById(id).classList.remove("open"); }

// ========== ДАШБОРД ==========
async function loadDashboard() {
  try {
    let r = await fetch("/api/admin/dashboard");
    let d = await r.json();
    if (!d.success) return;
    let s = d.stats;
    document.getElementById("page-dashboard").innerHTML = `
      <div class="cards">
        <div class="card"><div class="card-val c-green">${s.online}</div><div class="card-lbl">На линии</div></div>
        <div class="card"><div class="card-val c-green">${s.free}</div><div class="card-lbl">Свободны</div></div>
        <div class="card"><div class="card-val c-yellow">${s.busy}</div><div class="card-lbl">На заказе</div></div>
        <div class="card"><div class="card-val c-blue">${s.pending_pins}</div><div class="card-lbl">Заявок</div></div>
        <div class="card"><div class="card-val c-purple">${s.total}</div><div class="card-lbl">Всего водит.</div></div>
        <div class="card"><div class="card-val c-yellow">${s.active_orders}</div><div class="card-lbl">Акт.заказов</div></div>
        <div class="card"><div class="card-val c-green">${fmtMoney(s.today_revenue)}</div><div class="card-lbl">Выручка</div></div>
        <div class="card"><div class="card-val c-blue">${s.today_orders}</div><div class="card-lbl">Сегодня</div></div>
      </div>
      <div class="quick-actions" style="margin-bottom:20px">
        <button class="btn btn-primary" onclick="openModal('modal-order')">⚡ Новый заказ</button>
        <button class="btn btn-secondary" onclick="showPage('chat')">💬 Чат</button>
        <button class="btn btn-secondary" onclick="showPage('pins')">🔑 Заявки</button>
        <button class="btn btn-secondary" onclick="loadDashboard()">🔄 Обновить</button>
      </div>
      <div class="section"><div class="section-title">🚗 Водители онлайн</div>
        <div class="table-wrap"><table><thead><tr><th>Авто</th><th>Имя</th><th>Статус</th><th>Баланс</th><th>Рейтинг</th><th>Заказов</th><th>Действия</th></tr></thead>
        <tbody>${d.online_drivers.map(dr => `<tr>
          <td><b>${dr.car_number}</b></td><td>${dr.name}</td>
          <td><span class="badge badge-${dr.status}">${statusLabel(dr.status)}</span></td>
          <td>${fmtMoney(dr.balance)}</td><td>⭐ ${dr.rating}</td><td>${dr.orders_count}</td>
          <td><button class="btn btn-primary btn-sm" onclick="quickOrder('${dr.car_number}')">📋 Заказ</button>
          <button class="btn btn-secondary btn-sm" onclick="openChat('${dr.car_number}')">💬</button></td>
        </tr>`).join("")}</tbody></table></div>
      </div>
      <div class="section"><div class="section-title">📋 Активные заказы</div>
        <div class="table-wrap"><table><thead><tr><th>#</th><th>Водитель</th><th>Откуда</th><th>Куда</th><th>Цена</th><th>Клиент</th><th>Время</th></tr></thead>
        <tbody>${d.active_orders.map(o => `<tr>
          <td>${o.id}</td><td>${o.car_number}</td><td>${o.from_address}</td><td>${o.to_address}</td>
          <td>${fmtMoney(o.price)}</td><td>${o.client}</td><td>${formatTime(o.created_at)}</td>
        </tr>`).join("")}</tbody></table></div>
      </div>
    `;
  } catch(e) { console.error(e); }
}

function quickOrder(car) {
  document.getElementById("o-car").value = car;
  openModal("modal-order");
}

async function createOrder() {
  let car = document.getElementById("o-car").value.trim();
  let from = document.getElementById("o-from").value.trim();
  let to = document.getElementById("o-to").value.trim();
  let price = parseInt(document.getElementById("o-price").value);
  let client = document.getElementById("o-client").value.trim();
  let dist = document.getElementById("o-dist").value.trim();
  if (!car || !from || !to || !price) { alert("Заполните все поля"); return; }
  let r = await fetch("/api/admin/order", {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({car_number:car, from_address:from, to_address:to, price, client, distance:dist})
  });
  let d = await r.json();
  if (d.success) { alert("✅ Заказ создан"); closeModal("modal-order"); loadDashboard(); }
  else alert("Ошибка: " + (d.error||""));
}

// ========== ЗАКАЗЫ ==========
async function loadOrders() {
  let r = await fetch("/api/admin/orders");
  let d = await r.json();
  if (!d.success) return;
  document.getElementById("page-orders").innerHTML = `
    <div class="section"><div class="section-title">📋 Все заказы <button class="btn btn-primary btn-sm" onclick="openModal('modal-order')">+ Новый</button></div>
    <div class="table-wrap"><table><thead><tr><th>#</th><th>Авто</th><th>Откуда</th><th>Куда</th><th>Расст.</th><th>Цена</th><th>Клиент</th><th>Статус</th><th>Время</th></tr></thead>
    <tbody>${d.orders.map(o => `<tr>
      <td>${o.id}</td><td>${o.car_number}</td><td>${o.from_address}</td><td>${o.to_address}</td>
      <td>${o.distance}</td><td>${fmtMoney(o.price)}</td><td>${o.client}</td>
      <td><span class="badge badge-${o.status}">${orderStatus(o.status)}</span></td><td>${formatTime(o.created_at)}</td>
    </tr>`).join("")}</tbody></table></div></div>
  `;
}

// ========== ВОДИТЕЛИ ==========
async function loadDrivers() {
  let r = await fetch("/api/admin/drivers");
  let d = await r.json();
  if (!d.success) return;
  document.getElementById("page-drivers").innerHTML = `
    <div class="section"><div class="section-title">🚗 Все водители</div>
    <div class="table-wrap"><table><thead><tr><th>Авто</th><th>Имя</th><th>Телефон</th><th>Статус</th><th>Баланс</th><th>ПИН</th><th>Рейтинг</th><th>Заказов</th><th>Регистрация</th><th>Действия</th></tr></thead>
    <tbody>${d.drivers.map(dr => `<tr>
      <td><b>${dr.car_number}</b></td><td>${dr.name}</td><td>${dr.phone}</td>
      <td><span class="badge badge-${dr.status}">${statusLabel(dr.status)}</span></td>
      <td>${fmtMoney(dr.balance)}</td><td><code style="background:#f5c51820;padding:2px 8px;border-radius:6px">${dr.pin}</code></td>
      <td>⭐ ${dr.rating}</td><td>${dr.orders_count}</td><td>${formatTime(dr.created_at)}</td>
      <td><button class="btn btn-secondary btn-sm" onclick="openChat('${dr.car_number}')">💬</button>
      <button class="btn btn-danger btn-sm" onclick="deleteDriver('${dr.car_number}')">🗑</button></td>
    </tr>`).join("")}</tbody></table></div></div>
  `;
}

async function deleteDriver(car) {
  if (!confirm("Удалить водителя "+car+"?")) return;
  let r = await fetch("/api/admin/driver/"+car, {method:"DELETE"});
  let d = await r.json();
  if (d.success) { alert("Удалён"); loadDrivers(); }
}

// ========== ЗАЯВКИ ==========
async function loadPins() {
  let r = await fetch("/api/admin/drivers");
  let d = await r.json();
  if (!d.success) return;
  document.getElementById("page-pins").innerHTML = `
    <div class="section"><div class="section-title">🔑 Заявки на регистрацию</div>
    ${d.pending.length ? d.pending.map(p => `
      <div class="pin-card">
        <div><b>${p.name}</b><br><span style="color:#888">${p.car_number} | ${p.phone}</span><br>${formatTime(p.created_at)}</div>
        <div class="pin-code">${p.pin}</div>
        <div><button class="btn btn-success" onclick="approvePin('${p.id}')">✅ Одобрить</button>
        <button class="btn btn-danger" onclick="rejectPin('${p.id}')">❌ Отклонить</button></div>
      </div>
    `).join("") : "<div class='no-data'>Нет новых заявок</div>"}
  `;
}

async function approvePin(id) {
  if (!confirm("Одобрить водителя?")) return;
  let r = await fetch("/api/admin/approve/"+id, {method:"POST"});
  let d = await r.json();
  if (d.success) {
    alert(`✅ Водитель одобрен! ПИН: ${d.pin}\nСообщите водителю этот ПИН.`);
    loadPins(); loadDashboard();
  } else alert("Ошибка");
}

async function rejectPin(id) {
  if (!confirm("Отклонить?")) return;
  await fetch("/api/admin/reject/"+id, {method:"POST"});
  loadPins();
}

// ========== СМЕНЫ ==========
async function loadShifts() {
  let r = await fetch("/api/admin/shifts");
  let d = await r.json();
  document.getElementById("page-shifts").innerHTML = `
    <div class="section"><div class="section-title">⏱ Смены водителей</div>
    <div class="table-wrap"><table><thead><tr><th>Авто</th><th>Водитель</th><th>Начало</th><th>Конец</th><th>Выручка</th><th>Заказов</th></tr></thead>
    <tbody>${d.shifts.map(s => `<tr>
      <td>${s.car_number}</td><td>${s.name||"—"}</td><td>${formatTime(s.start_time)}</td>
      <td>${formatTime(s.end_time)}</td><td>${fmtMoney(s.revenue)}</td><td>${s.orders_count}</td>
    </tr>`).join("")}</tbody></table></div></div>
  `;
}

// ========== РЕЙТИНГ ==========
async function loadRatings() {
  let r = await fetch("/api/admin/ratings");
  let d = await r.json();
  document.getElementById("page-ratings").innerHTML = `
    <div class="section"><div class="section-title">⭐ Рейтинг водителей</div>
    <div class="table-wrap"><table><thead><tr><th>#</th><th>Авто</th><th>Водитель</th><th>Рейтинг</th><th>Оценок</th></tr></thead>
    <tbody>${d.ratings.map((r,i) => `<tr>
      <td>${i+1}</td><td>${r.car_number}</td><td>${r.name}</td><td>${'⭐'.repeat(Math.round(r.avg_rating))} ${r.avg_rating}</td><td>${r.count}</td>
    </tr>`).join("")}</tbody></table></div></div>
  `;
}

// ========== ЧАТ ==========
async function loadChatList() {
  let r = await fetch("/api/admin/drivers");
  let d = await r.json();
  let html = `<div class="chat-list">${d.drivers.map(dr => `
    <div class="chat-item" onclick="openChat('${dr.car_number}')">
      <span>${dr.car_number} — ${dr.name}</span> <span class="badge badge-${dr.status}">${statusLabel(dr.status)}</span>
    </div>`).join("")}</div><div id="chat-area" style="margin-top:16px"><div class="no-data">Выберите водителя</div></div>`;
  document.getElementById("page-chat").innerHTML = html;
}

async function openChat(car) {
  currentChatCar = car;
  let r = await fetch("/api/admin/chat/"+car);
  let d = await r.json();
  let msgs = d.messages.map(m => `<div class="msg msg-${m.sender}"><b>${m.sender==="admin"?"Диспетчер":"Водитель"}:</b> ${m.text}<div style="font-size:10px;color:#666">${m.created_at}</div></div>`).join("");
  document.getElementById("page-chat").innerHTML = `
    <div class="chat-list">${document.querySelector(".chat-list")?.innerHTML || ""}</div>
    <div style="margin-top:16px">
      <div class="section-title">💬 Чат с ${car}</div>
      <div class="chat-messages" id="chat-messages">${msgs || "<div class='no-data'>Нет сообщений</div>"}</div>
      <div class="chat-input-row"><input class="chat-input" id="chat-input" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sendChatMsg()">
      <button class="btn btn-primary" onclick="sendChatMsg()">Отправить</button></div>
    </div>
  `;
  document.getElementById("chat-messages").scrollTop = 9999;
}

async function sendChatMsg() {
  let input = document.getElementById("chat-input");
  let text = input.value.trim();
  if (!text || !currentChatCar) return;
  await fetch("/api/admin/chat/"+currentChatCar, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({text})});
  input.value = "";
  openChat(currentChatCar);
}

// ========== ФИНАНСЫ ==========
async function loadFinances() {
  let r = await fetch("/api/admin/finances");
  let d = await r.json();
  document.getElementById("page-finances").innerHTML = `
    <div class="section"><div class="section-title">💳 Заявки на пополнение</div>
    <div class="table-wrap"><table><thead><tr><th>Авто</th><th>Сумма</th><th>Время</th><th>Действия</th></tr></thead>
    <tbody>${d.balance_requests.map(req => `<tr>
      <td>${req.car_number}</td><td>${fmtMoney(req.amount)}</td><td>${formatTime(req.created_at)}</td>
      <td><button class="btn btn-success btn-sm" onclick="approveBalance(${req.id})">✅ Одобрить</button></td>
    </tr>`).join("")}</tbody></table></div></div>
    <div class="section"><div class="section-title">📊 Транзакции</div>
    <div class="table-wrap"><table><thead><tr><th>Авто</th><th>Сумма</th><th>Тип</th><th>Комментарий</th><th>Время</th></tr></thead>
    <tbody>${d.transactions.map(t => `<tr>
      <td>${t.car_number}</td><td style="color:${t.type==='income'?'#4caf50':'#2196f3'}">${fmtMoney(t.amount)}</td>
      <td>${t.type==='income'?'Доход':'Пополнение'}</td><td>${t.comment||"—"}</td><td>${formatTime(t.created_at)}</td>
    </tr>`).join("")}</tbody></table></div></div>
  `;
}

async function approveBalance(id) {
  if (!confirm("Одобрить пополнение?")) return;
  let r = await fetch("/api/admin/balance/approve/"+id, {method:"POST"});
  let d = await r.json();
  if (d.success) { alert("✅ Баланс пополнен"); loadFinances(); }
}

// ========== НАСТРОЙКИ ==========
async function loadSettings() {
  let r = await fetch("/api/admin/tariffs");
  let d = await r.json();
  tariffData = d.tariffs;
  document.getElementById("page-settings").innerHTML = `
    <div class="section"><div class="section-title">⚙️ Тарифы</div>
    ${tariffData.map(t => `
      <div style="background:#1a1a1a;border-radius:10px;padding:16px;margin-bottom:12px">
        <div style="font-weight:700;margin-bottom:12px">${t.name}</div>
        <div class="form-row">
          <div class="form-group"><label>Посадка (сум)</label><input type="number" id="base_${t.id}" value="${t.base_fare}"></div>
          <div class="form-group"><label>За км (сум)</label><input type="number" id="km_${t.id}" value="${t.rate_per_km}"></div>
          <div class="form-group"><label>Ожидание/мин</label><input type="number" id="wait_${t.id}" value="${t.wait_rate}"></div>
          <div class="form-group" style="justify-content:flex-end"><label>&nbsp;</label><button class="btn btn-primary" onclick="saveTariff(${t.id})">Сохранить</button></div>
        </div>
      </div>
    `).join("")}</div>
  `;
}

async function saveTariff(id) {
  let base = parseInt(document.getElementById(`base_${id}`).value);
  let km = parseInt(document.getElementById(`km_${id}`).value);
  let wait = parseInt(document.getElementById(`wait_${id}`).value);
  let r = await fetch(`/api/admin/tariffs/${id}`, {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({base_fare:base, rate_per_km:km, wait_rate:wait})});
  let d = await r.json();
  if (d.success) alert("✅ Тариф сохранён");
}

// Старт
loadDashboard();
</script>
</body>
</html>
"""

@app.route("/")
@admin_required
def index():
    return render_template_string(ADMIN_HTML)

# ---------- Запуск ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚕 Сервер TAXI 3042 запущен на порту {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
