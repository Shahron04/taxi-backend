from flask import Flask, request, jsonify, render_template_string, session, redirect, Response
from datetime import datetime
from functools import wraps
import random
import time
import threading
import os
import json
import sqlite3
import logging
import re

# ═══════════════════════════════════════
#  ЛОГИРОВАНИЕ
# ═══════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("taxi_server.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════
#  ПРИЛОЖЕНИЕ
# ═══════════════════════════════════════
app = Flask(__name__)
app.secret_key = "TAXI3042_SECRET_KEY_XAZARASP"
db_lock = threading.Lock()
DB_PATH = "taxi.db"
ADMIN_LOGIN    = os.environ.get("ADMIN_LOGIN",    "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "taxi3042")

# ═══════════════════════════════════════
#  БАЗА ДАННЫХ
# ═══════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS drivers (
            car_number TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            phone      TEXT NOT NULL,
            status     TEXT DEFAULT 'offline',
            balance    INTEGER DEFAULT 50000,
            pin        TEXT NOT NULL,
            last_seen  REAL DEFAULT 0,
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS orders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number    TEXT NOT NULL,
            from_address  TEXT NOT NULL,
            to_address    TEXT NOT NULL,
            distance      TEXT DEFAULT '—',
            price         INTEGER NOT NULL,
            client        TEXT DEFAULT 'Диспетчер',
            status        TEXT DEFAULT 'pending',
            created_at    REAL,
            completed_at  REAL,
            cancelled_at  REAL,
            cancel_reason TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS ratings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT NOT NULL,
            stars      INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS transactions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT NOT NULL,
            amount     INTEGER NOT NULL,
            type       TEXT NOT NULL,
            comment    TEXT,
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS shifts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number   TEXT NOT NULL,
            start_time   REAL NOT NULL,
            end_time     REAL,
            revenue      INTEGER DEFAULT 0,
            orders_count INTEGER DEFAULT 0
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            action     TEXT NOT NULL,
            car_number TEXT,
            details    TEXT,
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS tariffs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            zone        TEXT UNIQUE NOT NULL,
            time_type   TEXT,
            name        TEXT,
            base_fare   INTEGER DEFAULT 5000,
            rate_per_km INTEGER DEFAULT 2800,
            wait_rate   INTEGER DEFAULT 500
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS pending_pins (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            car_number TEXT NOT NULL,
            phone      TEXT NOT NULL,
            pin        TEXT NOT NULL,
            status     TEXT DEFAULT 'pending',
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS balance_requests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT NOT NULL,
            amount     INTEGER NOT NULL,
            status     TEXT DEFAULT 'pending',
            created_at REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT NOT NULL,
            text       TEXT NOT NULL,
            sender     TEXT DEFAULT 'admin',
            created_at REAL
        )''')
        default_tariffs = [
            ("city_day",     "day",   "🏙️ Город день",   5000, 2800, 500),
            ("city_night",   "night", "🌙 Город ночь",    7000, 3500, 700),
            ("suburb_day",   "day",   "🌳 Загород день",  5000, 3000, 500),
            ("suburb_night", "night", "🌙 Загород ночь",  7000, 3800, 700),
            ("airport",      "day",   "✈️ Аэропорт",     10000, 3500, 500),
            ("vokzal",       "day",   "🚉 Вокзал",         8000, 3000, 500),
        ]
        for zone, time_type, name, base, km, wait in default_tariffs:
            c.execute('''INSERT OR IGNORE INTO tariffs
                (zone,time_type,name,base_fare,rate_per_km,wait_rate)
                VALUES (?,?,?,?,?,?)''',
                (zone, time_type, name, base, km, wait))
        conn.commit()
        conn.close()
        logger.info("✅ База данных готова")

# ═══════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════
def log_action(action, car_number=None, details=None):
    try:
        with db_lock:
            conn = get_db()
            conn.execute(
                "INSERT INTO logs (action,car_number,details,created_at) VALUES (?,?,?,?)",
                (action, car_number, details, time.time())
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Ошибка лога: {e}")

def generate_pin():
    return str(random.randint(1000, 9999))

def format_time(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")

def format_money(amount):
    return f"{amount:,}".replace(",", " ") + " сум"

# ═══════════════════════════════════════
#  АВТОРИЗАЦИЯ АДМИНА
# ═══════════════════════════════════════
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if (request.form.get("login") == ADMIN_LOGIN and
                request.form.get("password") == ADMIN_PASSWORD):
            session["admin"] = True
            return redirect("/")
        error = "Неверный логин или пароль"
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ═══════════════════════════════════════
#  ФОНОВЫЕ ЗАДАЧИ
# ═══════════════════════════════════════
def background_tasks():
    while True:
        try:
            now = time.time()
            with db_lock:
                conn = get_db()
                # Офлайн если нет heartbeat 2 минуты
                conn.execute("""
                    UPDATE drivers SET status='offline'
                    WHERE status IN ('free','busy')
                    AND last_seen < ?
                """, (now - 120,))
                # Отменить заказы старше 60 минут
                conn.execute("""
                    UPDATE orders SET status='cancelled',
                    cancelled_at=?, cancel_reason='Таймаут'
                    WHERE status IN ('pending','accepted')
                    AND created_at < ?
                """, (now, now - 3600))
                conn.commit()
                conn.close()
        except Exception as e:
            logger.error(f"Фоновая задача: {e}")
        time.sleep(30)

# ═══════════════════════════════════════
#  API ДЛЯ ВОДИТЕЛЕЙ
# ═══════════════════════════════════════

# Регистрация (заявка)
@app.route("/api/driver/register", methods=["POST"])
def driver_register():
    data = request.get_json() or {}
    name       = data.get("name", "").strip()
    car_number = data.get("car_number", "").strip().upper()
    phone      = data.get("phone", "").strip()

    if not all([name, car_number, phone]):
        return jsonify({"success": False, "error": "Заполните все поля"}), 400

    try:
        with db_lock:
            conn = get_db()
            # Проверка — уже зарегистрирован?
            existing = conn.execute(
                "SELECT car_number FROM drivers WHERE car_number=?",
                (car_number,)
            ).fetchone()
            if existing:
                conn.close()
                return jsonify({"success": False, "error": "Водитель уже зарегистрирован"}), 400

            # Проверка — уже есть заявка?
            pending = conn.execute(
                "SELECT id FROM pending_pins WHERE car_number=? AND status='pending'",
                (car_number,)
            ).fetchone()
            if pending:
                conn.close()
                return jsonify({"success": False, "error": "Заявка уже отправлена, ожидайте"}), 400

            pin = generate_pin()
            req_id = f"REQ_{car_number}_{int(time.time())}"
            conn.execute(
                "INSERT INTO pending_pins (id,name,car_number,phone,pin,status,created_at) VALUES (?,?,?,?,?,?,?)",
                (req_id, name, car_number, phone, pin, "pending", time.time())
            )
            conn.commit()
            conn.close()

        log_action("register_request", car_number, f"Заявка от {name}")
        logger.info(f"📋 Новая заявка: {name} ({car_number})")
        return jsonify({"success": True, "message": "Заявка отправлена! Ожидайте одобрения администратора."})

    except Exception as e:
        logger.error(f"Ошибка регистрации: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

# Вход водителя
@app.route("/api/driver/login", methods=["POST"])
def driver_login():
    data = request.get_json() or {}
    car_number = data.get("car_number", "").strip().upper()
    pin        = data.get("pin", "").strip()

    if not all([car_number, pin]):
        return jsonify({"success": False, "error": "Введите номер авто и ПИН"}), 400

    try:
        with db_lock:
            conn = get_db()
            driver = conn.execute(
                "SELECT * FROM drivers WHERE car_number=? AND pin=?",
                (car_number, pin)
            ).fetchone()
            conn.close()

        if not driver:
            return jsonify({"success": False, "error": "Неверный номер авто или ПИН"}), 401

        log_action("driver_login", car_number)
        return jsonify({
            "success":    True,
            "name":       driver["name"],
            "car_number": driver["car_number"],
            "phone":      driver["phone"],
            "balance":    driver["balance"],
            "status":     driver["status"]
        })
    except Exception as e:
        logger.error(f"Ошибка входа: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

# Heartbeat (онлайн-пинг)
@app.route("/api/driver/heartbeat", methods=["POST"])
def driver_heartbeat():
    data = request.get_json() or {}
    car_number = data.get("car_number", "").strip().upper()
    if not car_number:
        return jsonify({"success": False}), 400
    try:
        with db_lock:
            conn = get_db()
            conn.execute(
                "UPDATE drivers SET last_seen=? WHERE car_number=?",
                (time.time(), car_number)
            )
            conn.commit()
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Heartbeat ошибка: {e}")
        return jsonify({"success": False}), 500

# Онлайн/Офлай��
@app.route("/api/driver/status", methods=["POST"])
def driver_status():
    data = request.get_json() or {}
    car_number = data.get("car_number", "").strip().upper()
    status     = data.get("status", "")

    if status not in ("free", "offline"):
        return jsonify({"success": False, "error": "Неверный статус"}), 400

    try:
        with db_lock:
            conn = get_db()
            if status == "free":
                conn.execute(
                    "UPDATE drivers SET status='free', last_seen=? WHERE car_number=?",
                    (time.time(), car_number)
                )
                # Открыть смену если нет активной
                active_shift = conn.execute(
                    "SELECT id FROM shifts WHERE car_number=? AND end_time IS NULL",
                    (car_number,)
                ).fetchone()
                if not active_shift:
                    conn.execute(
                        "INSERT INTO shifts (car_number,start_time) VALUES (?,?)",
                        (car_number, time.time())
                    )
            else:
                conn.execute(
                    "UPDATE drivers SET status='offline' WHERE car_number=?",
                    (car_number,)
                )
                # Закрыть смену
                conn.execute("""
                    UPDATE shifts SET end_time=?
                    WHERE car_number=? AND end_time IS NULL
                """, (time.time(), car_number))
            conn.commit()
            conn.close()

        log_action(f"status_{status}", car_number)
        return jsonify({"success": True, "status": status})
    except Exception as e:
        logger.error(f"Статус ошибка: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

# Получить активный заказ
@app.route("/api/driver/order", methods=["GET"])
def driver_get_order():
    car_number = request.args.get("car_number", "").strip().upper()
    if not car_number:
        return jsonify({"success": False}), 400
    try:
        with db_lock:
            conn = get_db()
            order = conn.execute("""
                SELECT * FROM orders
                WHERE car_number=? AND status IN ('pending','accepted')
                ORDER BY created_at DESC LIMIT 1
            """, (car_number,)).fetchone()
            conn.close()
        if order:
            return jsonify({
                "success":      True,
                "order": {
                    "id":           order["id"],
                    "from_address": order["from_address"],
                    "to_address":   order["to_address"],
                    "distance":     order["distance"],
                    "price":        order["price"],
                    "client":       order["client"],
                    "status":       order["status"]
                }
            })
        return jsonify({"success": True, "order": None})
    except Exception as e:
        logger.error(f"Получение заказа: {e}")
        return jsonify({"success": False}), 500

# Принять заказ
@app.route("/api/driver/order/accept", methods=["POST"])
def driver_accept_order():
    data = request.get_json() or {}
    car_number = data.get("car_number", "").strip().upper()
    order_id   = data.get("order_id")
    try:
        with db_lock:
            conn = get_db()
            conn.execute(
                "UPDATE orders SET status='accepted' WHERE id=? AND car_number=?",
                (order_id, car_number)
            )
            conn.execute(
                "UPDATE drivers SET status='busy' WHERE car_number=?",
                (car_number,)
            )
            conn.commit()
            conn.close()
        log_action("order_accepted", car_number, f"Заказ #{order_id}")
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Принятие заказа: {e}")
        return jsonify({"success": False}), 500

# Завершить заказ
@app.route("/api/driver/order/complete", methods=["POST"])
def driver_complete_order():
    data = request.get_json() or {}
    car_number = data.get("car_number", "").strip().upper()
    order_id   = data.get("order_id")
    try:
        with db_lock:
            conn = get_db()
            order = conn.execute(
                "SELECT price FROM orders WHERE id=? AND car_number=?",
                (order_id, car_number)
            ).fetchone()
            if not order:
                conn.close()
                return jsonify({"success": False, "error": "Заказ не найден"}), 404

            price = order["price"]
            conn.execute("""
                UPDATE orders SET status='completed', completed_at=?
                WHERE id=? AND car_number=?
            """, (time.time(), order_id, car_number))
            conn.execute(
                "UPDATE drivers SET status='free', balance=balance+? WHERE car_number=?",
                (price, car_number)
            )
            conn.execute("""
                UPDATE shifts SET revenue=revenue+?, orders_count=orders_count+1
                WHERE car_number=? AND end_time IS NULL
            """, (price, car_number))
            conn.execute(
                "INSERT INTO transactions (car_number,amount,type,comment,created_at) VALUES (?,?,?,?,?)",
                (car_number, price, "income", f"Заказ #{order_id}", time.time())
            )
            conn.commit()
            conn.close()
        log_action("order_completed", car_number, f"Заказ #{order_id} +{price}")
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Завершение заказа: {e}")
        return jsonify({"success": False}), 500

# Отменить заказ (водителем)
@app.route("/api/driver/order/cancel", methods=["POST"])
def driver_cancel_order():
    data = request.get_json() or {}
    car_number = data.get("car_number", "").strip().upper()
    order_id   = data.get("order_id")
    reason     = data.get("reason", "Отменено водителем")
    try:
        with db_lock:
            conn = get_db()
            conn.execute("""
                UPDATE orders SET status='cancelled',
                cancelled_at=?, cancel_reason=?
                WHERE id=? AND car_number=?
            """, (time.time(), reason, order_id, car_number))
            conn.execute(
                "UPDATE drivers SET status='free' WHERE car_number=?",
                (car_number,)
            )
            conn.commit()
            conn.close()
        log_action("order_cancelled", car_number, f"Заказ #{order_id}: {reason}")
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Отмена заказа: {e}")
        return jsonify({"success": False}), 500

# Баланс водителя
@app.route("/api/driver/balance", methods=["GET"])
def driver_balance():
    car_number = request.args.get("car_number", "").strip().upper()
    try:
        with db_lock:
            conn = get_db()
            driver = conn.execute(
                "SELECT balance FROM drivers WHERE car_number=?",
                (car_number,)
            ).fetchone()
            conn.close()
        if driver:
            return jsonify({"success": True, "balance": driver["balance"]})
        return jsonify({"success": False}), 404
    except Exception as e:
        logger.error(f"Баланс: {e}")
        return jsonify({"success": False}), 500

# Запрос пополнения баланса
@app.route("/api/driver/balance/request", methods=["POST"])
def driver_balance_request():
    data = request.get_json() or {}
    car_number = data.get("car_number", "").strip().upper()
    amount     = data.get("amount", 0)
    try:
        with db_lock:
            conn = get_db()
            conn.execute(
                "INSERT INTO balance_requests (car_number,amount,status,created_at) VALUES (?,?,?,?)",
                (car_number, amount, "pending", time.time())
            )
            conn.commit()
            conn.close()
        return jsonify({"success": True, "message": "Заявка отправлена"})
    except Exception as e:
        logger.error(f"Запрос баланса: {e}")
        return jsonify({"success": False}), 500

# Чат — получить сообщения
@app.route("/api/driver/chat", methods=["GET"])
def driver_chat_get():
    car_number = request.args.get("car_number", "").strip().upper()
    try:
        with db_lock:
            conn = get_db()
            messages = conn.execute("""
                SELECT * FROM chat_messages
                WHERE car_number=?
                ORDER BY created_at DESC LIMIT 50
            """, (car_number,)).fetchall()
            conn.close()
        return jsonify({
            "success": True,
            "messages": [{
                "id":         m["id"],
                "text":       m["text"],
                "sender":     m["sender"],
                "created_at": format_time(m["created_at"])
            } for m in reversed(messages)]
        })
    except Exception as e:
        logger.error(f"Чат получение: {e}")
        return jsonify({"success": False}), 500

# Чат — отправить сообщение
@app.route("/api/driver/chat", methods=["POST"])
def driver_chat_send():
    data = request.get_json() or {}
    car_number = data.get("car_number", "").strip().upper()
    text       = data.get("text", "").strip()
    if not text:
        return jsonify({"success": False}), 400
    try:
        with db_lock:
            conn = get_db()
            conn.execute(
                "INSERT INTO chat_messages (car_number,text,sender,created_at) VALUES (?,?,?,?)",
                (car_number, text, "driver", time.time())
            )
            conn.commit()
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Чат отправка: {e}")
        return jsonify({"success": False}), 500

# ═══════════════════════════════════════
#  API ДЛЯ АДМИНА
# ═══════════════════════════════════════

# Одобрить заявку водителя
@app.route("/api/admin/approve/<req_id>", methods=["POST"])
@login_required
def admin_approve(req_id):
    try:
        with db_lock:
            conn = get_db()
            req = conn.execute(
                "SELECT * FROM pending_pins WHERE id=? AND status='pending'",
                (req_id,)
            ).fetchone()
            if not req:
                conn.close()
                return jsonify({"success": False, "error": "Заявка не найдена"}), 404

            conn.execute("""
                INSERT OR IGNORE INTO drivers
                (car_number,name,phone,status,balance,pin,last_seen,created_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (req["car_number"], req["name"], req["phone"],
                  "offline", 50000, req["pin"], 0, time.time()))
            conn.execute(
                "UPDATE pending_pins SET status='approved' WHERE id=?",
                (req_id,)
            )
            conn.commit()
            conn.close()

        log_action("driver_approved", req["car_number"])
        logger.info(f"✅ Водитель одобрен: {req['car_number']} ПИН: {req['pin']}")
        return jsonify({"success": True, "pin": req["pin"]})
    except Exception as e:
        logger.error(f"Одобрение: {e}")
        return jsonify({"success": False}), 500

# Отклонить заявку
@app.route("/api/admin/reject/<req_id>", methods=["POST"])
@login_required
def admin_reject(req_id):
    try:
        with db_lock:
            conn = get_db()
            conn.execute(
                "UPDATE pending_pins SET status='rejected' WHERE id=?",
                (req_id,)
            )
            conn.commit()
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Отклонение: {e}")
        return jsonify({"success": False}), 500

# Создать заказ
@app.route("/api/admin/order", methods=["POST"])
@login_required
def admin_create_order():
    data = request.get_json() or {}
    car_number   = data.get("car_number", "").strip().upper()
    from_address = data.get("from_address", "").strip()
    to_address   = data.get("to_address", "").strip()
    price        = data.get("price", 0)
    client       = data.get("client", "Диспетчер")
    distance     = data.get("distance", "—")

    if not all([car_number, from_address, to_address, price]):
        return jsonify({"success": False, "error": "Заполните все поля"}), 400

    try:
        with db_lock:
            conn = get_db()
            conn.execute("""
                INSERT INTO orders
                (car_number,from_address,to_address,distance,price,client,status,created_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (car_number, from_address, to_address, distance,
                  price, client, "pending", time.time()))
            conn.execute(
                "UPDATE drivers SET status='busy' WHERE car_number=?",
                (car_number,)
            )
            conn.commit()
            conn.close()
        log_action("order_created", car_number, f"{from_address}→{to_address} {price}сум")
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Создание заказа: {e}")
        return jsonify({"success": False}), 500

# Одобрить пополнение баланса
@app.route("/api/admin/balance/approve/<int:req_id>", methods=["POST"])
@login_required
def admin_balance_approve(req_id):
    try:
        with db_lock:
            conn = get_db()
            req = conn.execute(
                "SELECT * FROM balance_requests WHERE id=? AND status='pending'",
                (req_id,)
            ).fetchone()
            if not req:
                conn.close()
                return jsonify({"success": False}), 404
            conn.execute(
                "UPDATE drivers SET balance=balance+? WHERE car_number=?",
                (req["amount"], req["car_number"])
            )
            conn.execute(
                "UPDATE balance_requests SET status='approved' WHERE id=?",
                (req_id,)
            )
            conn.execute(
                "INSERT INTO transactions (car_number,amount,type,comment,created_at) VALUES (?,?,?,?,?)",
                (req["car_number"], req["amount"], "topup", "Пополнение админом", time.time())
            )
            conn.commit()
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Пополнение баланса: {e}")
        return jsonify({"success": False}), 500

# Данные для дашборда
@app.route("/api/admin/dashboard", methods=["GET"])
@login_required
def admin_dashboard():
    try:
        with db_lock:
            conn = get_db()
            total    = conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
            online   = conn.execute("SELECT COUNT(*) FROM drivers WHERE status IN ('free','busy')").fetchone()[0]
            free     = conn.execute("SELECT COUNT(*) FROM drivers WHERE status='free'").fetchone()[0]
            busy     = conn.execute("SELECT COUNT(*) FROM drivers WHERE status='busy'").fetchone()[0]
            pending_pins_count = conn.execute(
                "SELECT COUNT(*) FROM pending_pins WHERE status='pending'"
            ).fetchone()[0]
            active_orders = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE status IN ('pending','accepted')"
            ).fetchone()[0]
            today_start = datetime.now().replace(hour=0,minute=0,second=0).timestamp()
            today_revenue = conn.execute(
                "SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed' AND completed_at>?",
                (today_start,)
            ).fetchone()[0]
            today_orders = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE status='completed' AND completed_at>?",
                (today_start,)
            ).fetchone()[0]

            online_drivers = conn.execute("""
                SELECT d.*, 
                       COALESCE(AVG(r.stars),0) as rating,
                       COUNT(DISTINCT o.id) as orders_count
                FROM drivers d
                LEFT JOIN ratings r ON r.car_number=d.car_number
                LEFT JOIN orders o ON o.car_number=d.car_number AND o.status='completed'
                WHERE d.status IN ('free','busy')
                GROUP BY d.car_number
            """).fetchall()

            active_orders_list = conn.execute("""
                SELECT * FROM orders
                WHERE status IN ('pending','accepted')
                ORDER BY created_at DESC
            """).fetchall()

            conn.close()

        return jsonify({
            "success": True,
            "stats": {
                "total":         total,
                "online":        online,
                "free":          free,
                "busy":          busy,
                "pending_pins":  pending_pins_count,
                "active_orders": active_orders,
                "today_revenue": today_revenue,
                "today_orders":  today_orders
            },
            "online_drivers": [{
                "car_number": d["car_number"],
                "name":       d["name"],
                "status":     d["status"],
                "balance":    d["balance"],
                "rating":     round(d["rating"], 1),
                "orders":     d["orders_count"]
            } for d in online_drivers],
            "active_orders": [{
                "id":           o["id"],
                "car_number":   o["car_number"],
                "from_address": o["from_address"],
                "to_address":   o["to_address"],
                "price":        o["price"],
                "client":       o["client"],
                "status":       o["status"],
                "created_at":   format_time(o["created_at"])
            } for o in active_orders_list]
        })
    except Exception as e:
        logger.error(f"Дашборд: {e}")
        return jsonify({"success": False}), 500

# Список водителей
@app.route("/api/admin/drivers", methods=["GET"])
@login_required
def admin_drivers():
    try:
        with db_lock:
            conn = get_db()
            drivers = conn.execute("""
                SELECT d.*,
                       COALESCE(AVG(r.stars),0) as rating,
                       COUNT(DISTINCT o.id) as orders_count
                FROM drivers d
                LEFT JOIN ratings r ON r.car_number=d.car_number
                LEFT JOIN orders o ON o.car_number=d.car_number AND o.status='completed'
                GROUP BY d.car_number
                ORDER BY d.created_at DESC
            """).fetchall()
            pending = conn.execute(
                "SELECT * FROM pending_pins WHERE status='pending' ORDER BY created_at DESC"
            ).fetchall()
            conn.close()

        return jsonify({
            "success": True,
            "drivers": [{
                "car_number": d["car_number"],
                "name":       d["name"],
                "phone":      d["phone"],
                "status":     d["status"],
                "balance":    d["balance"],
                "pin":        d["pin"],
                "rating":     round(d["rating"], 1),
                "orders":     d["orders_count"],
                "created_at": format_time(d["created_at"])
            } for d in drivers],
            "pending": [{
                "id":         p["id"],
                "name":       p["name"],
                "car_number": p["car_number"],
                "phone":      p["phone"],
                "pin":        p["pin"],
                "created_at": format_time(p["created_at"])
            } for p in pending]
        })
    except Exception as e:
        logger.error(f"Водители: {e}")
        return jsonify({"success": False}), 500

# Список заказов
@app.route("/api/admin/orders", methods=["GET"])
@login_required
def admin_orders():
    try:
        with db_lock:
            conn = get_db()
            orders = conn.execute("""
                SELECT * FROM orders
                ORDER BY created_at DESC LIMIT 100
            """).fetchall()
            conn.close()
        return jsonify({
            "success": True,
            "orders": [{
                "id":           o["id"],
                "car_number":   o["car_number"],
                "from_address": o["from_address"],
                "to_address":   o["to_address"],
                "distance":     o["distance"],
                "price":        o["price"],
                "client":       o["client"],
                "status":       o["status"],
                "created_at":   format_time(o["created_at"]),
                "completed_at": format_time(o["completed_at"])
            } for o in orders]
        })
    except Exception as e:
        logger.error(f"Заказы: {e}")
        return jsonify({"success": False}), 500

# Финансы
@app.route("/api/admin/finances", methods=["GET"])
@login_required
def admin_finances():
    try:
        with db_lock:
            conn = get_db()
            transactions = conn.execute("""
                SELECT * FROM transactions
                ORDER BY created_at DESC LIMIT 100
            """).fetchall()
            balance_reqs = conn.execute("""
                SELECT * FROM balance_requests
                WHERE status='pending'
                ORDER BY created_at DESC
            """).fetchall()
            conn.close()
        return jsonify({
            "success": True,
            "transactions": [{
                "id":         t["id"],
                "car_number": t["car_number"],
                "amount":     t["amount"],
                "type":       t["type"],
                "comment":    t["comment"],
                "created_at": format_time(t["created_at"])
            } for t in transactions],
            "balance_requests": [{
                "id":         r["id"],
                "car_number": r["car_number"],
                "amount":     r["amount"],
                "created_at": format_time(r["created_at"])
            } for r in balance_reqs]
        })
    except Exception as e:
        logger.error(f"Финансы: {e}")
        return jsonify({"success": False}), 500

# Смены
@app.route("/api/admin/shifts", methods=["GET"])
@login_required
def admin_shifts():
    try:
        with db_lock:
            conn = get_db()
            shifts = conn.execute("""
                SELECT s.*, d.name FROM shifts s
                LEFT JOIN drivers d ON d.car_number=s.car_number
                ORDER BY s.start_time DESC LIMIT 100
            """).fetchall()
            conn.close()
        return jsonify({
            "success": True,
            "shifts": [{
                "id":           s["id"],
                "car_number":   s["car_number"],
                "name":         s["name"],
                "start_time":   format_time(s["start_time"]),
                "end_time":     format_time(s["end_time"]),
                "revenue":      s["revenue"],
                "orders_count": s["orders_count"]
            } for s in shifts]
        })
    except Exception as e:
        logger.error(f"Смены: {e}")
        return jsonify({"success": False}), 500

# Чат админа
@app.route("/api/admin/chat/<car_number>", methods=["GET"])
@login_required
def admin_chat_get(car_number):
    try:
        with db_lock:
            conn = get_db()
            messages = conn.execute("""
                SELECT * FROM chat_messages
                WHERE car_number=?
                ORDER BY created_at ASC
            """, (car_number,)).fetchall()
            conn.close()
        return jsonify({
            "success": True,
            "messages": [{
                "id":         m["id"],
                "text":       m["text"],
                "sender":     m["sender"],
                "created_at": format_time(m["created_at"])
            } for m in messages]
        })
    except Exception as e:
        logger.error(f"Чат: {e}")
        return jsonify({"success": False}), 500

@app.route("/api/admin/chat/<car_number>", methods=["POST"])
@login_required
def admin_chat_send(car_number):
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"success": False}), 400
    try:
        with db_lock:
            conn = get_db()
            conn.execute(
                "INSERT INTO chat_messages (car_number,text,sender,created_at) VALUES (?,?,?,?)",
                (car_number, text, "admin", time.time())
            )
            conn.commit()
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Чат отправка: {e}")
        return jsonify({"success": False}), 500

# Рейтинг
@app.route("/api/admin/ratings", methods=["GET"])
@login_required
def admin_ratings():
    try:
        with db_lock:
            conn = get_db()
            ratings = conn.execute("""
                SELECT d.car_number, d.name,
                       COALESCE(AVG(r.stars),0) as avg_rating,
                       COUNT(r.id) as count
                FROM drivers d
                LEFT JOIN ratings r ON r.car_number=d.car_number
                GROUP BY d.car_number
                ORDER BY avg_rating DESC
            """).fetchall()
            conn.close()
        return jsonify({
            "success": True,
            "ratings": [{
                "car_number": r["car_number"],
                "name":       r["name"],
                "rating":     round(r["avg_rating"], 1),
                "count":      r["count"]
            } for r in ratings]
        })
    except Exception as e:
        logger.error(f"Рейтинг: {e}")
        return jsonify({"success": False}), 500

# Тарифы
@app.route("/api/admin/tariffs", methods=["GET"])
@login_required
def admin_tariffs_get():
    try:
        with db_lock:
            conn = get_db()
            tariffs = conn.execute("SELECT * FROM tariffs").fetchall()
            conn.close()
        return jsonify({
            "success": True,
            "tariffs": [{
                "id":          t["id"],
                "zone":        t["zone"],
                "name":        t["name"],
                "base_fare":   t["base_fare"],
                "rate_per_km": t["rate_per_km"],
                "wait_rate":   t["wait_rate"]
            } for t in tariffs]
        })
    except Exception as e:
        logger.error(f"Тарифы: {e}")
        return jsonify({"success": False}), 500

@app.route("/api/admin/tariffs/<int:tariff_id>", methods=["PUT"])
@login_required
def admin_tariff_update(tariff_id):
    data = request.get_json() or {}
    try:
        with db_lock:
            conn = get_db()
            conn.execute("""
                UPDATE tariffs SET
                base_fare=?, rate_per_km=?, wait_rate=?
                WHERE id=?
            """, (data.get("base_fare"), data.get("rate_per_km"),
                  data.get("wait_rate"), tariff_id))
            conn.commit()
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Обновление тарифа: {e}")
        return jsonify({"success": False}), 500

# Удалить водителя
@app.route("/api/admin/driver/<car_number>", methods=["DELETE"])
@login_required
def admin_delete_driver(car_number):
    try:
        with db_lock:
            conn = get_db()
            conn.execute("DELETE FROM drivers WHERE car_number=?", (car_number,))
            conn.commit()
            conn.close()
        log_action("driver_deleted", car_number)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Удаление водителя: {e}")
        return jsonify({"success": False}), 500

# ═══════════════════════════════════════
#  HTML СТРАНИЦЫ
# ═══════════════════════════════════════

LOGIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAXI 3042 — Вход</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#fff;font-family:'Segoe UI',sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh}
.box{background:#111;border:1px solid #222;border-radius:16px;
     padding:40px;width:360px;text-align:center}
.logo{font-size:28px;font-weight:900;color:#f5c518;margin-bottom:8px}
.sub{color:#666;font-size:13px;margin-bottom:32px}
input{width:100%;background:#1a1a1a;border:1px solid #333;border-radius:10px;
      padding:14px 16px;color:#fff;font-size:15px;margin-bottom:12px;outline:none}
input:focus{border-color:#f5c518}
button{width:100%;background:#f5c518;color:#000;border:none;border-radius:10px;
       padding:14px;font-size:16px;font-weight:700;cursor:pointer;margin-top:8px}
button:hover{background:#e6b800}
.error{background:#ff000020;border:1px solid #ff000050;border-radius:8px;
       padding:10px;color:#ff6b6b;font-size:13px;margin-bottom:16px}
</style>
</head>
<body>
<div class="box">
  <div class="logo">🚕 TAXI 3042</div>
  <div class="sub">XAZARASP — Диспетчерская панель</div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="POST">
    <input type="text"     name="login"    placeholder="Логин"   required>
    <input type="password" name="password" placeholder="Пароль"  required>
    <button type="submit">Войти</button>
  </form>
</div>
</body>
</html>"""

MAIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAXI 3042 XAZARASP</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#fff;font-family:'Segoe UI',sans-serif}

/* NAV */
.nav{background:#111;border-bottom:1px solid #1e1e1e;padding:0 24px;
     display:flex;align-items:center;gap:0;position:sticky;top:0;z-index:100}
.nav-logo{font-size:16px;font-weight:900;color:#f5c518;padding:16px 24px 16px 0;
          border-right:1px solid #222;margin-right:16px;white-space:nowrap}
.nav-tabs{display:flex;gap:0;flex:1}
.tab{padding:18px 20px;cursor:pointer;color:#888;font-size:13px;font-weight:600;
     border-bottom:3px solid transparent;transition:all .2s;white-space:nowrap}
.tab:hover{color:#fff}
.tab.active{color:#f5c518;border-bottom-color:#f5c518}
.nav-right{display:flex;align-items:center;gap:16px;margin-left:auto}
.time{color:#888;font-size:13px;font-family:monospace}
.live{display:flex;align-items:center;gap:6px;color:#4caf50;font-size:12px;font-weight:700}
.live-dot{width:8px;height:8px;background:#4caf50;border-radius:50%;animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.btn-logout{background:#1a1a1a;border:1px solid #333;color:#888;padding:8px 16px;
            border-radius:8px;cursor:pointer;font-size:12px;text-decoration:none}
.btn-logout:hover{color:#fff;border-color:#555}

/* CONTENT */
.content{padding:24px;max-width:1400px;margin:0 auto}
.page{display:none}.page.active{display:block}

/* CARDS */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}
.card{background:#111;border:1px solid #1e1e1e;border-radius:12px;padding:20px;text-align:center}
.card-val{font-size:28px;font-weight:900;margin-bottom:4px}
.card-lbl{font-size:11px;color:#666;text-transform:uppercase;letter-spacing:.5px}
.c-yellow{color:#f5c518}.c-green{color:#4caf50}.c-red{color:#f44336}
.c-blue{color:#2196f3}.c-purple{color:#9c27b0}

/* SECTIONS */
.section{background:#111;border:1px solid #1e1e1e;border-radius:12px;
         padding:20px;margin-bottom:20px}
.section-title{font-size:14px;font-weight:700;margin-bottom:16px;
               display:flex;align-items:center;gap:8px}

/* TABLE */
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:#666;font-weight:600;text-align:left;padding:10px 12px;
   border-bottom:1px solid #1e1e1e;white-space:nowrap}
td{padding:12px;border-bottom:1px solid #111;vertical-align:middle}
tr:last-child td{border:none}
tr:hover td{background:#151515}

/* STATUS BADGES */
.badge{padding:4px 10px;border-radius:20px;font-size:11px;font-weight:700;white-space:nowrap}
.badge-free{background:#4caf5020;color:#4caf50}
.badge-busy{background:#ff980020;color:#ff9800}
.badge-offline{background:#66666620;color:#666}
.badge-pending{background:#2196f320;color:#2196f3}
.badge-approved{background:#4caf5020;color:#4caf50}
.badge-rejected{background:#f4433620;color:#f44336}
.badge-completed{background:#4caf5020;color:#4caf50}
.badge-cancelled{background:#f4433620;color:#f44336}

/* BUTTONS */
.btn{padding:8px 16px;border-radius:8px;border:none;cursor:pointer;
     font-size:12px;font-weight:700;transition:all .2s}
.btn-primary{background:#f5c518;color:#000}
.btn-primary:hover{background:#e6b800}
.btn-success{background:#4caf50;color:#fff}
.btn-success:hover{background:#43a047}
.btn-danger{background:#f44336;color:#fff}
.btn-danger:hover{background:#e53935}
.btn-secondary{background:#1e1e1e;color:#aaa;border:1px solid #333}
.btn-secondary:hover{color:#fff}
.btn-sm{padding:5px 10px;font-size:11px}

/* FORMS */
.form-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:12px}
.form-group{display:flex;flex-direction:column;gap:6px}
.form-group label{font-size:12px;color:#888}
.form-group input,.form-group select{
  background:#1a1a1a;border:1px solid #333;border-radius:8px;
  padding:10px 12px;color:#fff;font-size:13px;outline:none}
.form-group input:focus,.form-group select:focus{border-color:#f5c518}

/* QUICK ACTIONS */
.quick-actions{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap}

/* PIN CARD */
.pin-card{background:#111;border:2px solid #f5c51830;border-radius:12px;
          padding:16px;margin-bottom:12px;display:flex;
          align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
.pin-info{display:flex;flex-direction:column;gap:4px}
.pin-name{font-weight:700;font-size:15px}
.pin-details{color:#888;font-size:12px}
.pin-code{background:#f5c51820;border:1px solid #f5c518;border-radius:8px;
          padding:8px 16px;font-size:20px;font-weight:900;color:#f5c518;
          font-family:monospace;letter-spacing:4px}
.pin-actions{display:flex;gap:8px}

/* CHAT */
.chat-list{display:flex;flex-direction:column;gap:8px;margin-bottom:16px}
.chat-item{background:#1a1a1a;border:1px solid #222;border-radius:8px;
           padding:12px 16px;cursor:pointer;display:flex;
           justify-content:space-between;align-items:center}
.chat-item:hover{border-color:#f5c518}
.chat-messages{height:300px;overflow-y:auto;background:#0a0a0a;
               border-radius:8px;padding:12px;margin-bottom:12px;display:flex;flex-direction:column;gap:8px}
.msg{max-width:70%;padding:10px 14px;border-radius:12px;font-size:13px;line-height:1.4}
.msg-admin{background:#f5c51820;border:1px solid #f5c51840;align-self:flex-end}
.msg-driver{background:#1e1e1e;border:1px solid #333;align-self:flex-start}
.msg-time{font-size:10px;color:#666;margin-top:4px}
.chat-input-row{display:flex;gap:8px}
.chat-input{flex:1;background:#1a1a1a;border:1px solid #333;border-radius:8px;
            padding:10px 12px;color:#fff;outline:none;font-size:13px}
.chat-input:focus{border-color:#f5c518}
.no-data{text-align:center;padding:40px;color:#444}
.no-data-icon{font-size:48px;margin-bottom:8px}
.modal{display:none;position:fixed;inset:0;background:#000a;z-index:1000;
       align-items:center;justify-content:center}
.modal.open{display:flex}
.modal-box{background:#111;border:1px solid #333;border-radius:16px;
           padding:32px;width:480px;max-width:90vw}
.modal-title{font-size:18px;font-weight:700;margin-bottom:20px}
.modal-actions{display:flex;gap:12px;margin-top:20px;justify-content:flex-end}
</style>
</head>
<body>

<!-- NAV -->
<nav class="nav">
  <div class="nav-logo">🚕 TAXI 3042 <span style="color:#666;font-size:11px">XAZARASP</span></div>
  <div class="nav-tabs">
    <div class="tab active" onclick="showPage('dashboard')">📊 Дашборд</div>
    <div class="tab" onclick="showPage('orders')">📋 Заказы</div>
    <div class="tab" onclick="showPage('drivers')">🚗 Водители</div>
    <div class="tab" onclick="showPage('pins')">🔑 Заявки</div>
    <div class="tab" onclick="showPage('shifts')">⏱ Смены</div>
    <div class="tab" onclick="showPage('ratings')">⭐ Рейтинг</div>
    <div class="tab" onclick="showPage('chat')">💬 Чат</div>
    <div class="tab" onclick="showPage('finances')">💰 Финансы</div>
    <div class="tab" onclick="showPage('settings')">⚙️ Настройки</div>
  </div>
  <div class="nav-right">
    <span class="time" id="clock">--:--:--</span>
    <span class="live"><span class="live-dot"></span>Live</span>
    <a href="/logout" class="btn-logout">🚪 Выход</a>
  </div>
</nav>

<div class="content">

<!-- ДАШБОРД -->
<div class="page active" id="page-dashboard">
  <div class="cards">
    <div class="card"><div class="card-val c-green" id="d-online">0</div><div class="card-lbl">На линии</div></div>
    <div class="card"><div class="card-val c-green" id="d-free">0</div><div class="card-lbl">Свободны</div></div>
    <div class="card"><div class="card-val c-yellow" id="d-busy">0</div><div class="card-lbl">На заказе</div></div>
    <div class="card"><div class="card-val c-blue" id="d-pins">0</div><div class="card-lbl">Заявок</div></div>
    <div class="card"><div class="card-val c-purple" id="d-total">0</div><div class="card-lbl">Всего водит.</div></div>
    <div class="card"><div class="card-val c-yellow" id="d-orders">0</div><div class="card-lbl">Акт.заказов</div></div>
    <div class="card"><div class="card-val c-green" id="d-revenue">0</div><div class="card-lbl">Выручка</div></div>
    <div class="card"><div class="card-val c-blue" id="d-today">0</div><div class="card-lbl">Сегодня</div></div>
  </div>
  <div class="quick-actions">
    <button class="btn btn-primary" onclick="openModal('modal-order')">⚡ Новый заказ</button>
    <button class="btn btn-secondary" onclick="showPage('chat')">💬 Чат</button>
    <button class="btn btn-secondary" onclick="showPage('pins')">🔑 Заявки</button>
    <button class="btn btn-secondary" onclick="loadDashboard()">🔄 Обновить</button>
  </div>
  <div class="section">
    <div class="section-title">🚗 Водители онлайн</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Авто</th><th>Водитель</th><th>Статус</th>
          <th>Баланс</th><th>Рейтинг</th><th>Заказов</th><th>Действия</th>
        </tr></thead>
        <tbody id="online-drivers-tbody">
          <tr><td colspan="7" class="no-data"><div class="no-data-icon">🚗</div>Нет водителей онлайн</td></tr>
        </tbody>
      </table>
    </div>
  </div>
  <div class="section">
    <div class="section-title">📋 Активные заказы</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>#</th><th>Водитель</th><th>Откуда</th><th>Куда</th>
          <th>Цена</th><th>Клиент</th><th>Время</th><th>Действия</th>
        </tr></thead>
        <tbody id="active-orders-tbody">
          <tr><td colspan="8" class="no-data"><div class="no-data-icon">📦</div>Нет активных заказов</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- ЗАКАЗЫ -->
<div class="page" id="page-orders">
  <div class="section">
    <div class="section-title" style="justify-content:space-between">
      <span>📋 Все заказы</span>
      <button class="btn btn-primary btn-sm" onclick="openModal('modal-order')">+ Новый заказ</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>#</th><th>Авто</th><th>Откуда</th><th>Куда</th>
          <th>Расстояние</th><th>Цена</th><th>Клиент</th>
          <th>Статус</th><th>Время</th>
        </tr></thead>
        <tbody id="orders-tbody">
          <tr><td colspan="9" class="no-data">Загрузка...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- ВОДИТЕЛИ -->
<div class="page" id="page-drivers">
  <div class="section">
    <div class="section-title">🚗 Все водители</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Авто</th><th>Имя</th><th>Телефон</th><th>Статус</th>
          <th>Баланс</th><th>ПИН</th><th>Рейтинг</th><th>Заказов</th>
          <th>Регистрация</th><th>Действия</th>
        </tr></thead>
        <tbody id="drivers-tbody">
          <tr><td colspan="10" class="no-data">Загрузка...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- ЗАЯВКИ (ПИНЫ) -->
<div class="page" id="page-pins">
  <div class="section">
    <div class="section-title">🔑 Заявки на регистрацию</div>
    <div id="pins-container">
      <div class="no-data"><div class="no-data-icon">🔑</div>Нет новых заявок</div>
    </div>
  </div>
</div>

<!-- СМЕНЫ -->
<div class="page" id="page-shifts">
  <div class="section">
    <div class="section-title">⏱ Смены водителей</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Авто</th><th>Водитель</th><th>Начало</th>
          <th>Конец</th><th>Выручка</th><th>Заказов</th>
        </tr></thead>
        <tbody id="shifts-tbody">
          <tr><td colspan="6" class="no-data">Загрузка...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- РЕЙТИНГ -->
<div class="page" id="page-ratings">
  <div class="section">
    <div class="section-title">⭐ Рейтинг водителей</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>#</th><th>Авто</th><th>Водитель</th>
          <th>Рейтинг</th><th>Оценок</th>
        </tr></thead>
        <tbody id="ratings-tbody">
          <tr><td colspan="5" class="no-data">Загрузка...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- ЧАТ -->
<div class="page" id="page-chat">
  <div style="display:grid;grid-template-columns:280px 1fr;gap:16px">
    <div class="section">
      <div class="section-title">💬 Водители</div>
      <div id="chat-list" class="chat-list">
        <div class="no-data">Загрузка...</div>
      </div>
    </div>
    <div class="section">
      <div class="section-title" id="chat-title">💬 Выберите водителя</div>
      <div class="chat-messages" id="chat-messages"></div>
      <div class="chat-input-row">
        <input class="chat-input" id="chat-input" placeholder="Сообщение..." 
               onkeypress="if(event.key==='Enter')sendChatMsg()">
        <button class="btn btn-primary" onclick="sendChatMsg()">Отправить</button>
      </div>
    </div>
  </div>
</div>

<!-- ФИНАНСЫ -->
<div class="page" id="page-finances">
  <div class="section">
    <div class="section-title">💳 Заявки на пополнение</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Авто</th><th>Сумма</th><th>Время</th><th>Действия</th>
        </tr></thead>
        <tbody id="balance-req-tbody">
          <tr><td colspan="4" class="no-data">Нет заявок</td></tr>
        </tbody>
      </table>
    </div>
  </div>
  <div class="section">
    <div class="section-title">📊 Транзакции</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Авто</th><th>Сумма</th><th>Тип</th><th>Комментарий</th><th>Время</th>
        </tr></thead>
        <tbody id="transactions-tbody">
          <tr><td colspan="5" class="no-data">Загрузка...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- НАСТРОЙКИ -->
<div class="page" id="page-settings">
  <div class="section">
    <div class="section-title">⚙️ Тарифы</div>
    <div id="tariffs-container"></div>
  </div>
</div>

</div><!-- /content -->

<!-- МОДАЛ: НОВЫЙ ЗАКАЗ -->
<div class="modal" id="modal-order">
  <div class="modal-box">
    <div class="modal-title">⚡ Новый заказ</div>
    <div class="form-row">
      <div class="form-group">
        <label>Водитель (номер авто)</label>
        <input id="o-car" placeholder="01A123BC">
      </div>
      <div class="form-group">
        <label>Клиент</label>
        <input id="o-client" placeholder="Имя клиента" value="Диспетчер">
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>Откуда</label>
        <input id="o-from" placeholder="Адрес подачи">
      </div>
      <div class="form-group">
        <label>Куда</label>
        <input id="o-to" placeholder="Адрес назначения">
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>Расстояние</label>
        <input id="o-dist" placeholder="5 км">
      </div>
      <div class="form-group">
        <label>Цена (сум)</label>
        <input id="o-price" type="number" placeholder="15000">
      </div>
    </div>
    <div class="modal-actions">
      <button class="btn btn-secondary" onclick="closeModal('modal-order')">Отмена</button>
      <button class="btn btn-primary" onclick="createOrder()">Создать заказ</button>
    </div>
  </div>
</div>

<script>
// ═══════════════════════════════════
//  НАВИГАЦИЯ
// ═══════════════════════════════════
let currentPage = 'dashboard';
let chatCar = null;

function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.querySelectorAll('.tab').forEach(t => {
    if (t.textContent.toLowerCase().includes(
      name === 'dashboard' ? 'дашборд' :
      name === 'orders' ? 'заказ' :
      name === 'drivers' ? 'водител' :
      name === 'pins' ? 'заявк' :
      name === 'shifts' ? 'смен' :
      name === 'ratings' ? 'рейтинг' :
      name === 'chat' ? 'чат' :
      name === 'finances' ? 'финанс' : 'настройк'
    )) t.classList.add('active');
  });
  currentPage = name;
  loadPage(name);
}

function loadPage(name) {
  if (name === 'dashboard') loadDashboard();
  else if (name === 'orders') loadOrders();
  else if (name === 'drivers') loadDrivers();
  else if (name === 'pins') loadPins();
  else if (name === 'shifts') loadShifts();
  else if (name === 'ratings') loadRatings();
  else if (name === 'chat') loadChatList();
  else if (name === 'finances') loadFinances();
  else if (name === 'settings') loadSettings();
}

// ═══════════════════════════════════
//  ЧАСЫ
// ═══════════════════════════════════
function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toTimeString().slice(0,8);
}
setInterval(updateClock, 1000);
updateClock();

// ═══════════════════════════════════
//  ДАШБОРД
// ═══════════════════════════════════
async function loadDashboard() {
  try {
    const r = await fetch('/api/admin/dashboard');
    const d = await r.json();
    if (!d.success) return;
    const s = d.stats;
    document.getElementById('d-online').textContent  = s.online;
    document.getElementById('d-free').textContent    = s.free;
    document.getElementById('d-busy').textContent    = s.busy;
    document.getElementById('d-pins').textContent    = s.pending_pins;
    document.getElementById('d-total').textContent   = s.total;
    document.getElementById('d-orders').textContent  = s.active_orders;
    document.getElementById('d-revenue').textContent = fmtMoney(s.today_revenue);
    document.getElementById('d-today').textContent   = s.today_orders;

    // Онлайн водители
    const tb = document.getElementById('online-drivers-tbody');
    if (d.online_drivers.length === 0) {
      tb.innerHTML = '<tr><td colspan="7" class="no-data"><div class="no-data-icon">🚗</div>Нет водителей онлайн</td></tr>';
    } else {
      tb.innerHTML = d.online_drivers.map(dr => `
        <tr>
          <td><b>${dr.car_number}</b></td>
          <td>${dr.name}</td>
          <td><span class="badge badge-${dr.status}">${statusLabel(dr.status)}</span></td>
          <td>${fmtMoney(dr.balance)}</td>
          <td>⭐ ${dr.rating}</td>
          <td>${dr.orders}</td>
          <td>
            <button class="btn btn-primary btn-sm" onclick="quickOrder('${dr.car_number}')">📋 Заказ</button>
            <button class="btn btn-secondary btn-sm" onclick="openChat('${dr.car_number}')">💬</button>
          </td>
        </tr>
      `).join('');
    }

    // Активные заказы
    const tb2 = document.getElementById('active-orders-tbody');
    if (d.active_orders.length === 0) {
      tb2.innerHTML = '<tr><td colspan="8" class="no-data"><div class="no-data-icon">📦</div>Нет активных заказов</td></tr>';
    } else {
      tb2.innerHTML = d.active_orders.map(o => `
        <tr>
          <td>#${o.id}</td>
          <td>${o.car_number}</td>
          <td>${o.from_address}</td>
          <td>${o.to_address}</td>
          <td>${fmtMoney(o.price)}</td>
          <td>${o.client}</td>
          <td>${o.created_at}</td>
          <td><span class="badge badge-${o.status}">${orderStatus(o.status)}</span></td>
        </tr>
      `).join('');
    }
  } catch(e) { console.error(e); }
}

// ═══════════════════════════════════
//  ЗАКАЗЫ
// ═══════════════════════════════════
async function loadOrders() {
  try {
    const r = await fetch('/api/admin/orders');
    const d = await r.json();
    const tb = document.getElementById('orders-tbody');
    if (!d.orders || d.orders.length === 0) {
      tb.innerHTML = '<tr><td colspan="9" class="no-data">Нет заказов</td></tr>';
      return;
    }
    tb.innerHTML = d.orders.map(o => `
      <tr>
        <td>#${o.id}</td>
        <td>${o.car_number}</td>
        <td>${o.from_address}</td>
        <td>${o.to_address}</td>
        <td>${o.distance}</td>
        <td>${fmtMoney(o.price)}</td>
        <td>${o.client}</td>
        <td><span class="badge badge-${o.status}">${orderStatus(o.status)}</span></td>
        <td>${o.created_at}</td>
      </tr>
    `).join('');
  } catch(e) { console.error(e); }
}

// ═══════════════════════════════════
//  ВОДИТЕЛИ
// ═══════════════════════════════════
async function loadDrivers() {
  try {
    const r = await fetch('/api/admin/drivers');
    const d = await r.json();
    const tb = document.getElementById('drivers-tbody');
    if (!d.drivers || d.drivers.length === 0) {
      tb.innerHTML = '<tr><td colspan="10" class="no-data">Нет водителей</td></tr>';
      return;
    }
    tb.innerHTML = d.drivers.map(dr => `
      <tr>
        <td><b>${dr.car_number}</b></td>
        <td>${dr.name}</td>
        <td>${dr.phone}</td>
        <td><span class="badge badge-${dr.status}">${statusLabel(dr.status)}</span></td>
        <td>${fmtMoney(dr.balance)}</td>
        <td><code style="background:#f5c51820;color:#f5c518;padding:4px 8px;border-radius:6px;font-size:16px;letter-spacing:3px">${dr.pin}</code></td>
        <td>⭐ ${dr.rating}</td>
        <td>${dr.orders}</td>
        <td>${dr.created_at}</td>
        <td>
          <button class="btn btn-secondary btn-sm" onclick="openChat('${dr.car_number}')">💬</button>
          <button class="btn btn-danger btn-sm" onclick="deleteDriver('${dr.car_number}')">🗑</button>
        </td>
      </tr>
    `).join('');
  } catch(e) { console.error(e); }
}

// ═══════════════════════════════════
//  ЗАЯВКИ (ПИНЫ)
// ═══════════════════════════════════
async function loadPins() {
  try {
    const r = await fetch('/api/admin/drivers');
    const d = await r.json();
    const container = document.getElementById('pins-container');
    if (!d.pending || d.pending.length === 0) {
      container.innerHTML = '<div class="no-data"><div class="no-data-icon">✅</div>Нет новых заявок</div>';
      return;
    }
    container.innerHTML = d.pending.map(p => `
      <div class="pin-card">
        <div class="pin-info">
          <div class="pin-name">🚗 ${p.car_number} — ${p.name}</div>
          <div class="pin-details">📞 ${p.phone} &nbsp;|&nbsp; 🕐 ${p.created_at}</div>
        </div>
        <div class="pin-code">${p.pin}</div>
        <div class="pin-actions">
          <button class="btn btn-success" onclick="approvePin('${p.id}')">✅ Одобрить</button>
          <button class="btn btn-danger" onclick="rejectPin('${p.id}')">❌ Отклонить</button>
        </div>
      </div>
    `).join('');
  } catch(e) { console.error(e); }
}

async function approvePin(id) {
  if (!confirm('Одобрить водителя?')) return;
  const r = await fetch('/api/admin/approve/' + id, {method:'POST'});
  const d = await r.json();
  if (d.success) {
    alert('✅ Водитель одобрен!\n\nЕго ПИН-код: ' + d.pin + '\n\nСообщите водителю этот ПИН!');
    loadPins();
    document.getElementById('d-pins').textContent =
      parseInt(document.getElementById('d-pins').textContent) - 1;
  }
}

async function rejectPin(id) {
  if (!confirm('Отклонить заявку?')) return;
  const r = await fetch('/api/admin/reject/' + id, {method:'POST'});
  const d = await r.json();
  if (d.success) { alert('Заявка отклонена'); loadPins(); }
}

// ═══════════════════════════════════
//  СМЕНЫ
// ═══════════════════════════════════
async function loadShifts() {
  try {
    const r = await fetch('/api/admin/shifts');
    const d = await r.json();
    const tb = document.getElementById('shifts-tbody');
    if (!d.shifts || d.shifts.length === 0) {
      tb.innerHTML = '<tr><td colspan="6" class="no-data">Нет смен</td></tr>';
      return;
    }
    tb.innerHTML = d.shifts.map(s => `
      <tr>
        <td>${s.car_number}</td>
        <td>${s.name || '—'}</td>
        <td>${s.start_time}</td>
        <td>${s.end_time}</td>
        <td>${fmtMoney(s.revenue)}</td>
        <td>${s.orders_count}</td>
      </tr>
    `).join('');
  } catch(e) { console.error(e); }
}

// ═══════════════════════════════════
//  РЕЙТИНГ
// ═══════════════════════════════════
async function loadRatings() {
  try {
    const r = await fetch('/api/admin/ratings');
    const d = await r.json();
    const tb = document.getElementById('ratings-tbody');
    if (!d.ratings || d.ratings.length === 0) {
      tb.innerHTML = '<tr><td colspan="5" class="no-data">Нет данных</td></tr>';
      return;
    }
    tb.innerHTML = d.ratings.map((r,i) => `
      <tr>
        <td>${i+1}</td>
        <td>${r.car_number}</td>
        <td>${r.name}</td>
        <td>${'⭐'.repeat(Math.round(r.rating))} ${r.rating}</td>
        <td>${r.count}</td>
      </tr>
    `).join('');
  } catch(e) { console.error(e); }
}

// ═══════════════════════════════════
//  ЧАТ
// ═══════════════════════════════════
async function loadChatList() {
  try {
    const r = await fetch('/api/admin/drivers');
    const d = await r.json();
    const container = document.getElementById('chat-list');
    if (!d.drivers || d.drivers.length === 0) {
      container.innerHTML = '<div class="no-data">Нет водителей</div>';
      return;
    }
    container.innerHTML = d.drivers.map(dr => `
      <div class="chat-item" onclick="openChat('${dr.car_number}')">
        <span>${dr.car_number} — ${dr.name}</span>
        <span class="badge badge-${dr.status}">${statusLabel(dr.status)}</span>
      </div>
    `).join('');
  } catch(e) { console.error(e); }
}

async function openChat(car) {
  chatCar = car;
  showPage('chat');
  document.getElementById('chat-title').textContent = '💬 Чат: ' + car;
  const r = await fetch('/api/admin/chat/' + car);
  const d = await r.json();
  const box = document.getElementById('chat-messages');
  if (!d.messages || d.messages.length === 0) {
    box.innerHTML = '<div style="color:#444;text-align:center;padding:20px">Нет сообщений</div>';
    return;
  }
  box.innerHTML = d.messages.map(m => `
    <div class="msg msg-${m.sender}">
      ${m.text}
      <div class="msg-time">${m.created_at}</div>
    </div>
  `).join('');
  box.scrollTop = box.scrollHeight;
}

async function sendChatMsg() {
  if (!chatCar) return alert('Выберите водителя');
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  await fetch('/api/admin/chat/' + chatCar, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({text})
  });
  input.value = '';
  openChat(chatCar);
}

// ═══════════════════════════════════
//  ФИНАНСЫ
// ═══════════════════════════════════
async function loadFinances() {
  try {
    const r = await fetch('/api/admin/finances');
    const d = await r.json();

    const tb1 = document.getElementById('balance-req-tbody');
    if (!d.balance_requests || d.balance_requests.length === 0) {
      tb1.innerHTML = '<tr><td colspan="4" class="no-data">Нет заявок</td></tr>';
    } else {
      tb1.innerHTML = d.balance_requests.map(req => `
        <tr>
          <td>${req.car_number}</td>
          <td>${fmtMoney(req.amount)}</td>
          <td>${req.created_at}</td>
          <td>
            <button class="btn btn-success btn-sm" onclick="approveBalance(${req.id})">✅ Одобрить</button>
          </td>
        </tr>
      `).join('');
    }

    const tb2 = document.getElementById('transactions-tbody');
    if (!d.transactions || d.transactions.length === 0) {
      tb2.innerHTML = '<tr><td colspan="5" class="no-data">Нет транзакций</td></tr>';
    } else {
      tb2.innerHTML = d.transactions.map(t => `
        <tr>
          <td>${t.car_number}</td>
          <td style="color:${t.type==='income'?'#4caf50':'#2196f3'}">${t.type==='income'?'+':'+'} ${fmtMoney(t.amount)}</td>
          <td>${t.type === 'income' ? '💰 Доход' : '💳 Пополнение'}</td>
          <td>${t.comment || '—'}</td>
          <td>${t.created_at}</td>
        </tr>
      `).join('');
    }
  } catch(e) { console.error(e); }
}

async function approveBalance(id) {
  if (!confirm('Одобрить пополнение?')) return;
  const r = await fetch('/api/admin/balance/approve/' + id, {method:'POST'});
  const d = await r.json();
  if (d.success) { alert('✅ Баланс пополнен'); loadFinances(); }
}

// ═══════════════════════════════════
//  НАСТРОЙКИ
// ═══════════════════════════════════
async function loadSettings() {
  try {
    const r = await fetch('/api/admin/tariffs');
    const d = await r.json();
    const container = document.getElementById('tariffs-container');
    container.innerHTML = d.tariffs.map(t => `
      <div style="background:#1a1a1a;border-radius:10px;padding:16px;margin-bottom:12px">
        <div style="font-weight:700;margin-bottom:12px">${t.name}</div>
        <div class="form-row">
          <div class="form-group">
            <label>Базовая стоимость (сум)</label>
            <input type="number" id="t-base-${t.id}" value="${t.base_fare}">
          </div>
          <div class="form-group">
            <label>За км (сум)</label>
            <input type="number" id="t-km-${t.id}" value="${t.rate_per_km}">
          </div>
          <div class="form-group">
            <label>Ожидание/мин (сум)</label>
            <input type="number" id="t-wait-${t.id}" value="${t.wait_rate}">
          </div>
          <div class="form-group" style="justify-content:flex-end">
            <label>&nbsp;</label>
            <button class="btn btn-primary" onclick="saveTariff(${t.id})">💾 Сохранить</button>
          </div>
        </div>
      </div>
    `).join('');
  } catch(e) { console.error(e); }
}

async function saveTariff(id) {
  const r = await fetch('/api/admin/tariffs/' + id, {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      base_fare:   parseInt(document.getElementById('t-base-'+id).value),
      rate_per_km: parseInt(document.getElementById('t-km-'+id).value),
      wait_rate:   parseInt(document.getElementById('t-wait-'+id).value)
    })
  });
  const d = await r.json();
  if (d.success) alert('✅ Тариф сохранён');
}

// ═══════════════════════════════════
//  ЗАКАЗ
// ═══════════════════════════════════
function quickOrder(car) {
  document.getElementById('o-car').value = car;
  openModal('modal-order');
}

async function createOrder() {
  const car   = document.getElementById('o-car').value.trim();
  const from  = document.getElementById('o-from').value.trim();
  const to    = document.getElementById('o-to').value.trim();
  const price = parseInt(document.getElementById('o-price').value);
  const client= document.getElementById('o-client').value.trim();
  const dist  = document.getElementById('o-dist').value.trim();

  if (!car || !from || !to || !price) {
    alert('Заполните все обязательные поля'); return;
  }
  const r = await fetch('/api/admin/order', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({car_number:car, from_address:from,
      to_address:to, price, client, distance:dist})
  });
  const d = await r.json();
  if (d.success) {
    closeModal('modal-order');
    alert('✅ Заказ создан!');
    loadDashboard();
  } else {
    alert('Ошибка: ' + (d.error || 'Неизвестная ошибка'));
  }
}

async function deleteDriver(car) {
  if (!confirm('Удалить водителя ' + car + '?')) return;
  const r = await fetch('/api/admin/driver/' + car, {method:'DELETE'});
  const d = await r.json();
  if (d.success) { alert('Водитель удалён'); loadDrivers(); }
}

// ═══════════════════════════════════
//  ВСПОМОГАТЕЛЬНЫЕ
// ═══════════════════════════════════
function fmtMoney(n) {
  if (!n) return '0 сум';
  return n.toLocaleString('ru') + ' сум';
}

function statusLabel(s) {
  return {free:'Свободен', busy:'На заказе', offline:'Офлайн'}[s] || s;
}

function orderStatus(s) {
  return {pending:'Ожидает', accepted:'Принят',
          completed:'Завершён', cancelled:'Отменён'}[s] || s;
}

function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

// Закрыть модал по клику снаружи
document.querySelectorAll('.modal').forEach(m => {
  m.addEventListener('click', e => { if(e.target===m) m.classList.remove('open'); });
});

// ═══════════════════════════════════
//  АВТООБНОВЛЕНИЕ
// ═══════════════════════════════════
setInterval(() => {
  if (currentPage === 'dashboard') loadDashboard();
  else if (currentPage === 'pins') loadPins();
}, 15000);

// Старт
loadDashboard();
</script>
</body>
</html>"""

# ═══════════════════════════════════════
#  ГЛАВНЫЙ МАРШРУТ
# ═══════════════════════════════════════
@app.route("/")
@login_required
def index():
    return render_template_string(MAIN_HTML)

# ═══════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════
if __name__ == "__main__":
    init_db()
    threading.Thread(target=background_tasks, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚕 TAXI 3042 XAZARASP запущен на порту {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
else:
    init_db()
    threading.Thread(target=background_tasks, daemon=True).start()
