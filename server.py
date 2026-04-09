from flask import Flask, request, jsonify, render_template_string, session, redirect
from datetime import datetime
from functools import wraps
import random
import time
import threading
import requests
import os
import json
import sqlite3
import logging
import re

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("taxi_server.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = "TAXI3042_SECRET_KEY_XAZARASP"

# ==================== БЛОКИРОВКА ====================
db_lock = threading.Lock()

# ==================== БАЗА ДАННЫХ ====================
DB_PATH = "taxi.db"

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

        # Дефолтные тарифы
        default_tariffs = [
            ("city_day",     "day",   "🏙️ Город день",    5000, 2800, 500),
            ("city_night",   "night", "🌙 Город ночь",     7000, 3500, 700),
            ("suburb_day",   "day",   "🌳 Загород день",   5000, 3000, 500),
            ("suburb_night", "night", "🌙 Загород ночь",   7000, 3800, 700),
            ("airport",      "day",   "✈️ Аэропорт",      10000, 3500, 500),
            ("vokzal",       "day",   "🚉 Вокзал",         8000, 3000, 500),
        ]
        for zone, time_type, name, base, km, wait in default_tariffs:
            c.execute('''INSERT OR IGNORE INTO tariffs
                (zone, time_type, name, base_fare, rate_per_km, wait_rate)
                VALUES (?,?,?,?,?,?)''',
                (zone, time_type, name, base, km, wait)
            )

        conn.commit()
        conn.close()
        logger.info("✅ База данных готова")

init_db()

# ==================== ВАЛИДАЦИЯ ====================
def validate_car_number(car):
    if not car or len(car) < 3 or len(car) > 20:
        return False, "Неверный номер авто"
    return True, ""

def validate_address(addr):
    if not addr or len(addr.strip()) < 2:
        return False, "Адрес слишком короткий"
    if len(addr) > 200:
        return False, "Адрес слишком длинный"
    return True, ""

def validate_price(price):
    try:
        p = int(price)
        if p <= 0:
            return False, "Цена должна быть больше 0"
        if p > 10000000:
            return False, "Цена слишком большая"
        return True, ""
    except:
        return False, "Неверный формат цены"

def validate_phone(phone):
    phone_clean = re.sub(r'[\s\-\(\)]', '', phone)
    if not re.match(r'^\+?[\d]{9,13}$', phone_clean):
        return False, "Неверный формат телефона"
    return True, ""

def validate_name(name):
    if not name or len(name.strip()) < 2:
        return False, "Имя слишком короткое"
    if len(name) > 100:
        return False, "Имя слишком длинное"
    return True, ""

def validate_amount(amount):
    try:
        a = int(amount)
        if a <= 0:
            return False, "Сумма должна быть больше 0"
        if a > 100000000:
            return False, "Сумма слишком большая"
        return True, ""
    except:
        return False, "Неверный формат суммы"

# ==================== АУТЕНТИФИКАЦИЯ ====================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "taxi3042")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

LOGIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAXI 3042 — Вход</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0a0a;
color:#e5e5e5;min-height:100vh;display:flex;align-items:center;justify-content:center}
.box{background:#111;border:1px solid #222;border-radius:20px;
padding:40px;width:100%;max-width:380px}
.logo{text-align:center;font-size:24px;font-weight:700;
color:#FFD600;margin-bottom:8px}
.sub{text-align:center;color:#666;font-size:13px;margin-bottom:28px}
.fg{margin-bottom:16px}
.fg label{display:block;color:#666;font-size:11px;
text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
.fc{width:100%;background:#0a0a0a;color:#e5e5e5;
border:1px solid #2a2a2a;padding:10px 14px;
border-radius:10px;font-size:14px;outline:none;transition:border-color .2s}
.fc:focus{border-color:#FFD600}
.btn{width:100%;background:#FFD600;color:#000;border:none;
border-radius:10px;padding:12px;font-size:15px;
font-weight:700;cursor:pointer;margin-top:8px}
.btn:hover{background:#e6c200}
.err{background:#2a0a0a;color:#ef4444;border:1px solid #4a0000;
border-radius:8px;padding:10px 14px;font-size:13px;
margin-bottom:16px;text-align:center}
</style>
</head>
<body>
<div class="box">
  <div class="logo">🚕 TAXI 3042</div>
  <div class="sub">Диспетчерская · Xazarasp</div>
  {% if error %}<div class="err">❌ {{ error }}</div>{% endif %}
  <form method="POST">
    <div class="fg">
      <label>Логин</label>
      <input class="fc" name="username" type="text" placeholder="admin">
    </div>
    <div class="fg">
      <label>Пароль</label>
      <input class="fc" name="password" type="password" placeholder="••••••••">
    </div>
    <button class="btn" type="submit">🔐 Войти</button>
  </form>
</div>
</body>
</html>"""

@app.route("/login", methods=["GET","POST"])
def login():
    error = ""
    if request.method == "POST":
        username = request.form.get("username","")
        password = request.form.get("password","")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            add_log("login", "admin", f"Вход: {username}")
            return redirect("/")
        error = "Неверный логин или пароль"
        logger.warning(f"Неудачный вход: {username}")
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ==================== TELEGRAM ====================
TG_TOKEN   = "8757251631:AAHMFD4cg1dU9SdZ8-7HMDxy5qDUpSc5TIs"
TG_CHAT_ID = "1053431273"
TG_API     = f"https://api.telegram.org/bot{TG_TOKEN}"

def tg_send(text, reply_markup=None):
    try:
        data = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        requests.post(f"{TG_API}/sendMessage", data=data, timeout=5)
    except Exception as e:
        logger.error(f"tg_send error: {e}")

def tg_answer_callback(callback_id, text):
    try:
        requests.post(f"{TG_API}/answerCallbackQuery",
                      data={"callback_query_id": callback_id, "text": text},
                      timeout=5)
    except:
        pass

def tg_notify_new_pin(pin_id, name, car, phone, pin):
    text = (
        f"🔑 <b>Новая заявка на регистрацию</b>\n\n"
        f"👤 Имя: <b>{name}</b>\n"
        f"🚗 Авто: <b>{car}</b>\n"
        f"📱 Тел: <b>{phone}</b>\n"
        f"🔐 ПИН: <b>{pin}</b>"
    )
    markup = {"inline_keyboard": [[
        {"text": "✅ Одобрить", "callback_data": f"approve:{pin_id}"},
        {"text": "❌ Отказать", "callback_data": f"reject:{pin_id}"}
    ]]}
    tg_send(text, markup)

def tg_notify_balance_request(req_id, car, amount):
    text = (
        f"💰 <b>Заявка на пополнение</b>\n\n"
        f"🚗 Авто: <b>{car}</b>\n"
        f"💵 Сумма: <b>{amount:,} сум</b>"
    )
    markup = {"inline_keyboard": [[
        {"text": "✅ Одобрить", "callback_data": f"bal_approve:{req_id}"},
        {"text": "❌ Отказать", "callback_data": f"bal_reject:{req_id}"}
    ]]}
    tg_send(text, markup)

def tg_notify_approved(name, car, pin):
    tg_send(f"✅ <b>{name}</b> ({car}) одобрен!\nПИН: <b>{pin}</b>")

def tg_notify_rejected(name, car):
    tg_send(f"❌ Заявка <b>{name}</b> ({car}) отклонена")

def tg_notify_shift_start(car, name):
    tg_send(
        f"🟢 <b>Смена начата</b>\n"
        f"🚗 {car} — {name}\n"
        f"⏱ {datetime.now().strftime('%H:%M:%S')}"
    )

def tg_notify_shift_end(car, name, revenue, orders):
    tg_send(
        f"🔴 <b>Смена завершена</b>\n"
        f"🚗 {car} — {name}\n"
        f"💰 Выручка: <b>{revenue:,} сум</b>\n"
        f"📦 Заказов: <b>{orders}</b>"
    )

def tg_notify_order_completed(car, from_addr, to_addr, price):
    tg_send(
        f"✅ <b>Заказ завершён</b>\n"
        f"🚗 {car}\n"
        f"📍 {from_addr} → {to_addr}\n"
        f"💰 {price:,} сум"
    )

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def add_log(action, car_number, details):
    try:
        with db_lock:
            conn = get_db()
            conn.execute(
                "INSERT INTO logs (action,car_number,details,created_at) VALUES (?,?,?,?)",
                (action, car_number or "—", details, time.time())
            )
            conn.commit()
            conn.close()
        logger.info(f"[{action}] {car_number}: {details}")
    except Exception as e:
        logger.error(f"add_log error: {e}")

def get_rating_db(car_number):
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT ROUND(AVG(stars),1) as avg, COUNT(*) as cnt FROM ratings WHERE car_number=?",
            (car_number,)
        ).fetchone()
        orders = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE car_number=? AND status='completed'",
            (car_number,)
        ).fetchone()[0]
        conn.close()
        avg = float(row["avg"]) if row["avg"] else 5.0
        return {"avg": avg, "count": row["cnt"], "orders": orders}
    except Exception as e:
        logger.error(f"get_rating_db error: {e}")
        return {"avg": 5.0, "count": 0, "orders": 0}

def update_balance_db(car_number, amount, type_, comment=""):
    try:
        with db_lock:
            conn = get_db()
            conn.execute(
                "UPDATE drivers SET balance=balance+? WHERE car_number=?",
                (amount, car_number)
            )
            conn.execute(
                "INSERT INTO transactions (car_number,amount,type,comment,created_at) VALUES (?,?,?,?,?)",
                (car_number, amount, type_, comment, time.time())
            )
            new_b = conn.execute(
                "SELECT balance FROM drivers WHERE car_number=?", (car_number,)
            ).fetchone()["balance"]
            conn.commit()
            conn.close()
        add_log("balance", car_number, f"{type_}: {amount:,} сум | Итого: {new_b:,} сум")
        return new_b
    except Exception as e:
        logger.error(f"update_balance_db error: {e}")
        return 0

def add_rating_db(car_number, stars):
    try:
        with db_lock:
            conn = get_db()
            conn.execute(
                "INSERT INTO ratings (car_number,stars,created_at) VALUES (?,?,?)",
                (car_number, stars, time.time())
            )
            conn.commit()
            conn.close()
        add_log("rating", car_number, f"Оценка: {stars}⭐")
    except Exception as e:
        logger.error(f"add_rating_db error: {e}")

def get_stars(avg):
    if avg >= 4.8: return "⭐⭐⭐⭐⭐"
    if avg >= 4.0: return "⭐⭐⭐⭐"
    if avg >= 3.0: return "⭐⭐⭐"
    if avg >= 2.0: return "⭐⭐"
    return "⭐"

def create_order_db(car_number, from_addr, to_addr, price, client="Диспетчер", distance="—"):
    try:
        with db_lock:
            conn = get_db()
            driver = conn.execute(
                "SELECT status FROM drivers WHERE car_number=?", (car_number,)
            ).fetchone()
            if driver and driver["status"] == "busy":
                conn.close()
                return None, "Водитель уже на заказе"

            conn.execute(
                """UPDATE orders SET status='cancelled',cancelled_at=?,cancel_reason='Новый заказ'
                WHERE car_number=? AND status='pending'""",
                (time.time(), car_number)
            )
            cursor = conn.execute(
                """INSERT INTO orders
                (car_number,from_address,to_address,distance,price,client,status,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (car_number, from_addr, to_addr, distance, price, client, "pending", time.time())
            )
            order_id = cursor.lastrowid
            conn.execute(
                "UPDATE drivers SET status='busy' WHERE car_number=?", (car_number,)
            )
            conn.commit()
            conn.close()
        add_log("create_order", car_number,
                f"Заказ #{order_id}: {from_addr}→{to_addr} | {price:,} сум")
        return order_id, None
    except Exception as e:
        logger.error(f"create_order_db error: {e}")
        return None, str(e)

def complete_order_db(order_id):
    try:
        with db_lock:
            conn = get_db()
            order = conn.execute(
                "SELECT * FROM orders WHERE id=?", (order_id,)
            ).fetchone()
            if not order:
                conn.close()
                return False, "Заказ не найден"
            if order["status"] != "pending":
                conn.close()
                return False, "Заказ уже обработан"

            car   = order["car_number"]
            price = order["price"]
            conn.execute(
                "UPDATE orders SET status='completed',completed_at=? WHERE id=?",
                (time.time(), order_id)
            )
            conn.execute(
                "UPDATE drivers SET status='free' WHERE car_number=?", (car,)
            )
            conn.execute(
                """UPDATE shifts SET revenue=revenue+?,orders_count=orders_count+1
                WHERE car_number=? AND end_time IS NULL""",
                (price, car)
            )
            conn.commit()
            conn.close()
        add_log("complete_order", car, f"Заказ #{order_id} завершён | {price:,} сум")
        tg_notify_order_completed(car, order["from_address"], order["to_address"], price)
        return True, "Заказ завершён"
    except Exception as e:
        logger.error(f"complete_order_db error: {e}")
        return False, str(e)

def cancel_order_db(order_id, reason="Отменён диспетчером"):
    try:
        with db_lock:
            conn = get_db()
            order = conn.execute(
                "SELECT * FROM orders WHERE id=?", (order_id,)
            ).fetchone()
            if not order:
                conn.close()
                return False, "Заказ не найден"
            if order["status"] != "pending":
                conn.close()
                return False, "Заказ уже обработан"

            car = order["car_number"]
            conn.execute(
                """UPDATE orders SET status='cancelled',cancelled_at=?,cancel_reason=?
                WHERE id=?""",
                (time.time(), reason, order_id)
            )
            conn.execute(
                "UPDATE drivers SET status='free' WHERE car_number=?", (car,)
            )
            conn.commit()
            conn.close()
        add_log("cancel_order", car, f"Заказ #{order_id} отменён: {reason}")
        return True, "Заказ отменён"
    except Exception as e:
        logger.error(f"cancel_order_db error: {e}")
        return False, str(e)

def start_shift(car_number):
    try:
        with db_lock:
            conn = get_db()
            active = conn.execute(
                "SELECT id FROM shifts WHERE car_number=? AND end_time IS NULL",
                (car_number,)
            ).fetchone()
            if active:
                conn.close()
                return False, "Смена уже начата"
            conn.execute(
                "INSERT INTO shifts (car_number,start_time,revenue,orders_count) VALUES (?,?,0,0)",
                (car_number, time.time())
            )
            conn.execute(
                "UPDATE drivers SET status='free' WHERE car_number=?", (car_number,)
            )
            driver = conn.execute(
                "SELECT name FROM drivers WHERE car_number=?", (car_number,)
            ).fetchone()
            conn.commit()
            conn.close()
        name = driver["name"] if driver else car_number
        add_log("shift_start", car_number, f"Смена начата {datetime.now().strftime('%H:%M')}")
        tg_notify_shift_start(car_number, name)
        return True, "Смена начата"
    except Exception as e:
        logger.error(f"start_shift error: {e}")
        return False, str(e)

def end_shift(car_number):
    try:
        with db_lock:
            conn = get_db()
            shift = conn.execute(
                "SELECT * FROM shifts WHERE car_number=? AND end_time IS NULL",
                (car_number,)
            ).fetchone()
            if not shift:
                conn.close()
                return False, "Нет активной смены"
            conn.execute(
                "UPDATE shifts SET end_time=? WHERE id=?",
                (time.time(), shift["id"])
            )
            conn.execute(
                "UPDATE drivers SET status='offline' WHERE car_number=?", (car_number,)
            )
            driver = conn.execute(
                "SELECT name FROM drivers WHERE car_number=?", (car_number,)
            ).fetchone()
            conn.commit()
            conn.close()
        name = driver["name"] if driver else car_number
        add_log("shift_end", car_number,
                f"Смена завершена | {shift['revenue']:,} сум | {shift['orders_count']} заказов")
        tg_notify_shift_end(car_number, name, shift["revenue"], shift["orders_count"])
        return True, "Смена завершена"
    except Exception as e:
        logger.error(f"end_shift error: {e}")
        return False, str(e)

# ==================== ТАЙМАУТ ЗАКАЗОВ ====================
ORDER_TIMEOUT = 60

def auto_timeout_orders():
    while True:
        try:
            with db_lock:
                conn = get_db()
                timeout_time = time.time() - (ORDER_TIMEOUT * 60)
                old = conn.execute(
                    "SELECT * FROM orders WHERE status='pending' AND created_at<?",
                    (timeout_time,)
                ).fetchall()
                for o in old:
                    conn.execute(
                        """UPDATE orders SET status='cancelled',cancelled_at=?,
                        cancel_reason='Таймаут' WHERE id=?""",
                        (time.time(), o["id"])
                    )
                    conn.execute(
                        "UPDATE drivers SET status='free' WHERE car_number=?",
                        (o["car_number"],)
                    )
                    logger.info(f"⏰ Заказ #{o['id']} отменён по таймауту")
                conn.commit()
                conn.close()
        except Exception as e:
            logger.error(f"auto_timeout error: {e}")
        time.sleep(120)

threading.Thread(target=auto_timeout_orders, daemon=True).start()

# ==================== TELEGRAM POLLING ====================
tg_offset = 0

def tg_polling():
    global tg_offset
    logger.info("🤖 Telegram бот запущен")
    while True:
        try:
            resp = requests.get(
                f"{TG_API}/getUpdates",
                params={"offset": tg_offset, "timeout": 30},
                timeout=35
            )
            updates = resp.json().get("result", [])
            for upd in updates:
                tg_offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    cq    = upd["callback_query"]
                    cq_id = cq["id"]
                    data  = cq.get("data","")

                    if data.startswith("approve:"):
                        pin_id = data.split(":",1)[1]
                        with db_lock:
                            conn = get_db()
                            row = conn.execute(
                                "SELECT * FROM pending_pins WHERE id=?", (pin_id,)
                            ).fetchone()
                            if row and row["status"] == "pending":
                                conn.execute(
                                    "UPDATE pending_pins SET status='approved' WHERE id=?",
                                    (pin_id,)
                                )
                                conn.execute(
                                    """INSERT OR IGNORE INTO drivers
                                    (car_number,name,phone,pin,balance,status,created_at)
                                    VALUES (?,?,?,?,50000,'offline',?)""",
                                    (row["car_number"],row["name"],
                                     row["phone"],row["pin"],time.time())
                                )
                                conn.commit()
                                conn.close()
                                tg_answer_callback(cq_id,"✅ Одобрено!")
                                tg_notify_approved(row["name"],row["car_number"],row["pin"])
                                add_log("approve_pin",row["car_number"],f"Одобрен: {row['name']}")
                            else:
                                conn.close()
                                tg_answer_callback(cq_id,"Заявка не найдена")

                    elif data.startswith("reject:"):
                        pin_id = data.split(":",1)[1]
                        with db_lock:
                            conn = get_db()
                            row = conn.execute(
                                "SELECT * FROM pending_pins WHERE id=?", (pin_id,)
                            ).fetchone()
                            if row:
                                conn.execute(
                                    "UPDATE pending_pins SET status='rejected' WHERE id=?",
                                    (pin_id,)
                                )
                                conn.commit()
                                conn.close()
                                tg_answer_callback(cq_id,"❌ Отклонено")
                                tg_notify_rejected(row["name"],row["car_number"])
                            else:
                                conn.close()

                    elif data.startswith("bal_approve:"):
                        req_id = int(data.split(":",1)[1])
                        with db_lock:
                            conn = get_db()
                            row = conn.execute(
                                "SELECT * FROM balance_requests WHERE id=?", (req_id,)
                            ).fetchone()
                            if row and row["status"] == "pending":
                                car    = row["car_number"]
                                amount = row["amount"]
                                conn.execute(
                                    "UPDATE drivers SET balance=balance+? WHERE car_number=?",
                                    (amount, car)
                                )
                                conn.execute(
                                    "UPDATE balance_requests SET status='approved' WHERE id=?",
                                    (req_id,)
                                )
                                conn.execute(
                                    """INSERT INTO transactions
                                    (car_number,amount,type,comment,created_at)
                                    VALUES (?,?,?,?,?)""",
                                    (car, amount,"deposit","Пополнение одобрено",time.time())
                                )
                                new_b = conn.execute(
                                    "SELECT balance FROM drivers WHERE car_number=?", (car,)
                                ).fetchone()["balance"]
                                conn.commit()
                                conn.close()
                                tg_answer_callback(cq_id,"✅ Баланс пополнен!")
                                tg_send(f"✅ Баланс <b>{car}</b>: <b>{new_b:,} сум</b>")
                                add_log("balance_approve", car, f"{amount:,} сум")
                            else:
                                conn.close()
                                tg_answer_callback(cq_id,"Не найдено")

                    elif data.startswith("bal_reject:"):
                        req_id = int(data.split(":",1)[1])
                        with db_lock:
                            conn = get_db()
                            conn.execute(
                                "UPDATE balance_requests SET status='rejected' WHERE id=?",
                                (req_id,)
                            )
                            conn.commit()
                            conn.close()
                        tg_answer_callback(cq_id,"❌ Отклонено")

                elif "message" in upd:
                    msg      = upd["message"]
                    text_msg = msg.get("text","")

                    if text_msg == "/start":
                        tg_send(
                            "🚕 <b>TAXI 3042 Xazarasp</b>\n\n"
                            "/status — статус\n"
                            "/drivers — водители\n"
                            "/orders — заказы\n"
                            "/revenue — выручка\n"
                            "/rating — рейтинг\n"
                            "/pending — заявки ПИН\n"
                            "/shifts — смены\n"
                            "/logs — логи\n\n"
                            "<code>/order НОМЕР Откуда;Куда;Цена</code>"
                        )
                    elif text_msg == "/status":
                        conn = get_db()
                        free    = conn.execute("SELECT COUNT(*) FROM drivers WHERE status='free'").fetchone()[0]
                        busy    = conn.execute("SELECT COUNT(*) FROM drivers WHERE status='busy'").fetchone()[0]
                        total   = conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
                        pending = conn.execute("SELECT COUNT(*) FROM pending_pins WHERE status='pending'").fetchone()[0]
                        active  = conn.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
                        rev     = conn.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed'").fetchone()[0]
                        conn.close()
                        tg_send(
                            f"📊 <b>Статус</b>\n\n"
                            f"🟢 Свободны: {free}\n"
                            f"🔴 На заказе: {busy}\n"
                            f"👥 Всего: {total}\n"
                            f"📦 Акт. заказов: {active}\n"
                            f"⏳ Ждут ПИН: {pending}\n"
                            f"💰 Выручка: {rev:,} сум"
                        )
                    elif text_msg == "/drivers":
                        conn = get_db()
                        rows = conn.execute(
                            "SELECT * FROM drivers WHERE status!='offline'"
                        ).fetchall()
                        conn.close()
                        if not rows:
                            tg_send("Нет водителей онлайн")
                        else:
                            lines = []
                            for d in rows:
                                icon = "🟢" if d["status"]=="free" else "🔴"
                                lines.append(f"{icon} <b>{d['car_number']}</b> — {d['name']}\n   💰 {d['balance']:,} сум")
                            tg_send("🚗 <b>Водители онлайн:</b>\n\n" + "\n".join(lines))
                    elif text_msg == "/orders":
                        conn = get_db()
                        rows = conn.execute("SELECT * FROM orders WHERE status='pending'").fetchall()
                        conn.close()
                        if not rows:
                            tg_send("Нет активных заказов")
                        else:
                            for o in rows:
                                tg_send(
                                    f"📦 <b>#{o['id']}</b> | {o['car_number']}\n"
                                    f"📍 {o['from_address']} → {o['to_address']}\n"
                                    f"💰 {o['price']:,} сум"
                                )
                    elif text_msg == "/revenue":
                        conn = get_db()
                        rows = conn.execute(
                            """SELECT car_number,SUM(price) as rev FROM orders
                            WHERE status='completed' GROUP BY car_number
                            ORDER BY rev DESC LIMIT 10"""
                        ).fetchall()
                        total = conn.execute(
                            "SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed'"
                        ).fetchone()[0]
                        conn.close()
                        if not rows:
                            tg_send("Нет данных")
                        else:
                            lines = [f"🚗 <b>{r['car_number']}</b>: {r['rev']:,} сум" for r in rows]
                            tg_send("💰 <b>Выручка:</b>\n\n" + "\n".join(lines) + f"\n\n📊 Итого: <b>{total:,} сум</b>")
                    elif text_msg == "/rating":
                        conn = get_db()
                        rows = conn.execute(
                            """SELECT car_number,ROUND(AVG(stars),1) as avg,COUNT(*) as cnt
                            FROM ratings GROUP BY car_number ORDER BY avg DESC LIMIT 10"""
                        ).fetchall()
                        conn.close()
                        if not rows:
                            tg_send("Нет данных")
                        else:
                            lines = [f"{i+1}. <b>{r['car_number']}</b> — ⭐{r['avg']} ({r['cnt']} оценок)"
                                     for i,r in enumerate(rows)]
                            tg_send("🏆 <b>Рейтинг:</b>\n\n" + "\n".join(lines))
                    elif text_msg == "/pending":
                        conn = get_db()
                        rows = conn.execute(
                            "SELECT * FROM pending_pins WHERE status='pending'"
                        ).fetchall()
                        conn.close()
                        if not rows:
                            tg_send("Нет заявок")
                        else:
                            for p in rows:
                                tg_send(
                                    f"⏳ <b>{p['name']}</b>\n"
                                    f"🚗 {p['car_number']}\n"
                                    f"📱 {p['phone']}\n"
                                    f"🔐 ПИН: <b>{p['pin']}</b>"
                                )
                    elif text_msg == "/shifts":
                        conn = get_db()
                        rows = conn.execute(
                            """SELECT s.*,d.name FROM shifts s
                            LEFT JOIN drivers d ON s.car_number=d.car_number
                            ORDER BY s.start_time DESC LIMIT 10"""
                        ).fetchall()
                        conn.close()
                        if not rows:
                            tg_send("Нет смен")
                        else:
                            lines = []
                            for r in rows:
                                start = datetime.fromtimestamp(r["start_time"]).strftime("%d.%m %H:%M")
                                end   = datetime.fromtimestamp(r["end_time"]).strftime("%H:%M") if r["end_time"] else "▶"
                                lines.append(
                                    f"🚗 <b>{r['car_number']}</b>\n"
                                    f"   ⏱ {start}→{end} | 💰{r['revenue']:,} | 📦{r['orders_count']}"
                                )
                            tg_send("⏱ <b>Смены:</b>\n\n" + "\n\n".join(lines))
                    elif text_msg == "/logs":
                        conn = get_db()
                        rows = conn.execute(
                            "SELECT * FROM logs ORDER BY created_at DESC LIMIT 10"
                        ).fetchall()
                        conn.close()
                        if not rows:
                            tg_send("Нет логов")
                        else:
                            lines = []
                            for r in rows:
                                t = datetime.fromtimestamp(r["created_at"]).strftime("%H:%M:%S")
                                lines.append(f"[{t}] <b>{r['action']}</b> {r['car_number']}\n{r['details']}")
                            tg_send("📝 <b>Логи:</b>\n\n" + "\n\n".join(lines))
                    elif text_msg.startswith("/order "):
                        try:
                            parts     = text_msg.split(" ",2)
                            car       = parts[1].strip().upper()
                            info      = parts[2].split(";")
                            from_addr = info[0].strip()
                            to_addr   = info[1].strip()
                            price     = int(info[2].strip())
                            oid, err  = create_order_db(car, from_addr, to_addr, price, "Telegram")
                            if oid:
                                tg_send(f"✅ Заказ #{oid} → {car}\n{from_addr}→{to_addr}\n💰{price:,} сум")
                            else:
                                tg_send(f"❌ {err}")
                        except Exception as e:
                            tg_send(f"❌ Ошибка: {e}\nФормат: /order НОМЕР Откуда;Куда;Цена")
        except Exception as e:
            logger.error(f"tg_polling error: {e}")
            time.sleep(5)

threading.Thread(target=tg_polling, daemon=True).start()

# ==================== API ====================
@app.route("/api/stats")
@login_required
def api_stats():
    try:
        conn = get_db()
        online  = conn.execute("SELECT COUNT(*) FROM drivers WHERE status!='offline'").fetchone()[0]
        free    = conn.execute("SELECT COUNT(*) FROM drivers WHERE status='free'").fetchone()[0]
        busy    = conn.execute("SELECT COUNT(*) FROM drivers WHERE status='busy'").fetchone()[0]
        total   = conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM pending_pins WHERE status='pending'").fetchone()[0]
        active  = conn.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
        revenue = conn.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed'").fetchone()[0]
        today   = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE created_at>?", (time.time()-86400,)
        ).fetchone()[0]
        conn.close()
        return jsonify({"ok":True,"stats":{
            "online":online,"free":free,"busy":busy,"total":total,
            "pending_pins":pending,"active_orders":active,
            "revenue":revenue,"today_orders":today
        }})
    except Exception as e:
        logger.error(f"api_stats error: {e}")
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/drivers")
@login_required
def api_drivers():
    try:
        conn = get_db()
        rows = conn.execute("SELECT * FROM drivers ORDER BY status").fetchall()
        conn.close()
        result = []
        for d in rows:
            r = get_rating_db(d["car_number"])
            result.append({
                "car_number": d["car_number"],
                "name":       d["name"],
                "phone":      d["phone"],
                "status":     d["status"],
                "balance":    d["balance"],
                "rating":     r["avg"],
                "orders":     r["orders"],
                "rating_count": r["count"]
            })
        return jsonify({"ok":True,"drivers":result})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/drivers/online")
@login_required
def api_drivers_online():
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM drivers WHERE status!='offline' ORDER BY status"
        ).fetchall()
        conn.close()
        result = []
        for d in rows:
            r = get_rating_db(d["car_number"])
            result.append({
                "car_number": d["car_number"],
                "name":       d["name"],
                "phone":      d["phone"],
                "status":     d["status"],
                "balance":    d["balance"],
                "rating":     r["avg"],
                "orders":     r["orders"]
            })
        return jsonify({"ok":True,"drivers":result})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/driver/<car_number>")
@login_required
def api_driver_info(car_number):
    try:
        conn = get_db()
        d = conn.execute(
            "SELECT * FROM drivers WHERE car_number=?", (car_number,)
        ).fetchone()
        if not d:
            conn.close()
            return jsonify({"ok":False,"error":"Не найден"})
        order = conn.execute(
            "SELECT * FROM orders WHERE car_number=? AND status='pending'", (car_number,)
        ).fetchone()
        shift = conn.execute(
            "SELECT * FROM shifts WHERE car_number=? AND end_time IS NULL", (car_number,)
        ).fetchone()
        orders = conn.execute(
            "SELECT * FROM orders WHERE car_number=? ORDER BY created_at DESC LIMIT 10", (car_number,)
        ).fetchall()
        conn.close()
        r = get_rating_db(car_number)
        return jsonify({
            "ok":True,
            "driver":{
                "car_number": d["car_number"],"name":d["name"],
                "phone":d["phone"],"status":d["status"],
                "balance":d["balance"],"rating":r["avg"],
                "orders":r["orders"],"rating_count":r["count"]
            },
            "active_order": dict(order) if order else None,
            "active_shift": dict(shift) if shift else None,
            "recent_orders":[dict(o) for o in orders]
        })
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/orders")
@login_required
def api_orders():
    try:
        status = request.args.get("status","all")
        conn   = get_db()
        if status == "all":
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status=? ORDER BY created_at DESC LIMIT 100",
                (status,)
            ).fetchall()
        conn.close()
        return jsonify({"ok":True,"orders":[dict(o) for o in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/orders/create", methods=["POST"])
@login_required
def api_create_order():
    try:
        data      = request.json or {}
        car       = data.get("car_number","").strip().upper()
        from_addr = data.get("from_address","").strip()
        to_addr   = data.get("to_address","").strip()
        price     = data.get("price",0)
        client    = data.get("client","Диспетчер").strip()

        # Валидация
        if car != "ALL":
            ok,err = validate_car_number(car)
            if not ok: return jsonify({"ok":False,"error":err})
        ok,err = validate_address(from_addr)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err = validate_address(to_addr)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err = validate_price(price)
        if not ok: return jsonify({"ok":False,"error":err})

        if car == "ALL":
            conn = get_db()
            free = conn.execute(
                "SELECT car_number FROM drivers WHERE status='free'"
            ).fetchall()
            conn.close()
            ids = []
            for d in free:
                oid,_ = create_order_db(d["car_number"],from_addr,to_addr,int(price),client)
                if oid: ids.append(oid)
            return jsonify({"ok":True,"order_ids":ids,"message":f"Отправлено {len(ids)} водителям"})

        oid, err = create_order_db(car, from_addr, to_addr, int(price), client)
        if oid:
            return jsonify({"ok":True,"order_id":oid})
        return jsonify({"ok":False,"error":err})
    except Exception as e:
        logger.error(f"api_create_order error: {e}")
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/orders/complete/<int:order_id>", methods=["POST"])
@login_required
def api_complete_order(order_id):
    ok, msg = complete_order_db(order_id)
    return jsonify({"ok":ok,"message":msg})

@app.route("/api/orders/cancel/<int:order_id>", methods=["POST"])
@login_required
def api_cancel_order(order_id):
    data   = request.json or {}
    reason = data.get("reason","Отменён диспетчером")
    ok, msg = cancel_order_db(order_id, reason)
    return jsonify({"ok":ok,"message":msg})

@app.route("/api/orders/driver_cancel/<int:order_id>", methods=["POST"])
def api_driver_cancel(order_id):
    try:
        data   = request.json or {}
        car    = data.get("car_number","").strip().upper()
        reason = data.get("reason","Отменён водителем")
        ok,err = validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})

        conn  = get_db()
        order = conn.execute(
            "SELECT * FROM orders WHERE id=? AND car_number=?", (order_id,car)
        ).fetchone()
        conn.close()
        if not order:
            return jsonify({"ok":False,"error":"Заказ не найден"})

        ok, msg = cancel_order_db(order_id, reason)
        if ok:
            tg_send(
                f"⚠️ Водитель <b>{car}</b> отменил заказ #{order_id}\n"
                f"Причина: {reason}"
            )
        return jsonify({"ok":ok,"message":msg})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/shift/start", methods=["POST"])
@login_required
def api_shift_start():
    data = request.json or {}
    car  = data.get("car_number","").strip().upper()
    ok,err = validate_car_number(car)
    if not ok: return jsonify({"ok":False,"error":err})
    ok, msg = start_shift(car)
    return jsonify({"ok":ok,"message":msg})

@app.route("/api/shift/end", methods=["POST"])
@login_required
def api_shift_end():
    data = request.json or {}
    car  = data.get("car_number","").strip().upper()
    ok,err = validate_car_number(car)
    if not ok: return jsonify({"ok":False,"error":err})
    ok, msg = end_shift(car)
    return jsonify({"ok":ok,"message":msg})

@app.route("/api/shifts")
@login_required
def api_shifts():
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT s.*,d.name FROM shifts s
            LEFT JOIN drivers d ON s.car_number=d.car_number
            ORDER BY s.start_time DESC LIMIT 50"""
        ).fetchall()
        conn.close()
        return jsonify({"ok":True,"shifts":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/pins")
@login_required
def api_pins():
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM pending_pins ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return jsonify({"ok":True,"pins":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/pins/approve/<pin_id>", methods=["POST"])
@login_required
def api_approve_pin(pin_id):
    try:
        with db_lock:
            conn = get_db()
            row  = conn.execute(
                "SELECT * FROM pending_pins WHERE id=?", (pin_id,)
            ).fetchone()
            if not row:
                conn.close()
                return jsonify({"ok":False,"error":"Не найдена"})
            if row["status"] != "pending":
                conn.close()
                return jsonify({"ok":False,"error":"Уже обработана"})
            conn.execute(
                "UPDATE pending_pins SET status='approved' WHERE id=?", (pin_id,)
            )
            conn.execute(
                """INSERT OR IGNORE INTO drivers
                (car_number,name,phone,pin,balance,status,created_at)
                VALUES (?,?,?,?,50000,'offline',?)""",
                (row["car_number"],row["name"],row["phone"],row["pin"],time.time())
            )
            conn.commit()
            conn.close()
        add_log("approve_pin", row["car_number"], f"Одобрен: {row['name']}")
        tg_notify_approved(row["name"], row["car_number"], row["pin"])
        return jsonify({"ok":True,"message":"Водитель одобрен"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/pins/reject/<pin_id>", methods=["POST"])
@login_required
def api_reject_pin(pin_id):
    try:
        with db_lock:
            conn = get_db()
            row  = conn.execute(
                "SELECT * FROM pending_pins WHERE id=?", (pin_id,)
            ).fetchone()
            if not row:
                conn.close()
                return jsonify({"ok":False,"error":"Не найдена"})
            conn.execute(
                "UPDATE pending_pins SET status='rejected' WHERE id=?", (pin_id,)
            )
            conn.commit()
            conn.close()
        add_log("reject_pin", row["car_number"], f"Отклонён: {row['name']}")
        tg_notify_rejected(row["name"], row["car_number"])
        return jsonify({"ok":True,"message":"Отклонено"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/balance/requests")
@login_required
def api_balance_requests():
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM balance_requests ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        conn.close()
        return jsonify({"ok":True,"requests":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/balance/request", methods=["POST"])
def api_balance_request():
    try:
        data   = request.json or {}
        car    = data.get("car_number","").strip().upper()
        amount = data.get("amount",0)
        ok,err = validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err = validate_amount(amount)
        if not ok: return jsonify({"ok":False,"error":err})
        with db_lock:
            conn = get_db()
            cur  = conn.execute(
                "INSERT INTO balance_requests (car_number,amount,status,created_at) VALUES (?,?,?,?)",
                (car, int(amount), "pending", time.time())
            )
            req_id = cur.lastrowid
            conn.commit()
            conn.close()
        tg_notify_balance_request(req_id, car, int(amount))
        add_log("balance_request", car, f"Заявка: {int(amount):,} сум")
        return jsonify({"ok":True,"message":"Заявка отправлена"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/balance/approve/<int:req_id>", methods=["POST"])
@login_required
def api_approve_balance(req_id):
    try:
        with db_lock:
            conn = get_db()
            row  = conn.execute(
                "SELECT * FROM balance_requests WHERE id=?", (req_id,)
            ).fetchone()
            if not row or row["status"] != "pending":
                conn.close()
                return jsonify({"ok":False,"error":"Не найдена или уже обработана"})
            car    = row["car_number"]
            amount = row["amount"]
            conn.execute(
                "UPDATE drivers SET balance=balance+? WHERE car_number=?", (amount,car)
            )
            conn.execute(
                "UPDATE balance_requests SET status='approved' WHERE id=?", (req_id,)
            )
            conn.execute(
                "INSERT INTO transactions (car_number,amount,type,comment,created_at) VALUES (?,?,?,?,?)",
                (car, amount, "deposit", "Пополнение одобрено", time.time())
            )
            new_b = conn.execute(
                "SELECT balance FROM drivers WHERE car_number=?", (car,)
            ).fetchone()["balance"]
            conn.commit()
            conn.close()
        add_log("balance_approve", car, f"{amount:,} сум → {new_b:,} сум")
        tg_send(f"✅ Баланс <b>{car}</b> пополнен!\n💰 <b>{new_b:,} сум</b>")
        return jsonify({"ok":True,"new_balance":new_b})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/balance/reject/<int:req_id>", methods=["POST"])
@login_required
def api_reject_balance(req_id):
    try:
        with db_lock:
            conn = get_db()
            row  = conn.execute(
                "SELECT * FROM balance_requests WHERE id=?", (req_id,)
            ).fetchone()
            if not row:
                conn.close()
                return jsonify({"ok":False,"error":"Не найдена"})
            conn.execute(
                "UPDATE balance_requests SET status='rejected' WHERE id=?", (req_id,)
            )
            conn.commit()
            conn.close()
        add_log("balance_reject", row["car_number"], f"Отклонено: {row['amount']:,} сум")
        return jsonify({"ok":True,"message":"Отклонено"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/balance/add", methods=["POST"])
@login_required
def api_balance_add():
    try:
        data    = request.json or {}
        car     = data.get("car_number","").strip().upper()
        amount  = data.get("amount",0)
        comment = data.get("comment","Ручное пополнение")
        ok,err  = validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err  = validate_amount(abs(int(amount)))
        if not ok: return jsonify({"ok":False,"error":err})
        new_b = update_balance_db(car, int(amount), "manual", comment)
        return jsonify({"ok":True,"new_balance":new_b})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/rating")
@login_required
def api_rating():
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT r.car_number,ROUND(AVG(r.stars),1) as avg,COUNT(r.id) as cnt,d.name,
            (SELECT COUNT(*) FROM orders o WHERE o.car_number=r.car_number AND o.status='completed') as orders
            FROM ratings r LEFT JOIN drivers d ON r.car_number=d.car_number
            GROUP BY r.car_number ORDER BY avg DESC"""
        ).fetchall()
        conn.close()
        return jsonify({"ok":True,"ratings":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/rating/add", methods=["POST"])
@login_required
def api_add_rating():
    try:
        data  = request.json or {}
        car   = data.get("car_number","").strip().upper()
        stars = data.get("stars",5)
        ok,err = validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err = validate_stars(stars)
        if not ok: return jsonify({"ok":False,"error":err})
        add_rating_db(car, int(stars))
        return jsonify({"ok":True,"message":"Оценка добавлена"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/tariffs")
@login_required
def api_tariffs():
    try:
        conn = get_db()
        rows = conn.execute("SELECT * FROM tariffs").fetchall()
        conn.close()
        result = {}
        for r in rows:
            result[r["zone"]] = {
                "name":       r["name"],
                "base_fare":  r["base_fare"],
                "rate_per_km":r["rate_per_km"],
                "wait_rate":  r["wait_rate"]
            }
        return jsonify({"ok":True,"tariffs":result})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/tariffs/update", methods=["POST"])
@login_required
def api_update_tariffs():
    try:
        data = request.json or {}
        with db_lock:
            conn = get_db()
            for zone, vals in data.items():
                conn.execute(
                    """INSERT INTO tariffs (zone,base_fare,rate_per_km,wait_rate)
                    VALUES (?,?,?,?)
                    ON CONFLICT(zone) DO UPDATE SET
                    base_fare=excluded.base_fare,
                    rate_per_km=excluded.rate_per_km,
                    wait_rate=excluded.wait_rate""",
                    (zone, vals.get("base_fare",5000),
                     vals.get("rate_per_km",2800),
                     vals.get("wait_rate",500))
                )
            conn.commit()
            conn.close()
        add_log("tariff_update","admin","Тарифы обновлены")
        return jsonify({"ok":True,"message":"Тарифы сохранены"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/logs")
@login_required
def api_logs():
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        conn.close()
        return jsonify({"ok":True,"logs":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/chat/<car_number>")
@login_required
def api_chat_get(car_number):
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE car_number=? ORDER BY created_at ASC LIMIT 100",
            (car_number,)
        ).fetchall()
        conn.close()
        return jsonify({"ok":True,"messages":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/chat/send", methods=["POST"])
@login_required
def api_chat_send():
    try:
        data   = request.json or {}
        car    = data.get("car_number","").strip().upper()
        text   = data.get("text","").strip()
        sender = data.get("sender","admin")
        ok,err = validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        if not text or len(text) > 500:
            return jsonify({"ok":False,"error":"Неверное сообщение"})
        with db_lock:
            conn = get_db()
            conn.execute(
                "INSERT INTO chat_messages (car_number,text,sender,created_at) VALUES (?,?,?,?)",
                (car, text, sender, time.time())
            )
            conn.commit()
            conn.close()
        if sender == "admin":
            tg_send(f"💬 <b>Диспетчер → {car}</b>\n{text}")
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/finance")
@login_required
def api_finance():
    try:
        conn = get_db()
        total_rev = conn.execute(
            "SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed'"
        ).fetchone()[0]
        today_rev = conn.execute(
            "SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed' AND completed_at>?",
            (time.time()-86400,)
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status='completed'"
        ).fetchone()[0]
        avg_price = conn.execute(
            "SELECT COALESCE(AVG(price),0) FROM orders WHERE status='completed'"
        ).fetchone()[0]
        drivers = conn.execute(
            """SELECT o.car_number,d.name,SUM(o.price) as revenue,COUNT(o.id) as orders
            FROM orders o LEFT JOIN drivers d ON o.car_number=d.car_number
            WHERE o.status='completed' GROUP BY o.car_number ORDER BY revenue DESC"""
        ).fetchall()
        transactions = conn.execute(
            "SELECT * FROM transactions ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        conn.close()
        return jsonify({
            "ok":True,
            "total_revenue":  total_rev,
            "today_revenue":  today_rev,
            "completed_orders": completed,
            "avg_price":      round(avg_price),
            "drivers":        [dict(d) for d in drivers],
            "transactions":   [dict(t) for t in transactions]
        })
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/register", methods=["POST"])
def api_register():
    try:
        data  = request.json or {}
        name  = data.get("name","").strip()
        car   = data.get("car_number","").strip().upper()
        phone = data.get("phone","").strip()

        ok,err = validate_name(name)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err = validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err = validate_phone(phone)
        if not ok: return jsonify({"ok":False,"error":err})

        conn = get_db()
        existing = conn.execute(
            "SELECT car_number FROM drivers WHERE car_number=?", (car,)
        ).fetchone()
        conn.close()
        if existing:
            return jsonify({"ok":False,"error":"Водитель уже зарегистрирован"})

        pin    = str(random.randint(1000,9999))
        pin_id = f"{car}_{int(time.time())}"
        with db_lock:
            conn = get_db()
            conn.execute(
                "INSERT INTO pending_pins (id,name,car_number,phone,pin,status,created_at) VALUES (?,?,?,?,?,?,?)",
                (pin_id, name, car, phone, pin, "pending", time.time())
            )
            conn.commit()
            conn.close()
        tg_notify_new_pin(pin_id, name, car, phone, pin)
        add_log("register", car, f"Заявка: {name} | {phone}")
        return jsonify({"ok":True,"message":"Заявка отправлена. Ожидайте ПИН-код"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/login", methods=["POST"])
def api_login():
    try:
        data = request.json or {}
        car  = data.get("car_number","").strip().upper()
        pin  = data.get("pin","").strip()
        ok,err = validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        if not pin or len(pin) != 4:
            return jsonify({"ok":False,"error":"Неверный ПИН"})
        conn = get_db()
        driver = conn.execute(
            "SELECT * FROM drivers WHERE car_number=? AND pin=?", (car,pin)
        ).fetchone()
        conn.close()
        if not driver:
            return jsonify({"ok":False,"error":"Неверный номер или ПИН"})
        add_log("driver_login", car, "Вход водителя")
        return jsonify({"ok":True,"driver":{
            "car_number": driver["car_number"],
            "name":       driver["name"],
            "phone":      driver["phone"],
            "balance":    driver["balance"],
            "status":     driver["status"]
        }})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/export")
@login_required
def api_export():
    try:
        from flask import Response
        conn    = get_db()
        orders  = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
        drivers = conn.execute("SELECT * FROM drivers").fetchall()
        shifts  = conn.execute(
            "SELECT s.*,d.name FROM shifts s LEFT JOIN drivers d ON s.car_number=d.car_number ORDER BY s.start_time DESC"
        ).fetchall()
        conn.close()

        lines = [
            "="*50,
            f"ОТЧЁТ TAXI 3042 XAZARASP",
            f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            "="*50,
            f"\nЗАКАЗЫ ({len(orders)}):"
        ]
        for o in orders:
            lines.append(f"#{o['id']} | {o['car_number']} | {o['from_address']}→{o['to_address']} | {o['price']:,} сум | {o['status']}")
        lines.append(f"\nВОДИТЕЛИ ({len(drivers)}):")
        for d in drivers:
            lines.append(f"{d['car_number']} | {d['name']} | {d['phone']} | {d['balance']:,} сум")
        lines.append(f"\nСМЕНЫ ({len(shifts)}):")
        for s in shifts:
            start = datetime.fromtimestamp(s["start_time"]).strftime("%d.%m %H:%M")
            end   = datetime.fromtimestamp(s["end_time"]).strftime("%H:%M") if s["end_time"] else "▶"
            lines.append(f"{s['car_number']} | {s['name']or'—'} | {start}→{end} | {s['revenue']:,} сум | {s['orders_count']} заказов")
        total = sum(o["price"] for o in orders if o["status"]=="completed")
        lines += [f"\nИТОГО: {total:,} сум","="*50]

        return Response(
            "\n".join(lines),
            mimetype="text/plain",
            headers={"Content-Disposition":f"attachment;filename=taxi_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"}
        )
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

# ==================== ГЛАВНАЯ ====================
@app.route("/")
@login_required
def index():
    return render_template_string(ADMIN_HTML)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚕 TAXI 3042 запущен на порту {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
