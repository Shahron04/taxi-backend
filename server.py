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

db_lock = threading.Lock()
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
                (zone,time_type,name,base_fare,rate_per_km,wait_rate)
                VALUES (?,?,?,?,?,?)''',
                (zone,time_type,name,base,km,wait))
        conn.commit()
        conn.close()
        logger.info("✅ База данных готова")

init_db()

# ==================== ВАЛИДАЦИЯ ====================
def validate_car_number(car):
    if not car or len(car)<3 or len(car)>20:
        return False,"Неверный номер авто"
    return True,""

def validate_address(addr):
    if not addr or len(addr.strip())<2:
        return False,"Адрес слишком короткий"
    if len(addr)>200:
        return False,"Адрес слишком длинный"
    return True,""

def validate_price(price):
    try:
        p=int(price)
        if p<=0: return False,"Цена должна быть больше 0"
        if p>10000000: return False,"Цена слишком большая"
        return True,""
    except: return False,"Неверный формат цены"

def validate_phone(phone):
    phone_clean=re.sub(r'[\s\-\(\)]','',phone)
    if not re.match(r'^\+?[\d]{9,13}$',phone_clean):
        return False,"Неверный формат телефона"
    return True,""

def validate_name(name):
    if not name or len(name.strip())<2:
        return False,"Имя слишком короткое"
    if len(name)>100: return False,"Имя слишком длинное"
    return True,""

def validate_amount(amount):
    try:
        a=int(amount)
        if a<=0: return False,"Сумма должна быть больше 0"
        if a>100000000: return False,"Сумма слишком большая"
        return True,""
    except: return False,"Неверный формат суммы"

def validate_stars(stars):
    try:
        s=int(stars)
        if s<1 or s>5: return False,"Оценка от 1 до 5"
        return True,""
    except: return False,"Неверный формат"

# ==================== АУТЕНТИФИКАЦИЯ ====================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD","taxi3042")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME","admin")

def login_required(f):
    @wraps(f)
    def decorated(*args,**kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return f(*args,**kwargs)
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
.box{background:#111;border:1px solid #222;border-radius:20px;padding:40px;width:100%;max-width:380px}
.logo{text-align:center;font-size:24px;font-weight:700;color:#FFD600;margin-bottom:8px}
.sub{text-align:center;color:#666;font-size:13px;margin-bottom:28px}
.fg{margin-bottom:16px}
.fg label{display:block;color:#666;font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
.fc{width:100%;background:#0a0a0a;color:#e5e5e5;border:1px solid #2a2a2a;
padding:10px 14px;border-radius:10px;font-size:14px;outline:none;transition:border-color .2s}
.fc:focus{border-color:#FFD600}
.btn{width:100%;background:#FFD600;color:#000;border:none;border-radius:10px;
padding:12px;font-size:15px;font-weight:700;cursor:pointer;margin-top:8px}
.btn:hover{background:#e6c200}
.err{background:#2a0a0a;color:#ef4444;border:1px solid #4a0000;border-radius:8px;
padding:10px 14px;font-size:13px;margin-bottom:16px;text-align:center}
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

@app.route("/login",methods=["GET","POST"])
def login():
    error=""
    if request.method=="POST":
        username=request.form.get("username","")
        password=request.form.get("password","")
        if username==ADMIN_USERNAME and password==ADMIN_PASSWORD:
            session["logged_in"]=True
            add_log("login","admin",f"Вход: {username}")
            return redirect("/")
        error="Неверный логин или пароль"
        logger.warning(f"Неудачный вход: {username}")
    return render_template_string(LOGIN_HTML,error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ==================== TELEGRAM ====================
TG_TOKEN   = "8757251631:AAHMFD4cg1dU9SdZ8-7HMDxy5qDUpSc5TIs"
TG_CHAT_ID = "1053431273"
TG_API     = f"https://api.telegram.org/bot{TG_TOKEN}"

def tg_send(text,reply_markup=None):
    try:
        data={"chat_id":TG_CHAT_ID,"text":text,"parse_mode":"HTML"}
        if reply_markup:
            data["reply_markup"]=json.dumps(reply_markup)
        requests.post(f"{TG_API}/sendMessage",data=data,timeout=5)
    except Exception as e:
        logger.error(f"tg_send error: {e}")

def tg_answer_callback(callback_id,text):
    try:
        requests.post(f"{TG_API}/answerCallbackQuery",
                      data={"callback_query_id":callback_id,"text":text},timeout=5)
    except: pass

def tg_notify_new_pin(pin_id,name,car,phone,pin):
    text=(f"🔑 <b>Новая заявка</b>\n\n"
          f"👤 {name}\n🚗 {car}\n📱 {phone}\n🔐 ПИН: <b>{pin}</b>")
    markup={"inline_keyboard":[[
        {"text":"✅ Одобрить","callback_data":f"approve:{pin_id}"},
        {"text":"❌ Отказать","callback_data":f"reject:{pin_id}"}
    ]]}
    tg_send(text,markup)

def tg_notify_balance_request(req_id,car,amount):
    text=(f"💰 <b>Заявка на пополнение</b>\n\n"
          f"🚗 {car}\n💵 {amount:,} сум")
    markup={"inline_keyboard":[[
        {"text":"✅ Одобрить","callback_data":f"bal_approve:{req_id}"},
        {"text":"❌ Отказать","callback_data":f"bal_reject:{req_id}"}
    ]]}
    tg_send(text,markup)

def tg_notify_approved(name,car,pin):
    tg_send(f"✅ <b>{name}</b> ({car}) одобрен!\nПИН: <b>{pin}</b>")

def tg_notify_rejected(name,car):
    tg_send(f"❌ Заявка <b>{name}</b> ({car}) отклонена")

def tg_notify_shift_start(car,name):
    tg_send(f"🟢 <b>Смена начата</b>\n🚗 {car} — {name}\n⏱ {datetime.now().strftime('%H:%M:%S')}")

def tg_notify_shift_end(car,name,revenue,orders):
    tg_send(f"🔴 <b>Смена завершена</b>\n🚗 {car} — {name}\n💰 {revenue:,} сум\n📦 {orders} заказов")

def tg_notify_order_completed(car,from_addr,to_addr,price):
    tg_send(f"✅ <b>Заказ завершён</b>\n🚗 {car}\n📍 {from_addr}→{to_addr}\n💰 {price:,} сум")

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
def add_log(action,car_number,details):
    try:
        with db_lock:
            conn=get_db()
            conn.execute(
                "INSERT INTO logs (action,car_number,details,created_at) VALUES (?,?,?,?)",
                (action,car_number or "—",details,time.time()))
            conn.commit()
            conn.close()
        logger.info(f"[{action}] {car_number}: {details}")
    except Exception as e:
        logger.error(f"add_log error: {e}")

def get_rating_db(car_number):
    try:
        conn=get_db()
        row=conn.execute(
            "SELECT ROUND(AVG(stars),1) as avg,COUNT(*) as cnt FROM ratings WHERE car_number=?",
            (car_number,)).fetchone()
        orders=conn.execute(
            "SELECT COUNT(*) FROM orders WHERE car_number=? AND status='completed'",
            (car_number,)).fetchone()[0]
        conn.close()
        avg=float(row["avg"]) if row["avg"] else 5.0
        return {"avg":avg,"count":row["cnt"],"orders":orders}
    except Exception as e:
        logger.error(f"get_rating_db error: {e}")
        return {"avg":5.0,"count":0,"orders":0}

def update_balance_db(car_number,amount,type_,comment=""):
    try:
        with db_lock:
            conn=get_db()
            conn.execute("UPDATE drivers SET balance=balance+? WHERE car_number=?",(amount,car_number))
            conn.execute(
                "INSERT INTO transactions (car_number,amount,type,comment,created_at) VALUES (?,?,?,?,?)",
                (car_number,amount,type_,comment,time.time()))
            new_b=conn.execute(
                "SELECT balance FROM drivers WHERE car_number=?",(car_number,)).fetchone()["balance"]
            conn.commit()
            conn.close()
        add_log("balance",car_number,f"{type_}: {amount:,} сум | Итого: {new_b:,} сум")
        return new_b
    except Exception as e:
        logger.error(f"update_balance_db error: {e}")
        return 0

def add_rating_db(car_number,stars):
    try:
        with db_lock:
            conn=get_db()
            conn.execute(
                "INSERT INTO ratings (car_number,stars,created_at) VALUES (?,?,?)",
                (car_number,stars,time.time()))
            conn.commit()
            conn.close()
        add_log("rating",car_number,f"Оценка: {stars}⭐")
    except Exception as e:
        logger.error(f"add_rating_db error: {e}")

def get_stars(avg):
    if avg>=4.8: return "⭐⭐⭐⭐⭐"
    if avg>=4.0: return "⭐⭐⭐⭐"
    if avg>=3.0: return "⭐⭐⭐"
    if avg>=2.0: return "⭐⭐"
    return "⭐"

def create_order_db(car_number,from_addr,to_addr,price,client="Диспетчер",distance="—"):
    try:
        with db_lock:
            conn=get_db()
            driver=conn.execute(
                "SELECT status FROM drivers WHERE car_number=?",(car_number,)).fetchone()
            if driver and driver["status"]=="busy":
                conn.close()
                return None,"Водитель уже на заказе"
            conn.execute(
                """UPDATE orders SET status='cancelled',cancelled_at=?,cancel_reason='Новый заказ'
                WHERE car_number=? AND status='pending'""",(time.time(),car_number))
            cursor=conn.execute(
                """INSERT INTO orders
                (car_number,from_address,to_address,distance,price,client,status,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (car_number,from_addr,to_addr,distance,price,client,"pending",time.time()))
            order_id=cursor.lastrowid
            conn.execute("UPDATE drivers SET status='busy' WHERE car_number=?",(car_number,))
            conn.commit()
            conn.close()
        add_log("create_order",car_number,f"Заказ #{order_id}: {from_addr}→{to_addr} | {price:,} сум")
        return order_id,None
    except Exception as e:
        logger.error(f"create_order_db error: {e}")
        return None,str(e)

def complete_order_db(order_id):
    try:
        with db_lock:
            conn=get_db()
            order=conn.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
            if not order:
                conn.close()
                return False,"Заказ не найден"
            if order["status"]!="pending":
                conn.close()
                return False,"Заказ уже обработан"
            car=order["car_number"]
            price=order["price"]
            conn.execute(
                "UPDATE orders SET status='completed',completed_at=? WHERE id=?",
                (time.time(),order_id))
            conn.execute("UPDATE drivers SET status='free' WHERE car_number=?",(car,))
            conn.execute(
                """UPDATE shifts SET revenue=revenue+?,orders_count=orders_count+1
                WHERE car_number=? AND end_time IS NULL""",(price,car))
            conn.commit()
            conn.close()
        add_log("complete_order",car,f"Заказ #{order_id} завершён | {price:,} сум")
        tg_notify_order_completed(car,order["from_address"],order["to_address"],price)
        return True,"Заказ завершён"
    except Exception as e:
        logger.error(f"complete_order_db error: {e}")
        return False,str(e)

def cancel_order_db(order_id,reason="Отменён диспетчером"):
    try:
        with db_lock:
            conn=get_db()
            order=conn.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
            if not order:
                conn.close()
                return False,"Заказ не найден"
            if order["status"]!="pending":
                conn.close()
                return False,"Заказ уже обработан"
            car=order["car_number"]
            conn.execute(
                "UPDATE orders SET status='cancelled',cancelled_at=?,cancel_reason=? WHERE id=?",
                (time.time(),reason,order_id))
            conn.execute("UPDATE drivers SET status='free' WHERE car_number=?",(car,))
            conn.commit()
            conn.close()
        add_log("cancel_order",car,f"Заказ #{order_id} отменён: {reason}")
        return True,"Заказ отменён"
    except Exception as e:
        logger.error(f"cancel_order_db error: {e}")
        return False,str(e)

def start_shift(car_number):
    try:
        with db_lock:
            conn=get_db()
            active=conn.execute(
                "SELECT id FROM shifts WHERE car_number=? AND end_time IS NULL",
                (car_number,)).fetchone()
            if active:
                conn.close()
                return False,"Смена уже начата"
            conn.execute(
                "INSERT INTO shifts (car_number,start_time,revenue,orders_count) VALUES (?,?,0,0)",
                (car_number,time.time()))
            conn.execute("UPDATE drivers SET status='free' WHERE car_number=?",(car_number,))
            driver=conn.execute(
                "SELECT name FROM drivers WHERE car_number=?",(car_number,)).fetchone()
            conn.commit()
            conn.close()
        name=driver["name"] if driver else car_number
        add_log("shift_start",car_number,f"Смена начата {datetime.now().strftime('%H:%M')}")
        tg_notify_shift_start(car_number,name)
        return True,"Смена начата"
    except Exception as e:
        logger.error(f"start_shift error: {e}")
        return False,str(e)

def end_shift(car_number):
    try:
        with db_lock:
            conn=get_db()
            shift=conn.execute(
                "SELECT * FROM shifts WHERE car_number=? AND end_time IS NULL",
                (car_number,)).fetchone()
            if not shift:
                conn.close()
                return False,"Нет активной смены"
            conn.execute(
                "UPDATE shifts SET end_time=? WHERE id=?",(time.time(),shift["id"]))
            conn.execute("UPDATE drivers SET status='offline' WHERE car_number=?",(car_number,))
            driver=conn.execute(
                "SELECT name FROM drivers WHERE car_number=?",(car_number,)).fetchone()
            conn.commit()
            conn.close()
        name=driver["name"] if driver else car_number
        add_log("shift_end",car_number,
                f"Смена завершена | {shift['revenue']:,} сум | {shift['orders_count']} заказов")
        tg_notify_shift_end(car_number,name,shift["revenue"],shift["orders_count"])
        return True,"Смена завершена"
    except Exception as e:
        logger.error(f"end_shift error: {e}")
        return False,str(e)

# ==================== ТАЙМАУТ ЗАКАЗОВ ====================
ORDER_TIMEOUT=60

def auto_timeout_orders():
    while True:
        try:
            with db_lock:
                conn=get_db()
                timeout_time=time.time()-(ORDER_TIMEOUT*60)
                old=conn.execute(
                    "SELECT * FROM orders WHERE status='pending' AND created_at<?",
                    (timeout_time,)).fetchall()
                for o in old:
                    conn.execute(
                        """UPDATE orders SET status='cancelled',cancelled_at=?,
                        cancel_reason='Таймаут' WHERE id=?""",(time.time(),o["id"]))
                    conn.execute(
                        "UPDATE drivers SET status='free' WHERE car_number=?",(o["car_number"],))
                    logger.info(f"⏰ Заказ #{o['id']} отменён по таймауту")
                conn.commit()
                conn.close()
        except Exception as e:
            logger.error(f"auto_timeout error: {e}")
        time.sleep(120)

threading.Thread(target=auto_timeout_orders,daemon=True).start()

# ==================== TELEGRAM POLLING ====================
tg_offset=0

def tg_polling():
    global tg_offset
    logger.info("🤖 Telegram бот запущен")
    while True:
        try:
            resp=requests.get(
                f"{TG_API}/getUpdates",
                params={"offset":tg_offset,"timeout":30},timeout=35)
            updates=resp.json().get("result",[])
            for upd in updates:
                tg_offset=upd["update_id"]+1
                if "callback_query" in upd:
                    cq=upd["callback_query"]
                    cq_id=cq["id"]
                    data=cq.get("data","")
                    if data.startswith("approve:"):
                        pin_id=data.split(":",1)[1]
                        with db_lock:
                            conn=get_db()
                            row=conn.execute(
                                "SELECT * FROM pending_pins WHERE id=?",(pin_id,)).fetchone()
                            if row and row["status"]=="pending":
                                conn.execute(
                                    "UPDATE pending_pins SET status='approved' WHERE id=?",(pin_id,))
                                conn.execute(
                                    """INSERT OR IGNORE INTO drivers
                                    (car_number,name,phone,pin,balance,status,created_at)
                                    VALUES (?,?,?,?,50000,'offline',?)""",
                                    (row["car_number"],row["name"],
                                     row["phone"],row["pin"],time.time()))
                                conn.commit()
                                conn.close()
                                tg_answer_callback(cq_id,"✅ Одобрено!")
                                tg_notify_approved(row["name"],row["car_number"],row["pin"])
                                add_log("approve_pin",row["car_number"],f"Одобрен: {row['name']}")
                            else:
                                conn.close()
                                tg_answer_callback(cq_id,"Не найдено")
                    elif data.startswith("reject:"):
                        pin_id=data.split(":",1)[1]
                        with db_lock:
                            conn=get_db()
                            row=conn.execute(
                                "SELECT * FROM pending_pins WHERE id=?",(pin_id,)).fetchone()
                            if row:
                                conn.execute(
                                    "UPDATE pending_pins SET status='rejected' WHERE id=?",(pin_id,))
                                conn.commit()
                                conn.close()
                                tg_answer_callback(cq_id,"❌ Отклонено")
                                tg_notify_rejected(row["name"],row["car_number"])
                            else:
                                conn.close()
                    elif data.startswith("bal_approve:"):
                        req_id=int(data.split(":",1)[1])
                        with db_lock:
                            conn=get_db()
                            row=conn.execute(
                                "SELECT * FROM balance_requests WHERE id=?",(req_id,)).fetchone()
                            if row and row["status"]=="pending":
                                car=row["car_number"]
                                amount=row["amount"]
                                conn.execute(
                                    "UPDATE drivers SET balance=balance+? WHERE car_number=?",
                                    (amount,car))
                                conn.execute(
                                    "UPDATE balance_requests SET status='approved' WHERE id=?",
                                    (req_id,))
                                conn.execute(
                                    """INSERT INTO transactions
                                    (car_number,amount,type,comment,created_at)
                                    VALUES (?,?,?,?,?)""",
                                    (car,amount,"deposit","Пополнение одобрено",time.time()))
                                new_b=conn.execute(
                                    "SELECT balance FROM drivers WHERE car_number=?",
                                    (car,)).fetchone()["balance"]
                                conn.commit()
                                conn.close()
                                tg_answer_callback(cq_id,"✅ Баланс пополнен!")
                                tg_send(f"✅ Баланс <b>{car}</b>: <b>{new_b:,} сум</b>")
                                add_log("balance_approve",car,f"{amount:,} сум")
                            else:
                                conn.close()
                                tg_answer_callback(cq_id,"Не найдено")
                    elif data.startswith("bal_reject:"):
                        req_id=int(data.split(":",1)[1])
                        with db_lock:
                            conn=get_db()
                            conn.execute(
                                "UPDATE balance_requests SET status='rejected' WHERE id=?",(req_id,))
                            conn.commit()
                            conn.close()
                        tg_answer_callback(cq_id,"❌ Отклонено")
                elif "message" in upd:
                    msg=upd["message"]
                    text_msg=msg.get("text","")
                    if text_msg=="/start":
                        tg_send(
                            "🚕 <b>TAXI 3042 Xazarasp</b>\n\n"
                            "/status — статус\n/drivers — водители\n"
                            "/orders — заказы\n/revenue — выручка\n"
                            "/rating — рейтинг\n/pending — заявки\n"
                            "/shifts — смены\n/logs — логи\n\n"
                            "<code>/order НОМЕР Откуда;Куда;Цена</code>")
                    elif text_msg=="/status":
                        conn=get_db()
                        free=conn.execute("SELECT COUNT(*) FROM drivers WHERE status='free'").fetchone()[0]
                        busy=conn.execute("SELECT COUNT(*) FROM drivers WHERE status='busy'").fetchone()[0]
                        total=conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
                        pending=conn.execute("SELECT COUNT(*) FROM pending_pins WHERE status='pending'").fetchone()[0]
                        active=conn.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
                        rev=conn.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed'").fetchone()[0]
                        conn.close()
                        tg_send(f"📊 <b>Статус</b>\n\n🟢 Свободны: {free}\n🔴 На заказе: {busy}\n"
                                f"👥 Всего: {total}\n📦 Акт.заказов: {active}\n"
                                f"⏳ Ждут ПИН: {pending}\n💰 Выручка: {rev:,} сум")
                    elif text_msg=="/drivers":
                        conn=get_db()
                        rows=conn.execute("SELECT * FROM drivers WHERE status!='offline'").fetchall()
                        conn.close()
                        if not rows: tg_send("Нет водителей онлайн")
                        else:
                            lines=[]
                            for d in rows:
                                icon="🟢" if d["status"]=="free" else "🔴"
                                lines.append(f"{icon} <b>{d['car_number']}</b> — {d['name']}\n   💰 {d['balance']:,} сум")
                            tg_send("🚗 <b>Водители онлайн:</b>\n\n"+"\n".join(lines))
                    elif text_msg=="/orders":
                        conn=get_db()
                        rows=conn.execute("SELECT * FROM orders WHERE status='pending'").fetchall()
                        conn.close()
                        if not rows: tg_send("Нет активных заказов")
                        else:
                            for o in rows:
                                tg_send(f"📦 <b>#{o['id']}</b> | {o['car_number']}\n"
                                        f"📍 {o['from_address']}→{o['to_address']}\n💰 {o['price']:,} сум")
                    elif text_msg=="/revenue":
                        conn=get_db()
                        rows=conn.execute(
                            """SELECT car_number,SUM(price) as rev FROM orders
                            WHERE status='completed' GROUP BY car_number ORDER BY rev DESC LIMIT 10"""
                        ).fetchall()
                        total=conn.execute(
                            "SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed'"
                        ).fetchone()[0]
                        conn.close()
                        if not rows: tg_send("Нет данных")
                        else:
                            lines=[f"🚗 <b>{r['car_number']}</b>: {r['rev']:,} сум" for r in rows]
                            tg_send("💰 <b>Выручка:</b>\n\n"+"\n".join(lines)+f"\n\n📊 Итого: <b>{total:,} сум</b>")
                    elif text_msg=="/rating":
                        conn=get_db()
                        rows=conn.execute(
                            """SELECT car_number,ROUND(AVG(stars),1) as avg,COUNT(*) as cnt
                            FROM ratings GROUP BY car_number ORDER BY avg DESC LIMIT 10"""
                        ).fetchall()
                        conn.close()
                        if not rows: tg_send("Нет данных")
                        else:
                            lines=[f"{i+1}. <b>{r['car_number']}</b> — ⭐{r['avg']} ({r['cnt']} оценок)"
                                   for i,r in enumerate(rows)]
                            tg_send("🏆 <b>Рейтинг:</b>\n\n"+"\n".join(lines))
                    elif text_msg=="/pending":
                        conn=get_db()
                        rows=conn.execute("SELECT * FROM pending_pins WHERE status='pending'").fetchall()
                        conn.close()
                        if not rows: tg_send("Нет заявок")
                        else:
                            for p in rows:
                                tg_send(f"⏳ <b>{p['name']}</b>\n🚗 {p['car_number']}\n"
                                        f"📱 {p['phone']}\n🔐 ПИН: <b>{p['pin']}</b>")
                    elif text_msg=="/shifts":
                        conn=get_db()
                        rows=conn.execute(
                            """SELECT s.*,d.name FROM shifts s
                            LEFT JOIN drivers d ON s.car_number=d.car_number
                            ORDER BY s.start_time DESC LIMIT 10"""
                        ).fetchall()
                        conn.close()
                        if not rows: tg_send("Нет смен")
                        else:
                            lines=[]
                            for r in rows:
                                start=datetime.fromtimestamp(r["start_time"]).strftime("%d.%m %H:%M")
                                end=datetime.fromtimestamp(r["end_time"]).strftime("%H:%M") if r["end_time"] else "▶"
                                lines.append(f"🚗 <b>{r['car_number']}</b>\n"
                                             f"   ⏱ {start}→{end} | 💰{r['revenue']:,} | 📦{r['orders_count']}")
                            tg_send("⏱ <b>Смены:</b>\n\n"+"\n\n".join(lines))
                    elif text_msg=="/logs":
                        conn=get_db()
                        rows=conn.execute(
                            "SELECT * FROM logs ORDER BY created_at DESC LIMIT 10").fetchall()
                        conn.close()
                        if not rows: tg_send("Нет логов")
                        else:
                            lines=[]
                            for r in rows:
                                t=datetime.fromtimestamp(r["created_at"]).strftime("%H:%M:%S")
                                lines.append(f"[{t}] <b>{r['action']}</b> {r['car_number']}\n{r['details']}")
                            tg_send("📝 <b>Логи:</b>\n\n"+"\n\n".join(lines))
                    elif text_msg.startswith("/order "):
                        try:
                            parts=text_msg.split(" ",2)
                            car=parts[1].strip().upper()
                            info=parts[2].split(";")
                            from_addr=info[0].strip()
                            to_addr=info[1].strip()
                            price=int(info[2].strip())
                            oid,err=create_order_db(car,from_addr,to_addr,price,"Telegram")
                            if oid: tg_send(f"✅ Заказ #{oid} → {car}\n{from_addr}→{to_addr}\n💰{price:,} сум")
                            else: tg_send(f"❌ {err}")
                        except Exception as e:
                            tg_send(f"❌ Ошибка: {e}\nФормат: /order НОМЕР Откуда;Куда;Цена")
        except Exception as e:
            logger.error(f"tg_polling error: {e}")
            time.sleep(5)

threading.Thread(target=tg_polling,daemon=True).start()

# ==================== ОНЛАЙН СТАТУС ВОДИТЕЛЯ ====================

@app.route("/api/driver/heartbeat", methods=["POST"])
def api_heartbeat():
    """Водитель каждые 30 сек отправляет сигнал"""
    try:
        data = request.json or {}
        car  = data.get("car_number", "").strip().upper()
        ok, err = validate_car_number(car)
        if not ok:
            return jsonify({"ok": False, "error": err})

        with db_lock:
            conn = get_db()
            # Обновить время последнего онлайна
            conn.execute(
                """UPDATE drivers 
                SET last_seen=?, status=CASE 
                    WHEN status='offline' THEN 'free' 
                    ELSE status END
                WHERE car_number=?""",
                (time.time(), car)
            )
            conn.commit()
            
            # Получить текущий заказ
            order = conn.execute(
                "SELECT * FROM orders WHERE car_number=? AND status='pending'",
                (car,)
            ).fetchone()
            
            driver = conn.execute(
                "SELECT * FROM drivers WHERE car_number=?", (car,)
            ).fetchone()
            conn.close()

        return jsonify({
            "ok": True,
            "status": driver["status"] if driver else "free",
            "balance": driver["balance"] if driver else 0,
            "active_order": dict(order) if order else None
        })
    except Exception as e:
        logger.error(f"heartbeat error: {e}")
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/driver/go_offline", methods=["POST"])
def api_go_offline():
    """Водитель уходит офлайн"""
    try:
        data = request.json or {}
        car  = data.get("car_number", "").strip().upper()
        ok, err = validate_car_number(car)
        if not ok:
            return jsonify({"ok": False, "error": err})

        with db_lock:
            conn = get_db()
            conn.execute(
                "UPDATE drivers SET status='offline' WHERE car_number=?",
                (car,)
            )
            conn.commit()
            conn.close()

        add_log("go_offline", car, "Водитель ушёл офлайн")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/driver/go_online", methods=["POST"])
def api_go_online():
    """Водитель выходит онлайн"""
    try:
        data = request.json or {}
        car  = data.get("car_number", "").strip().upper()
        pin  = data.get("pin", "").strip()

        ok, err = validate_car_number(car)
        if not ok:
            return jsonify({"ok": False, "error": err})

        conn = get_db()
        driver = conn.execute(
            "SELECT * FROM drivers WHERE car_number=? AND pin=?",
            (car, pin)
        ).fetchone()
        conn.close()

        if not driver:
            return jsonify({"ok": False, "error": "Неверный номер или ПИН"})

        with db_lock:
            conn = get_db()
            conn.execute(
                "UPDATE drivers SET status='free', last_seen=? WHERE car_number=?",
                (time.time(), car)
            )
            conn.commit()
            conn.close()

        add_log("go_online", car, "Водитель вышел онлайн")
        tg_send(f"🟢 Водитель <b>{car}</b> — {driver['name']} вышел онлайн!")
        return jsonify({
            "ok": True,
            "driver": {
                "car_number": driver["car_number"],
                "name":       driver["name"],
                "phone":      driver["phone"],
                "balance":    driver["balance"],
                "status":     "free"
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/driver/order_status", methods=["POST"])
def api_driver_order_status():
    """Водитель получает свой текущий заказ"""
    try:
        data = request.json or {}
        car  = data.get("car_number", "").strip().upper()
        ok, err = validate_car_number(car)
        if not ok:
            return jsonify({"ok": False, "error": err})

        conn = get_db()
        order = conn.execute(
            "SELECT * FROM orders WHERE car_number=? AND status='pending'",
            (car,)
        ).fetchone()
        driver = conn.execute(
            "SELECT balance, status FROM drivers WHERE car_number=?",
            (car,)
        ).fetchone()
        
        # Непрочитанные сообщения чата
        messages = conn.execute(
            """SELECT * FROM chat_messages 
            WHERE car_number=? 
            ORDER BY created_at DESC LIMIT 5""",
            (car,)
        ).fetchall()
        conn.close()

        return jsonify({
            "ok":          True,
            "active_order": dict(order) if order else None,
            "balance":     driver["balance"] if driver else 0,
            "status":      driver["status"] if driver else "offline",
            "messages":    [dict(m) for m in messages]
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ==================== API ====================
@app.route("/api/stats")
@login_required
def api_stats():
    try:
        conn=get_db()
        online=conn.execute("SELECT COUNT(*) FROM drivers WHERE status!='offline'").fetchone()[0]
        free=conn.execute("SELECT COUNT(*) FROM drivers WHERE status='free'").fetchone()[0]
        busy=conn.execute("SELECT COUNT(*) FROM drivers WHERE status='busy'").fetchone()[0]
        total=conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
        pending=conn.execute("SELECT COUNT(*) FROM pending_pins WHERE status='pending'").fetchone()[0]
        active=conn.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
        revenue=conn.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed'").fetchone()[0]
        today=conn.execute(
            "SELECT COUNT(*) FROM orders WHERE created_at>?",(time.time()-86400,)).fetchone()[0]
        conn.close()
        return jsonify({"ok":True,"stats":{
            "online":online,"free":free,"busy":busy,"total":total,
            "pending_pins":pending,"active_orders":active,
            "revenue":revenue,"today_orders":today}})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/drivers")
@login_required
def api_drivers():
    try:
        conn=get_db()
        rows=conn.execute("SELECT * FROM drivers ORDER BY status").fetchall()
        conn.close()
        result=[]
        for d in rows:
            r=get_rating_db(d["car_number"])
            result.append({"car_number":d["car_number"],"name":d["name"],
                "phone":d["phone"],"status":d["status"],"balance":d["balance"],
                "rating":r["avg"],"orders":r["orders"],"rating_count":r["count"]})
        return jsonify({"ok":True,"drivers":result})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/drivers/online")
@login_required
def api_drivers_online():
    try:
        conn=get_db()
        rows=conn.execute(
            "SELECT * FROM drivers WHERE status!='offline' ORDER BY status").fetchall()
        conn.close()
        result=[]
        for d in rows:
            r=get_rating_db(d["car_number"])
            result.append({"car_number":d["car_number"],"name":d["name"],
                "phone":d["phone"],"status":d["status"],"balance":d["balance"],
                "rating":r["avg"],"orders":r["orders"]})
        return jsonify({"ok":True,"drivers":result})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/driver/<car_number>")
@login_required
def api_driver_info(car_number):
    try:
        conn=get_db()
        d=conn.execute("SELECT * FROM drivers WHERE car_number=?",(car_number,)).fetchone()
        if not d:
            conn.close()
            return jsonify({"ok":False,"error":"Не найден"})
        order=conn.execute(
            "SELECT * FROM orders WHERE car_number=? AND status='pending'",(car_number,)).fetchone()
        shift=conn.execute(
            "SELECT * FROM shifts WHERE car_number=? AND end_time IS NULL",(car_number,)).fetchone()
        orders=conn.execute(
            "SELECT * FROM orders WHERE car_number=? ORDER BY created_at DESC LIMIT 10",
            (car_number,)).fetchall()
        conn.close()
        r=get_rating_db(car_number)
        return jsonify({"ok":True,
            "driver":{"car_number":d["car_number"],"name":d["name"],
                "phone":d["phone"],"status":d["status"],"balance":d["balance"],
                "rating":r["avg"],"orders":r["orders"],"rating_count":r["count"]},
            "active_order":dict(order) if order else None,
            "active_shift":dict(shift) if shift else None,
            "recent_orders":[dict(o) for o in orders]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/orders")
@login_required
def api_orders():
    try:
        status=request.args.get("status","all")
        conn=get_db()
        if status=="all":
            rows=conn.execute(
                "SELECT * FROM orders ORDER BY created_at DESC LIMIT 100").fetchall()
        else:
            rows=conn.execute(
                "SELECT * FROM orders WHERE status=? ORDER BY created_at DESC LIMIT 100",
                (status,)).fetchall()
        conn.close()
        return jsonify({"ok":True,"orders":[dict(o) for o in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/orders/create",methods=["POST"])
@login_required
def api_create_order():
    try:
        data=request.json or {}
        car=data.get("car_number","").strip().upper()
        from_addr=data.get("from_address","").strip()
        to_addr=data.get("to_address","").strip()
        price=data.get("price",0)
        client=data.get("client","Диспетчер").strip()
        if car!="ALL":
            ok,err=validate_car_number(car)
            if not ok: return jsonify({"ok":False,"error":err})
        ok,err=validate_address(from_addr)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err=validate_address(to_addr)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err=validate_price(price)
        if not ok: return jsonify({"ok":False,"error":err})
        if car=="ALL":
            conn=get_db()
            free=conn.execute("SELECT car_number FROM drivers WHERE status='free'").fetchall()
            conn.close()
            ids=[]
            for d in free:
                oid,_=create_order_db(d["car_number"],from_addr,to_addr,int(price),client)
                if oid: ids.append(oid)
            return jsonify({"ok":True,"order_ids":ids,"message":f"Отправлено {len(ids)} водителям"})
        oid,err=create_order_db(car,from_addr,to_addr,int(price),client)
        if oid: return jsonify({"ok":True,"order_id":oid})
        return jsonify({"ok":False,"error":err})
    except Exception as e:
        logger.error(f"api_create_order error: {e}")
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/orders/complete/<int:order_id>",methods=["POST"])
@login_required
def api_complete_order(order_id):
    ok,msg=complete_order_db(order_id)
    return jsonify({"ok":ok,"message":msg})

@app.route("/api/orders/cancel/<int:order_id>",methods=["POST"])
@login_required
def api_cancel_order(order_id):
    data=request.json or {}
    reason=data.get("reason","Отменён диспетчером")
    ok,msg=cancel_order_db(order_id,reason)
    return jsonify({"ok":ok,"message":msg})

@app.route("/api/orders/driver_cancel/<int:order_id>",methods=["POST"])
def api_driver_cancel(order_id):
    try:
        data=request.json or {}
        car=data.get("car_number","").strip().upper()
        reason=data.get("reason","Отменён водителем")
        ok,err=validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        conn=get_db()
        order=conn.execute(
            "SELECT * FROM orders WHERE id=? AND car_number=?",(order_id,car)).fetchone()
        conn.close()
        if not order: return jsonify({"ok":False,"error":"Заказ не найден"})
        ok,msg=cancel_order_db(order_id,reason)
        if ok: tg_send(f"⚠️ Водитель <b>{car}</b> отменил заказ #{order_id}\nПричина: {reason}")
        return jsonify({"ok":ok,"message":msg})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/shift/start",methods=["POST"])
@login_required
def api_shift_start():
    data=request.json or {}
    car=data.get("car_number","").strip().upper()
    ok,err=validate_car_number(car)
    if not ok: return jsonify({"ok":False,"error":err})
    ok,msg=start_shift(car)
    return jsonify({"ok":ok,"message":msg})

@app.route("/api/shift/end",methods=["POST"])
@login_required
def api_shift_end():
    data=request.json or {}
    car=data.get("car_number","").strip().upper()
    ok,err=validate_car_number(car)
    if not ok: return jsonify({"ok":False,"error":err})
    ok,msg=end_shift(car)
    return jsonify({"ok":ok,"message":msg})

@app.route("/api/shifts")
@login_required
def api_shifts():
    try:
        conn=get_db()
        rows=conn.execute(
            """SELECT s.*,d.name FROM shifts s
            LEFT JOIN drivers d ON s.car_number=d.car_number
            ORDER BY s.start_time DESC LIMIT 50""").fetchall()
        conn.close()
        return jsonify({"ok":True,"shifts":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/pins")
@login_required
def api_pins():
    try:
        conn=get_db()
        rows=conn.execute(
            "SELECT * FROM pending_pins ORDER BY created_at DESC").fetchall()
        conn.close()
        return jsonify({"ok":True,"pins":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/pins/approve/<pin_id>",methods=["POST"])
@login_required
def api_approve_pin(pin_id):
    try:
        with db_lock:
            conn=get_db()
            row=conn.execute(
                "SELECT * FROM pending_pins WHERE id=?",(pin_id,)).fetchone()
            if not row:
                conn.close()
                return jsonify({"ok":False,"error":"Не найдена"})
            if row["status"]!="pending":
                conn.close()
                return jsonify({"ok":False,"error":"Уже обработана"})
            conn.execute("UPDATE pending_pins SET status='approved' WHERE id=?",(pin_id,))
            conn.execute(
                """INSERT OR IGNORE INTO drivers
                (car_number,name,phone,pin,balance,status,created_at)
                VALUES (?,?,?,?,50000,'offline',?)""",
                (row["car_number"],row["name"],row["phone"],row["pin"],time.time()))
            conn.commit()
            conn.close()
        add_log("approve_pin",row["car_number"],f"Одобрен: {row['name']}")
        tg_notify_approved(row["name"],row["car_number"],row["pin"])
        return jsonify({"ok":True,"message":"Водитель одобрен"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/pins/reject/<pin_id>",methods=["POST"])
@login_required
def api_reject_pin(pin_id):
    try:
        with db_lock:
            conn=get_db()
            row=conn.execute(
                "SELECT * FROM pending_pins WHERE id=?",(pin_id,)).fetchone()
            if not row:
                conn.close()
                return jsonify({"ok":False,"error":"Не найдена"})
            conn.execute("UPDATE pending_pins SET status='rejected' WHERE id=?",(pin_id,))
            conn.commit()
            conn.close()
        add_log("reject_pin",row["car_number"],f"Отклонён: {row['name']}")
        tg_notify_rejected(row["name"],row["car_number"])
        return jsonify({"ok":True,"message":"Отклонено"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/balance/requests")
@login_required
def api_balance_requests():
    try:
        conn=get_db()
        rows=conn.execute(
            "SELECT * FROM balance_requests ORDER BY created_at DESC LIMIT 50").fetchall()
        conn.close()
        return jsonify({"ok":True,"requests":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/balance/request",methods=["POST"])
def api_balance_request():
    try:
        data=request.json or {}
        car=data.get("car_number","").strip().upper()
        amount=data.get("amount",0)
        ok,err=validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err=validate_amount(amount)
        if not ok: return jsonify({"ok":False,"error":err})
        with db_lock:
            conn=get_db()
            cur=conn.execute(
                "INSERT INTO balance_requests (car_number,amount,status,created_at) VALUES (?,?,?,?)",
                (car,int(amount),"pending",time.time()))
            req_id=cur.lastrowid
            conn.commit()
            conn.close()
        tg_notify_balance_request(req_id,car,int(amount))
        add_log("balance_request",car,f"Заявка: {int(amount):,} сум")
        return jsonify({"ok":True,"message":"Заявка отправлена"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/balance/approve/<int:req_id>",methods=["POST"])
@login_required
def api_approve_balance(req_id):
    try:
        with db_lock:
            conn=get_db()
            row=conn.execute(
                "SELECT * FROM balance_requests WHERE id=?",(req_id,)).fetchone()
            if not row or row["status"]!="pending":
                conn.close()
                return jsonify({"ok":False,"error":"Не найдена или уже обработана"})
            car=row["car_number"]
            amount=row["amount"]
            conn.execute("UPDATE drivers SET balance=balance+? WHERE car_number=?",(amount,car))
            conn.execute("UPDATE balance_requests SET status='approved' WHERE id=?",(req_id,))
            conn.execute(
                "INSERT INTO transactions (car_number,amount,type,comment,created_at) VALUES (?,?,?,?,?)",
                (car,amount,"deposit","Пополнение одобрено",time.time()))
            new_b=conn.execute(
                "SELECT balance FROM drivers WHERE car_number=?",(car,)).fetchone()["balance"]
            conn.commit()
            conn.close()
        add_log("balance_approve",car,f"{amount:,} сум → {new_b:,} сум")
        tg_send(f"✅ Баланс <b>{car}</b> пополнен!\n💰 <b>{new_b:,} сум</b>")
        return jsonify({"ok":True,"new_balance":new_b})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/balance/reject/<int:req_id>",methods=["POST"])
@login_required
def api_reject_balance(req_id):
    try:
        with db_lock:
            conn=get_db()
            row=conn.execute(
                "SELECT * FROM balance_requests WHERE id=?",(req_id,)).fetchone()
            if not row:
                conn.close()
                return jsonify({"ok":False,"error":"Не найдена"})
            conn.execute("UPDATE balance_requests SET status='rejected' WHERE id=?",(req_id,))
            conn.commit()
            conn.close()
        add_log("balance_reject",row["car_number"],f"Отклонено: {row['amount']:,} сум")
        return jsonify({"ok":True,"message":"Отклонено"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/balance/add",methods=["POST"])
@login_required
def api_balance_add():
    try:
        data=request.json or {}
        car=data.get("car_number","").strip().upper()
        amount=data.get("amount",0)
        comment=data.get("comment","Ручное пополнение")
        ok,err=validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err=validate_amount(abs(int(amount)))
        if not ok: return jsonify({"ok":False,"error":err})
        new_b=update_balance_db(car,int(amount),"manual",comment)
        return jsonify({"ok":True,"new_balance":new_b})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/rating")
@login_required
def api_rating():
    try:
        conn=get_db()
        rows=conn.execute(
            """SELECT r.car_number,ROUND(AVG(r.stars),1) as avg,COUNT(r.id) as cnt,d.name,
            (SELECT COUNT(*) FROM orders o WHERE o.car_number=r.car_number AND o.status='completed') as orders
            FROM ratings r LEFT JOIN drivers d ON r.car_number=d.car_number
            GROUP BY r.car_number ORDER BY avg DESC""").fetchall()
        conn.close()
        return jsonify({"ok":True,"ratings":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/rating/add",methods=["POST"])
@login_required
def api_add_rating():
    try:
        data=request.json or {}
        car=data.get("car_number","").strip().upper()
        stars=data.get("stars",5)
        ok,err=validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err=validate_stars(stars)
        if not ok: return jsonify({"ok":False,"error":err})
        add_rating_db(car,int(stars))
        return jsonify({"ok":True,"message":"Оценка добавлена"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/tariffs")
@login_required
def api_tariffs():
    try:
        conn=get_db()
        rows=conn.execute("SELECT * FROM tariffs").fetchall()
        conn.close()
        result={}
        for r in rows:
            result[r["zone"]]={"name":r["name"],"base_fare":r["base_fare"],
                "rate_per_km":r["rate_per_km"],"wait_rate":r["wait_rate"]}
        return jsonify({"ok":True,"tariffs":result})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/tariffs/update",methods=["POST"])
@login_required
def api_update_tariffs():
    try:
        data=request.json or {}
        with db_lock:
            conn=get_db()
            for zone,vals in data.items():
                conn.execute(
                    """INSERT INTO tariffs (zone,base_fare,rate_per_km,wait_rate)
                    VALUES (?,?,?,?)
                    ON CONFLICT(zone) DO UPDATE SET
                    base_fare=excluded.base_fare,
                    rate_per_km=excluded.rate_per_km,
                    wait_rate=excluded.wait_rate""",
                    (zone,vals.get("base_fare",5000),
                     vals.get("rate_per_km",2800),vals.get("wait_rate",500)))
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
        conn=get_db()
        rows=conn.execute(
            "SELECT * FROM logs ORDER BY created_at DESC LIMIT 100").fetchall()
        conn.close()
        return jsonify({"ok":True,"logs":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/chat/<car_number>")
@login_required
def api_chat_get(car_number):
    try:
        conn=get_db()
        rows=conn.execute(
            "SELECT * FROM chat_messages WHERE car_number=? ORDER BY created_at ASC LIMIT 100",
            (car_number,)).fetchall()
        conn.close()
        return jsonify({"ok":True,"messages":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/chat/send",methods=["POST"])
@login_required
def api_chat_send():
    try:
        data=request.json or {}
        car=data.get("car_number","").strip().upper()
        text=data.get("text","").strip()
        sender=data.get("sender","admin")
        ok,err=validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        if not text or len(text)>500:
            return jsonify({"ok":False,"error":"Неверное сообщение"})
        with db_lock:
            conn=get_db()
            conn.execute(
                "INSERT INTO chat_messages (car_number,text,sender,created_at) VALUES (?,?,?,?)",
                (car,text,sender,time.time()))
            conn.commit()
            conn.close()
        if sender=="admin":
            tg_send(f"💬 <b>Диспетчер → {car}</b>\n{text}")
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/finance")
@login_required
def api_finance():
    try:
        conn=get_db()
        total_rev=conn.execute(
            "SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed'").fetchone()[0]
        today_rev=conn.execute(
            "SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed' AND completed_at>?",
            (time.time()-86400,)).fetchone()[0]
        completed=conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status='completed'").fetchone()[0]
        avg_price=conn.execute(
            "SELECT COALESCE(AVG(price),0) FROM orders WHERE status='completed'").fetchone()[0]
        drivers=conn.execute(
            """SELECT o.car_number,d.name,SUM(o.price) as revenue,COUNT(o.id) as orders
            FROM orders o LEFT JOIN drivers d ON o.car_number=d.car_number
            WHERE o.status='completed' GROUP BY o.car_number ORDER BY revenue DESC""").fetchall()
        transactions=conn.execute(
            "SELECT * FROM transactions ORDER BY created_at DESC LIMIT 50").fetchall()
        conn.close()
        return jsonify({"ok":True,"total_revenue":total_rev,"today_revenue":today_rev,
            "completed_orders":completed,"avg_price":round(avg_price),
            "drivers":[dict(d) for d in drivers],
            "transactions":[dict(t) for t in transactions]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/register",methods=["POST"])
def api_register():
    try:
        data=request.json or {}
        name=data.get("name","").strip()
        car=data.get("car_number","").strip().upper()
        phone=data.get("phone","").strip()
        ok,err=validate_name(name)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err=validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        ok,err=validate_phone(phone)
        if not ok: return jsonify({"ok":False,"error":err})
        conn=get_db()
        existing=conn.execute(
            "SELECT car_number FROM drivers WHERE car_number=?",(car,)).fetchone()
        conn.close()
        if existing: return jsonify({"ok":False,"error":"Водитель уже зарегистрирован"})
        pin=str(random.randint(1000,9999))
        pin_id=f"{car}_{int(time.time())}"
        with db_lock:
            conn=get_db()
            conn.execute(
                "INSERT INTO pending_pins (id,name,car_number,phone,pin,status,created_at) VALUES (?,?,?,?,?,?,?)",
                (pin_id,name,car,phone,pin,"pending",time.time()))
            conn.commit()
            conn.close()
        tg_notify_new_pin(pin_id,name,car,phone,pin)
        add_log("register",car,f"Заявка: {name} | {phone}")
        return jsonify({"ok":True,"message":"Заявка отправлена. Ожидайте ПИН-код"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/login",methods=["POST"])
def api_login():
    try:
        data=request.json or {}
        car=data.get("car_number","").strip().upper()
        pin=data.get("pin","").strip()
        ok,err=validate_car_number(car)
        if not ok: return jsonify({"ok":False,"error":err})
        if not pin or len(pin)!=4:
            return jsonify({"ok":False,"error":"Неверный ПИН"})
        conn=get_db()
        driver=conn.execute(
            "SELECT * FROM drivers WHERE car_number=? AND pin=?",(car,pin)).fetchone()
        conn.close()
        if not driver: return jsonify({"ok":False,"error":"Неверный номер или ПИН"})
        add_log("driver_login",car,"Вход водителя")
        return jsonify({"ok":True,"driver":{
            "car_number":driver["car_number"],"name":driver["name"],
            "phone":driver["phone"],"balance":driver["balance"],"status":driver["status"]}})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/export")
@login_required
def api_export():
    try:
        from flask import Response
        conn=get_db()
        orders=conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
        drivers=conn.execute("SELECT * FROM drivers").fetchall()
        shifts=conn.execute(
            """SELECT s.*,d.name FROM shifts s
            LEFT JOIN drivers d ON s.car_number=d.car_number
            ORDER BY s.start_time DESC""").fetchall()
        conn.close()
        lines=["="*50,f"ОТЧЁТ TAXI 3042 XAZARASP",
               f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}","="*50,
               f"\nЗАКАЗЫ ({len(orders)}):"]
        for o in orders:
            lines.append(f"#{o['id']} | {o['car_number']} | {o['from_address']}→{o['to_address']} | {o['price']:,} сум | {o['status']}")
        lines.append(f"\nВОДИТЕЛИ ({len(drivers)}):")
        for d in drivers:
            lines.append(f"{d['car_number']} | {d['name']} | {d['phone']} | {d['balance']:,} сум")
        lines.append(f"\nСМЕНЫ ({len(shifts)}):")
        for s in shifts:
            start=datetime.fromtimestamp(s["start_time"]).strftime("%d.%m %H:%M")
            end=datetime.fromtimestamp(s["end_time"]).strftime("%H:%M") if s["end_time"] else "▶"
            lines.append(f"{s['car_number']} | {s['name']or'—'} | {start}→{end} | {s['revenue']:,} сум | {s['orders_count']} заказов")
        total=sum(o["price"] for o in orders if o["status"]=="completed")
        lines+=[f"\nИТОГО: {total:,} сум","="*50]
        return Response("\n".join(lines),mimetype="text/plain",
            headers={"Content-Disposition":f"attachment;filename=taxi_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

# ==================== ADMIN HTML ====================
ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAXI 3042 — Диспетчерская</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0a0a0a;--bg2:#111;--bg3:#161616;--bg4:#1c1c1c;
  --border:#222;--border2:#2a2a2a;
  --gold:#FFD600;--gold2:#e6c200;
  --green:#22c55e;--red:#ef4444;--blue:#3b82f6;--orange:#f97316;
  --text:#e5e5e5;--muted:#666;--muted2:#444;
  --r:10px;--r2:14px;--r3:20px;
}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;font-size:14px}
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}
.topbar{position:sticky;top:0;z-index:200;display:flex;align-items:center;gap:12px;
  background:rgba(10,10,10,.97);border-bottom:1px solid var(--border);
  padding:0 20px;height:54px;backdrop-filter:blur(10px)}
.logo{color:var(--gold);font-size:18px;font-weight:700;white-space:nowrap}
.logo span{color:var(--text);font-weight:400;font-size:13px;margin-left:6px}
.topbar-spacer{flex:1}
.pill{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}
.pill-live{background:#0a1f0a;color:var(--green);border:1px solid #1a3a1a}
.pill-tg{background:#001f3d;color:#60a5fa;border:1px solid #1a3a5a}
.pill-time{background:var(--bg2);color:var(--gold);border:1px solid var(--border);font-family:monospace;letter-spacing:.05em}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(1.3)}}
.logout-btn{padding:4px 12px;background:var(--bg3);color:var(--muted);border:1px solid var(--border);border-radius:20px;font-size:12px;cursor:pointer;text-decoration:none;transition:all .2s}
.logout-btn:hover{color:var(--red);border-color:var(--red)}
.tabs{display:flex;gap:0;border-bottom:1px solid var(--border);background:var(--bg);padding:0 20px;overflow-x:auto;position:sticky;top:54px;z-index:100}
.tab{padding:12px 16px;font-size:13px;font-weight:500;color:var(--muted);border:none;background:none;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;transition:all .2s}
.tab:hover{color:var(--text)}
.tab.active{color:var(--gold);border-bottom-color:var(--gold)}
.tab-badge{background:var(--red);color:#fff;padding:1px 5px;border-radius:8px;font-size:10px;margin-left:4px}
.page{display:none;padding:20px;max-width:1400px;margin:0 auto}
.page.active{display:block}
.stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-bottom:20px}
.stat{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);padding:16px;cursor:default;transition:all .2s}
.stat:hover{border-color:var(--gold);transform:translateY(-2px)}
.stat-val{font-size:26px;font-weight:700;color:var(--gold);line-height:1}
.stat-val.green{color:var(--green)}.stat-val.red{color:var(--red)}
.stat-val.blue{color:var(--blue)}.stat-val.orange{color:var(--orange)}
.stat-lbl{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-top:6px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);margin-bottom:16px;overflow:hidden}
.card-head{display:flex;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid var(--border);background:var(--bg3)}
.card-head h3{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--gold);flex:1}
.card-body{padding:16px}
table{width:100%;border-collapse:collapse}
th{background:var(--bg3);padding:9px 12px;text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;font-weight:500;white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.02)}
.btn{display:inline-flex;align-items:center;gap:4px;padding:5px 11px;border:none;border-radius:7px;cursor:pointer;font-size:12px;font-weight:600;transition:all .15s;white-space:nowrap}
.btn:hover{opacity:.85;transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
.btn-primary{background:var(--gold);color:#000}
.btn-success{background:#14532d;color:var(--green);border:1px solid #166534}
.btn-danger{background:#2a0000;color:var(--red);border:1px solid #4a0000}
.btn-info{background:#0c1a3a;color:var(--blue);border:1px solid #1e3a6a}
.btn-ghost{background:var(--bg3);color:var(--muted);border:1px solid var(--border)}
.btn-lg{padding:9px 20px;font-size:14px;border-radius:var(--r)}
.btn-send{padding:9px 24px;background:var(--gold);color:#000;border:none;border-radius:var(--r);font-size:14px;font-weight:700;cursor:pointer;transition:all .2s}
.btn-send:hover{background:var(--gold2)}
.btn-send:disabled{background:var(--border2);color:var(--muted);cursor:not-allowed}
.form-row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
.form-group{display:flex;flex-direction:column;gap:5px}
.form-group label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.form-control{background:var(--bg);color:var(--text);border:1px solid var(--border2);padding:8px 12px;border-radius:var(--r);font-size:13px;outline:none;transition:border-color .2s;min-width:0}
.form-control:focus{border-color:var(--gold)}
.form-control::placeholder{color:var(--muted2)}
select.form-control option{background:var(--bg2)}
.quick-addr{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.addr-chip{padding:4px 11px;background:var(--bg3);color:var(--muted);border:1px solid var(--border2);border-radius:20px;font-size:12px;cursor:pointer;transition:all .18s}
.addr-chip:hover{background:var(--gold);color:#000;border-color:var(--gold)}
.toast{display:none;margin-top:12px;padding:10px 16px;border-radius:var(--r);font-size:13px;font-weight:600}
.toast.ok{background:#0a2a0a;color:var(--green);border-left:3px solid var(--green)}
.toast.err{background:#2a0a0a;color:var(--red);border-left:3px solid var(--red)}
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600}
.badge-free{background:#0a2a0a;color:var(--green);border:1px solid #166534}
.badge-busy{background:#2a0a0a;color:var(--red);border:1px solid #7f1d1d}
.badge-offline{background:#1a1a1a;color:var(--muted);border:1px solid var(--border)}
.badge-pending{background:#2a1a00;color:var(--orange);border:1px solid #7c2d12}
.badge-ok{background:#0a2a0a;color:var(--green)}
.badge-rejected{background:#2a0a0a;color:var(--red)}
.car-num{color:var(--gold);font-weight:700;font-family:monospace;font-size:13px}
.pin-code{color:var(--gold);font-weight:700;font-family:monospace;font-size:18px;letter-spacing:.15em}
.chat-layout{display:grid;grid-template-columns:220px 1fr;gap:12px;height:500px}
.chat-drivers{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);overflow-y:auto}
.chat-driver-item{padding:10px 14px;border-bottom:1px solid var(--border);cursor:pointer;transition:background .15s}
.chat-driver-item:hover{background:var(--bg3)}
.chat-driver-item.active{background:var(--bg4);border-left:2px solid var(--gold)}
.chat-driver-name{font-size:13px;font-weight:600}
.chat-driver-car{font-size:11px;color:var(--muted);font-family:monospace}
.chat-main{display:flex;flex-direction:column;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);overflow:hidden}
.chat-header{padding:10px 16px;border-bottom:1px solid var(--border);background:var(--bg3);font-weight:600;font-size:13px}
.chat-messages{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
.chat-msg{padding:8px 12px;border-radius:10px;max-width:65%;font-size:13px;line-height:1.5}
.chat-msg.own{background:var(--gold);color:#000;align-self:flex-end;border-bottom-right-radius:3px}
.chat-msg.other{background:var(--bg4);color:var(--text);align-self:flex-start;border-bottom-left-radius:3px}
.chat-msg .msg-time{font-size:10px;opacity:.6;margin-top:3px}
.chat-bottom{padding:10px;border-top:1px solid var(--border);display:flex;gap:8px;flex-direction:column}
.chat-input-row{display:flex;gap:8px}
.chat-input-row input{flex:1}
.quick-replies{display:flex;gap:6px;flex-wrap:wrap}
.qr-btn{padding:4px 10px;background:var(--bg3);color:var(--muted);border:1px solid var(--border2);border-radius:20px;font-size:11px;cursor:pointer;transition:all .18s}
.qr-btn:hover{background:var(--gold);color:#000;border-color:var(--gold)}
.driver-card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);padding:14px 16px;margin-bottom:10px;display:flex;align-items:center;gap:14px;transition:all .2s}
.driver-card:hover{border-color:var(--border2);transform:translateY(-1px)}
.rank-num{font-size:24px;font-weight:700;color:var(--muted2);min-width:32px;text-align:center}
.rank-1{color:#FFD600}.rank-2{color:#a8a8a8}.rank-3{color:#cd7f32}
.dc-info{flex:1}
.dc-car{color:var(--gold);font-weight:700;font-size:15px;font-family:monospace}
.dc-sub{color:var(--muted);font-size:12px;margin-top:3px}
.dc-right{text-align:right}
.dc-avg{font-size:24px;font-weight:700;color:var(--gold)}
.rating-bar{background:var(--bg3);border-radius:3px;height:5px;width:100px;margin:4px 0 4px auto;overflow:hidden}
.rating-fill{background:var(--gold);height:100%;border-radius:3px;transition:width .5s}
.rev-item{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border)}
.rev-item:last-child{border:none}
.rev-bar-bg{flex:1;background:var(--bg3);border-radius:3px;height:8px;overflow:hidden}
.rev-bar-fill{height:100%;background:linear-gradient(90deg,var(--gold2),var(--gold));border-radius:3px;transition:width .5s}
.tariff-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:16px}
.tariff-card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);padding:16px}
.tariff-card h4{color:var(--gold);font-size:13px;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em}
.tariff-input-group{margin-bottom:8px}
.tariff-input-group label{color:var(--muted);font-size:11px;display:block;margin-bottom:4px}
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:500;align-items:center;justify-content:center;padding:20px}
.modal-bg.open{display:flex}
.modal{background:var(--bg2);border:1px solid var(--border2);border-radius:var(--r3);padding:24px;width:100%;max-width:460px;position:relative;max-height:90vh;overflow-y:auto}
.modal h3{color:var(--gold);font-size:17px;margin-bottom:16px}
.modal-close{position:absolute;top:14px;right:16px;background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer;padding:2px 6px;border-radius:5px}
.modal-close:hover{background:var(--bg4)}
.info-row{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--border);font-size:13px}
.info-row:last-child{border:none}
.info-lbl{color:var(--muted)}
.info-val{font-weight:600}
.modal-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
.modal-actions .btn{flex:1;justify-content:center;padding:9px}
.notifications{position:fixed;top:64px;right:16px;z-index:1000;display:flex;flex-direction:column;gap:8px;pointer-events:none}
.notif{background:var(--bg2);border:1px solid var(--border2);border-radius:var(--r);padding:12px 16px;font-size:13px;max-width:300px;pointer-events:all;animation:slideIn .3s ease;box-shadow:0 4px 24px rgba(0,0,0,.5)}
.notif.notif-success{border-left:3px solid var(--green)}
.notif.notif-warning{border-left:3px solid var(--orange)}
.notif.notif-info{border-left:3px solid var(--blue)}
.notif.notif-error{border-left:3px solid var(--red)}
@keyframes slideIn{from{transform:translateX(120%);opacity:0}to{transform:translateX(0);opacity:1}}
@keyframes slideOut{from{opacity:1}to{transform:translateX(120%);opacity:0}}
.notif.removing{animation:slideOut .3s ease forwards}
.empty{text-align:center;padding:40px 20px;color:var(--muted2)}
.empty-icon{font-size:36px;margin-bottom:10px}
@media(max-width:768px){
  .topbar{padding:0 12px}.page{padding:12px}
  .form-row{flex-direction:column}
  .chat-layout{grid-template-columns:1fr;height:auto}
  .chat-drivers{height:150px}.chat-main{height:400px}
  .btn-lg{width:100%;justify-content:center}
  .stats-row{grid-template-columns:repeat(2,1fr)}
}
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">🚕 TAXI 3042 <span>XAZARASP</span></div>
  <div class="topbar-spacer"></div>
  <div class="pill pill-time" id="clock">00:00:00</div>
  <div class="pill pill-live"><span class="dot"></span>Live</div>
  <div class="pill pill-tg">🤖 TG</div>
  <a href="/logout" class="logout-btn">🚪 Выход</a>
</div>
<div class="tabs">
  <button class="tab active" onclick="switchTab('dash',this)">📊 Дашборд</button>
  <button class="tab" onclick="switchTab('order',this)">📦 Заказы <span class="tab-badge" id="badge-orders" style="display:none">0</span></button>
  <button class="tab" onclick="switchTab('drivers',this)">🚗 Водители <span class="tab-badge" id="badge-pins" style="display:none">0</span></button>
  <button class="tab" onclick="switchTab('shifts',this)">⏱ Смены</button>
  <button class="tab" onclick="switchTab('rating',this)">🏆 Рейтинг</button>
  <button class="tab" onclick="switchTab('chat',this)">💬 Чат</button>
  <button class="tab" onclick="switchTab('finance',this)">💰 Финансы</button>
  <button class="tab" onclick="switchTab('settings',this)">⚙️ Настройки</button>
</div>
<div class="notifications" id="notifBox"></div>

<div class="page active" id="page-dash">
  <div class="stats-row">
    <div class="stat"><div class="stat-val" id="s-online">0</div><div class="stat-lbl">На линии</div></div>
    <div class="stat"><div class="stat-val green" id="s-free">0</div><div class="stat-lbl">Свободны</div></div>
    <div class="stat"><div class="stat-val red" id="s-busy">0</div><div class="stat-lbl">На заказе</div></div>
    <div class="stat"><div class="stat-val orange" id="s-pending">0</div><div class="stat-lbl">Ждут ПИН</div></div>
    <div class="stat"><div class="stat-val" id="s-total">0</div><div class="stat-lbl">Всего водит.</div></div>
    <div class="stat"><div class="stat-val blue" id="s-orders">0</div><div class="stat-lbl">Акт.заказов</div></div>
    <div class="stat"><div class="stat-val green" id="s-revenue">0</div><div class="stat-lbl">Выручка</div></div>
    <div class="stat"><div class="stat-val blue" id="s-today">0</div><div class="stat-lbl">Сегодня</div></div>
  </div>
  <div class="card">
    <div class="card-head"><h3>⚡ Быстрые действия</h3></div>
    <div class="card-body" style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn btn-primary btn-lg" onclick="switchTab('order',null)">📦 Новый заказ</button>
      <button class="btn btn-info btn-lg" onclick="switchTab('chat',null)">💬 Чат</button>
      <button class="btn btn-success btn-lg" onclick="switchTab('shifts',null)">⏱ Смены</button>
      <button class="btn btn-ghost btn-lg" onclick="window.open('/api/export')">📥 Экспорт</button>
    </div>
  </div>
  <div class="card">
    <div class="card-head">
      <h3>🚗 Водители онлайн</h3>
      <span id="last-upd" style="color:var(--muted2);font-size:11px"></span>
    </div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Авто</th><th>Водитель</th><th>Статус</th><th>Баланс</th><th>Рейтинг</th><th>Заказов</th><th>Действия</th></tr></thead>
        <tbody id="tbody-dash-drivers">
          <tr><td colspan="7"><div class="empty"><div class="empty-icon">🚗</div>Нет водителей онлайн</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <div class="card-head"><h3>📦 Активные заказы</h3></div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>#</th><th>Водитель</th><th>Откуда</th><th>Куда</th><th>Цена</th><th>Клиент</th><th>Время</th><th>Действия</th></tr></thead>
        <tbody id="tbody-dash-orders">
          <tr><td colspan="8"><div class="empty"><div class="empty-icon">📦</div>Нет активных заказов</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="page" id="page-order">
  <div class="card">
    <div class="card-head"><h3>📦 Новый заказ</h3></div>
    <div class="card-body">
      <div class="form-row">
        <div class="form-group">
          <label>Водитель</label>
          <select class="form-control" id="o-car" style="width:200px">
            <option value="">— Выбрать —</option>
            <option value="ALL">📢 Всем свободным</option>
          </select>
        </div>
        <div class="form-group">
          <label>Откуда</label>
          <input class="form-control" id="o-from" placeholder="Адрес подачи" style="width:170px">
        </div>
        <div class="form-group">
          <label>Куда</label>
          <input class="form-control" id="o-to" placeholder="Адрес назначения" style="width:170px">
        </div>
        <div class="form-group">
          <label>Цена (сум)</label>
          <input class="form-control" id="o-price" type="number" placeholder="0" style="width:120px" min="0" step="500">
        </div>
        <div class="form-group">
          <label>Клиент</label>
          <input class="form-control" id="o-client" placeholder="Имя/тел" style="width:140px">
        </div>
        <div class="form-group">
          <label>&nbsp;</label>
          <button class="btn-send" id="btn-order" onclick="createOrder()">🚀 Отправить</button>
        </div>
      </div>
      <div class="quick-addr">
        <span style="color:var(--muted2);font-size:11px">Быстро:</span>
        <button class="addr-chip" onclick="setAddr('Bozor')">📍 Bozor</button>
        <button class="addr-chip" onclick="setAddr('Aeroport')">✈️ Aeroport</button>
        <button class="addr-chip" onclick="setAddr('Kasalxona')">🏥 Kasalxona</button>
        <button class="addr-chip" onclick="setAddr('Vokzal')">🚉 Vokzal</button>
        <button class="addr-chip" onclick="setAddr('Maktab')">🏫 Maktab</button>
        <button class="addr-chip" onclick="setAddr('Markaziy')">🛒 Markaziy</button>
        <button class="addr-chip" onclick="setAddr('Poliklinika')">💊 Poliklinika</button>
        <button class="addr-chip" onclick="setAddr('Dokon')">🏪 Dokon</button>
      </div>
      <div class="toast" id="o-toast"></div>
    </div>
  </div>
  <div class="card">
    <div class="card-head">
      <h3>📋 История заказов</h3>
      <div style="display:flex;gap:8px;align-items:center">
        <select class="form-control" id="filter-status" onchange="loadOrders()" style="width:130px;padding:4px 8px">
          <option value="all">Все</option>
          <option value="pending">Активные</option>
          <option value="completed">Завершённые</option>
          <option value="cancelled">Отменённые</option>
        </select>
        <button class="btn btn-ghost" onclick="loadOrders()">↻</button>
      </div>
    </div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>#</th><th>Водитель</th><th>Откуда→Куда</th><th>Цена</th><th>Клиент</th><th>Статус</th><th>Время</th><th>Действия</th></tr></thead>
        <tbody id="tbody-orders">
          <tr><td colspan="8"><div class="empty"><div class="empty-icon">📋</div>Нет заказов</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="page" id="page-drivers">
  <div class="card">
    <div class="card-head">
      <h3>🔑 Заявки на регистрацию</h3>
      <span id="pin-count" style="background:var(--orange);color:#000;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;display:none"></span>
    </div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Имя</th><th>Телефон</th><th>Авто</th><th>ПИН</th><th>Статус</th><th>Время</th><th>Действия</th></tr></thead>
        <tbody id="tbody-pins">
          <tr><td colspan="7"><div class="empty"><div class="empty-icon">🔑</div>Нет заявок</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <div class="card-head"><h3>💳 Заявки на пополнение</h3></div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Авто</th><th>Сумма</th><th>Статус</th><th>Время</th><th>Действия</th></tr></thead>
        <tbody id="tbody-bal-reqs">
          <tr><td colspan="5"><div class="empty"><div class="empty-icon">💳</div>Нет заявок</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <div class="card-head">
      <h3>👥 Все водители</h3>
      <button class="btn btn-ghost" onclick="loadAllDrivers()">↻</button>
    </div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Авто</th><th>Имя</th><th>Телефон</th><th>Статус</th><th>Баланс</th><th>Рейтинг</th><th>Заказов</th><th>Действия</th></tr></thead>
        <tbody id="tbody-all-drivers">
          <tr><td colspan="8"><div class="empty"><div class="empty-icon">👥</div>Нет водителей</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="page" id="page-shifts">
  <div class="card">
    <div class="card-head"><h3>⏱ Управление сменой</h3></div>
    <div class="card-body">
      <div class="form-row" style="margin-bottom:16px">
        <div class="form-group">
          <label>Водитель</label>
          <select class="form-control" id="shift-car" style="width:220px">
            <option value="">— Выбрать —</option>
          </select>
        </div>
        <div class="form-group">
          <label>&nbsp;</label>
          <div style="display:flex;gap:8px">
            <button class="btn btn-success btn-lg" onclick="startShift()">🟢 Начать смену</button>
            <button class="btn btn-danger btn-lg" onclick="endShift()">🔴 Закончить смену</button>
          </div>
        </div>
      </div>
      <div class="toast" id="shift-toast"></div>
    </div>
  </div>
  <div class="card">
    <div class="card-head"><h3>🟢 Активные смены</h3><button class="btn btn-ghost" onclick="loadShifts()">↻</button></div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Водитель</th><th>Авто</th><th>Начало</th><th>Длит.</th><th>Выручка</th><th>Заказов</th><th>Действия</th></tr></thead>
        <tbody id="tbody-active-shifts">
          <tr><td colspan="7"><div class="empty"><div class="empty-icon">⏱</div>Нет активных смен</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <div class="card-head"><h3>📋 История смен</h3></div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Водитель</th><th>Авто</th><th>Начало</th><th>Конец</th><th>Выручка</th><th>Заказов</th></tr></thead>
        <tbody id="tbody-shifts-history">
          <tr><td colspan="6"><div class="empty"><div class="empty-icon">📋</div>Нет истории</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="page" id="page-rating">
  <div class="card">
    <div class="card-head"><h3>🏆 Рейтинг водителей</h3><button class="btn btn-ghost" onclick="loadRating()">↻</button></div>
    <div class="card-body">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-bottom:16px;padding-bottom:16px;border-bottom:1px solid var(--border)">
        <div class="form-group">
          <label>Водитель</label>
          <select class="form-control" id="r-car" style="width:180px"><option value="">— Выбрать —</option></select>
        </div>
        <div class="form-group">
          <label>Оценка</label>
          <select class="form-control" id="r-stars" style="width:130px">
            <option value="5">⭐⭐⭐⭐⭐ 5</option>
            <option value="4">⭐⭐⭐⭐ 4</option>
            <option value="3">⭐⭐⭐ 3</option>
            <option value="2">⭐⭐ 2</option>
            <option value="1">⭐ 1</option>
          </select>
        </div>
        <button class="btn btn-primary btn-lg" onclick="addRating()">✅ Добавить</button>
      </div>
      <div id="rating-list"><div class="empty"><div class="empty-icon">🏆</div>Нет данных</div></div>
    </div>
  </div>
</div>

<div class="page" id="page-chat">
  <div class="card">
    <div class="card-head"><h3>💬 Чат с водителями</h3></div>
    <div class="card-body" style="padding:0">
      <div class="chat-layout">
        <div class="chat-drivers" id="chat-driver-list">
          <div style="padding:10px 14px;border-bottom:1px solid var(--border);font-size:11px;color:var(--muted);text-transform:uppercase">Водители</div>
        </div>
        <div class="chat-main">
          <div class="chat-header" id="chat-header">💬 Выберите водителя</div>
          <div class="chat-messages" id="chat-messages">
            <div class="empty"><div class="empty-icon">💬</div>Выберите водителя слева</div>
          </div>
          <div class="chat-bottom">
            <div class="quick-replies">
              <button class="qr-btn" onclick="setMsg('Заказ готов, выезжайте')">🚀 Заказ готов</button>
              <button class="qr-btn" onclick="setMsg('Клиент ждёт')">⏰ Клиент ждёт</button>
              <button class="qr-btn" onclick="setMsg('Вернитесь в зону')">📍 Вернитесь</button>
              <button class="qr-btn" onclick="setMsg('Позвоните в диспетчерскую')">📞 Позвоните</button>
              <button class="qr-btn" onclick="setMsg('Заказ отменён')">❌ Отмена</button>
              <button class="qr-btn" onclick="setMsg('Хорошая работа!')">👍 Молодец</button>
            </div>
            <div class="chat-input-row">
              <input class="form-control" id="chat-input" placeholder="Написать..." onkeydown="if(event.key==='Enter')sendMsg()">
              <button class="btn btn-primary" onclick="sendMsg()">📤</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="page" id="page-finance">
  <div class="stats-row" style="margin-bottom:16px">
    <div class="stat"><div class="stat-val green" id="f-total-rev">0</div><div class="stat-lbl">Общая выручка</div></div>
    <div class="stat"><div class="stat-val blue" id="f-today-rev">0</div><div class="stat-lbl">Сегодня</div></div>
    <div class="stat"><div class="stat-val orange" id="f-orders-count">0</div><div class="stat-lbl">Завершено</div></div>
    <div class="stat"><div class="stat-val" id="f-avg-price">0</div><div class="stat-lbl">Средний чек</div></div>
  </div>
  <div class="card">
    <div class="card-head"><h3>💰 Выручка по водителям</h3><button class="btn btn-ghost" onclick="loadFinance()">↻</button></div>
    <div class="card-body" id="revenue-list"><div class="empty"><div class="empty-icon">💰</div>Нет данных</div></div>
  </div>
  <div class="card">
    <div class="card-head"><h3>📊 Транзакции</h3></div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Авто</th><th>Сумма</th><th>Тип</th><th>Комментарий</th><th>Время</th></tr></thead>
        <tbody id="tbody-transactions">
          <tr><td colspan="5"><div class="empty"><div class="empty-icon">📊</div>Нет транзакций</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="page" id="page-settings">
  <div class="card">
    <div class="card-head"><h3>💰 Тарифы</h3></div>
    <div class="card-body">
      <div class="tariff-grid">
        <div class="tariff-card">
          <h4>🏙️ Город день</h4>
          <div class="tariff-input-group"><label>Посадка (сум)</label><input class="form-control" id="t-city_day-base" type="number" value="5000"></div>
          <div class="tariff-input-group"><label>За км (сум)</label><input class="form-control" id="t-city_day-km" type="number" value="2800"></div>
          <div class="tariff-input-group"><label>Ожидание/мин</label><input class="form-control" id="t-city_day-wait" type="number" value="500"></div>
        </div>
        <div class="tariff-card">
          <h4>🌙 Город ночь</h4>
          <div class="tariff-input-group"><label>Посадка (сум)</label><input class="form-control" id="t-city_night-base" type="number" value="7000"></div>
          <div class="tariff-input-group"><label>За км (сум)</label><input class="form-control" id="t-city_night-km" type="number" value="3500"></div>
          <div class="tariff-input-group"><label>Ожидание/мин</label><input class="form-control" id="t-city_night-wait" type="number" value="700"></div>
        </div>
        <div class="tariff-card">
          <h4>🌳 Загород день</h4>
          <div class="tariff-input-group"><label>Посадка (сум)</label><input class="form-control" id="t-suburb_day-base" type="number" value="5000"></div>
          <div class="tariff-input-group"><label>За км (сум)</label><input class="form-control" id="t-suburb_day-km" type="number" value="3000"></div>
          <div class="tariff-input-group"><label>Ожидание/мин</label><input class="form-control" id="t-suburb_day-wait" type="number" value="500"></div>
        </div>
        <div class="tariff-card">
          <h4>🌙🌳 Загород ночь</h4>
          <div class="tariff-input-group"><label>Посадка (сум)</label><input class="form-control" id="t-suburb_night-base" type="number" value="7000"></div>
          <div class="tariff-input-group"><label>За км (сум)</label><input class="form-control" id="t-suburb_night-km" type="number" value="3800"></div>
          <div class="tariff-input-group"><label>Ожидание/мин</label><input class="form-control" id="t-suburb_night-wait" type="number" value="700"></div>
        </div>
        <div class="tariff-card">
          <h4>✈️ Аэропорт</h4>
          <div class="tariff-input-group"><label>Посадка (сум)</label><input class="form-control" id="t-airport-base" type="number" value="10000"></div>
          <div class="tariff-input-group"><label>За км (сум)</label><input class="form-control" id="t-airport-km" type="number" value="3500"></div>
          <div class="tariff-input-group"><label>Ожидание/мин</label><input class="form-control" id="t-airport-wait" type="number" value="500"></div>
        </div>
        <div class="tariff-card">
          <h4>🚉 Вокзал</h4>
          <div class="tariff-input-group"><label>Посадка (сум)</label><input class="form-control" id="t-vokzal-base" type="number" value="8000"></div>
          <div class="tariff-input-group"><label>За км (сум)</label><input class="form-control" id="t-vokzal-km" type="number" value="3000"></div>
          <div class="tariff-input-group"><label>Ожидание/мин</label><input class="form-control" id="t-vokzal-wait" type="number" value="500"></div>
        </div>
      </div>
      <button class="btn btn-primary btn-lg" onclick="saveTariffs()">💾 Сохранить тарифы</button>
      <div class="toast" id="tariff-toast" style="margin-top:12px"></div>
    </div>
  </div>
  <div class="card">
    <div class="card-head"><h3>📝 Журнал действий</h3><button class="btn btn-ghost" onclick="loadLogs()">↻</button></div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Время</th><th>Действие</th><th>Авто</th><th>Детали</th></tr></thead>
        <tbody id="tbody-logs">
          <tr><td colspan="4"><div class="empty"><div class="empty-icon">📝</div>Нет логов</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="modal-bg" id="modal-driver">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('modal-driver')">✕</button>
    <h3 id="modal-driver-title">Водитель</h3>
    <div id="modal-driver-body"></div>
    <div class="modal-actions" id="modal-driver-actions"></div>
  </div>
</div>
<div class="modal-bg" id="modal-order">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('modal-order')">✕</button>
    <h3 id="modal-order-title">Заказ</h3>
    <div id="modal-order-body"></div>
    <div class="modal-actions" id="modal-order-actions"></div>
  </div>
</div>

<script>
let currentTab='dash',currentChatCar=null;

function switchTab(tab,el){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('page-'+tab).classList.add('active');
  if(el) el.classList.add('active');
  currentTab=tab;
  if(tab==='dash') loadDash();
  if(tab==='order'){loadOrders();loadDriversSelect();}
  if(tab==='drivers'){loadPins();loadBalanceReqs();loadAllDrivers();}
  if(tab==='shifts'){loadShifts();loadDriversSelect();}
  if(tab==='rating'){loadRating();loadDriversSelect();}
  if(tab==='chat') loadChatDrivers();
  if(tab==='finance') loadFinance();
  if(tab==='settings'){loadTariffs();loadLogs();}
}

function showNotif(text,type='info'){
  const box=document.getElementById('notifBox');
  const n=document.createElement('div');
  n.className=`notif notif-${type}`;
  n.innerHTML=text;
  box.appendChild(n);
  setTimeout(()=>{n.classList.add('removing');setTimeout(()=>n.remove(),300);},3500);
}

function showToast(id,text,type='ok'){
  const el=document.getElementById(id);
  if(!el) return;
  el.className=`toast ${type}`;
  el.textContent=text;
  el.style.display='block';
  setTimeout(()=>el.style.display='none',3000);
}

function closeModal(id){document.getElementById(id).classList.remove('open');}

function fmtTime(ts){
  if(!ts) return '—';
  return new Date(ts*1000).toLocaleTimeString('ru',{hour:'2-digit',minute:'2-digit'});
}
function fmtDate(ts){
  if(!ts) return '—';
  const d=new Date(ts*1000);
  return d.toLocaleDateString('ru',{day:'2-digit',month:'2-digit'})+' '+
         d.toLocaleTimeString('ru',{hour:'2-digit',minute:'2-digit'});
}
function fmtDur(start,end){
  const diff=((end||Date.now()/1000)-start);
  const h=Math.floor(diff/3600),m=Math.floor((diff%3600)/60);
  return h>0?`${h}ч ${m}м`:`${m}м`;
}
function fmtMoney(n){return Number(n||0).toLocaleString('ru')+' сум';}
function getStars(avg){
  if(avg>=4.8) return '⭐⭐⭐⭐⭐';
  if(avg>=4.0) return '⭐⭐⭐⭐';
  if(avg>=3.0) return '⭐⭐⭐';
  if(avg>=2.0) return '⭐⭐';
  return '⭐';
}
function badgeStatus(s){
  const m={
    'free':'<span class="badge badge-free">🟢 Свободен</span>',
    'busy':'<span class="badge badge-busy">🔴 На заказе</span>',
    'offline':'<span class="badge badge-offline">⚫ Офлайн</span>',
    'pending':'<span class="badge badge-pending">🟡 Ожидание</span>',
    'completed':'<span class="badge badge-ok">✅ Завершён</span>',
    'cancelled':'<span class="badge badge-rejected">❌ Отменён</span>',
    'approved':'<span class="badge badge-ok">✅ Одобрен</span>',
    'rejected':'<span class="badge badge-rejected">❌ Отклонён</span>',
  };
  return m[s]||s;
}

setInterval(()=>{
  document.getElementById('clock').textContent=
    new Date().toLocaleTimeString('ru',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
},1000);

async function loadDash(){
  try{
    const [sRes,dRes,oRes]=await Promise.all([
      fetch('/api/stats'),fetch('/api/drivers/online'),fetch('/api/orders?status=pending')]);
    const [sData,dData,oData]=await Promise.all([sRes.json(),dRes.json(),oRes.json()]);
    if(sData.ok){
      const s=sData.stats;
      document.getElementById('s-online').textContent=s.online;
      document.getElementById('s-free').textContent=s.free;
      document.getElementById('s-busy').textContent=s.busy;
      document.getElementById('s-pending').textContent=s.pending_pins;
      document.getElementById('s-total').textContent=s.total;
      document.getElementById('s-orders').textContent=s.active_orders;
      document.getElementById('s-revenue').textContent=(s.revenue/1000).toFixed(0)+'K';
      document.getElementById('s-today').textContent=s.today_orders;
      const bo=document.getElementById('badge-orders');
      const bp=document.getElementById('badge-pins');
      if(s.active_orders>0){bo.style.display='';bo.textContent=s.active_orders;}
      else bo.style.display='none';
      if(s.pending_pins>0){bp.style.display='';bp.textContent=s.pending_pins;}
      else bp.style.display='none';
    }
    const dt=document.getElementById('tbody-dash-drivers');
    if(dData.ok&&dData.drivers.length>0){
      dt.innerHTML=dData.drivers.map(d=>`
        <tr>
          <td><span class="car-num">${d.car_number}</span></td>
          <td>${d.name||'—'}</td>
          <td>${badgeStatus(d.status)}</td>
          <td>${fmtMoney(d.balance)}</td>
          <td>${getStars(d.rating)} ${d.rating}</td>
          <td>${d.orders}</td>
          <td><div style="display:flex;gap:4px">
            <button class="btn btn-info" onclick="showDriverModal('${d.car_number}')">👁</button>
            <button class="btn btn-primary" onclick="quickOrder('${d.car_number}')">📦</button>
            <button class="btn btn-ghost" onclick="openChat('${d.car_number}','${d.name||d.car_number}')">💬</button>
          </div></td>
        </tr>`).join('');
    } else {
      dt.innerHTML='<tr><td colspan="7"><div class="empty"><div class="empty-icon">🚗</div>Нет водителей онлайн</div></td></tr>';
    }
    const ot=document.getElementById('tbody-dash-orders');
    if(oData.ok&&oData.orders.length>0){
      ot.innerHTML=oData.orders.map(o=>`
        <tr>
          <td>#${o.id}</td>
          <td><span class="car-num">${o.car_number}</span></td>
          <td>${o.from_address}</td>
          <td>${o.to_address}</td>
          <td>${fmtMoney(o.price)}</td>
          <td>${o.client||'—'}</td>
          <td>${fmtDate(o.created_at)}</td>
          <td><div style="display:flex;gap:4px">
            <button class="btn btn-success" onclick="completeOrder(${o.id})">✅</button>
            <button class="btn btn-danger" onclick="cancelOrder(${o.id})">❌</button>
          </div></td>
        </tr>`).join('');
    } else {
      ot.innerHTML='<tr><td colspan="8"><div class="empty"><div class="empty-icon">📦</div>Нет активных заказов</div></td></tr>';
    }
    document.getElementById('last-upd').textContent='Обновлено: '+fmtTime(Date.now()/1000);
  }catch(e){console.error('loadDash:',e);}
}

function setAddr(addr){
  const f=document.getElementById('o-from'),t=document.getElementById('o-to');
  if(!f.value) f.value=addr;
  else if(!t.value) t.value=addr;
  else f.value=addr;
}

async function loadDriversSelect(){
  try{
    const res=await fetch('/api/drivers/online');
    const data=await res.json();
    ['o-car','shift-car','r-car'].forEach(id=>{
      const sel=document.getElementById(id);
      if(!sel) return;
      const cur=sel.value;
      const fixed=Array.from(sel.options).filter(o=>o.value===''||o.value==='ALL').map(o=>o.outerHTML).join('');
      sel.innerHTML=fixed;
      if(data.ok) data.drivers.forEach(d=>{
        const opt=document.createElement('option');
        opt.value=d.car_number;
        opt.textContent=`${d.car_number} — ${d.name||'—'}`;
        sel.appendChild(opt);
      });
      sel.value=cur;
    });
  }catch(e){}
}

async function createOrder(){
  const car=document.getElementById('o-car').value;
  const from=document.getElementById('o-from').value.trim();
  const to=document.getElementById('o-to').value.trim();
  const price=document.getElementById('o-price').value;
  const client=document.getElementById('o-client').value.trim();
  if(!car||!from||!to||!price){showToast('o-toast','❌ Заполните все поля','err');return;}
  const btn=document.getElementById('btn-order');
  btn.disabled=true;btn.textContent='⏳...';
  try{
    const res=await fetch('/api/orders/create',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({car_number:car,from_address:from,to_address:to,
        price:parseInt(price),client:client||'Диспетчер'})});
    const data=await res.json();
    if(data.ok){
      showToast('o-toast',`✅ Заказ создан! ${data.message||'#'+data.order_id}`,'ok');
      showNotif(`📦 Заказ отправлен → <b>${car}</b>`,'success');
      ['o-from','o-to','o-price','o-client'].forEach(id=>document.getElementById(id).value='');
      loadOrders();
    } else showToast('o-toast','❌ '+data.error,'err');
  }catch(e){showToast('o-toast','❌ Ошибка сервера','err');}
  finally{btn.disabled=false;btn.textContent='🚀 Отправить';}
}

async function loadOrders(){
  try{
    const status=document.getElementById('filter-status')?.value||'all';
    const res=await fetch(`/api/orders?status=${status}`);
    const data=await res.json();
    const tbody=document.getElementById('tbody-orders');
    if(data.ok&&data.orders.length>0){
      tbody.innerHTML=data.orders.map(o=>`
        <tr>
          <td>#${o.id}</td>
          <td><span class="car-num">${o.car_number}</span></td>
          <td style="max-width:200px">${o.from_address}→${o.to_address}</td>
          <td>${fmtMoney(o.price)}</td>
          <td>${o.client||'—'}</td>
          <td>${badgeStatus(o.status)}</td>
          <td>${fmtDate(o.created_at)}</td>
          <td><div style="display:flex;gap:4px">
            ${o.status==='pending'?`
              <button class="btn btn-success" onclick="completeOrder(${o.id})">✅</button>
              <button class="btn btn-danger" onclick="cancelOrder(${o.id})">❌</button>`:''}
            <button class="btn btn-ghost" onclick='showOrderModal(${o.id},${JSON.stringify(o)})'>👁</button>
          </div></td>
        </tr>`).join('');
    } else {
      tbody.innerHTML='<tr><td colspan="8"><div class="empty"><div class="empty-icon">📋</div>Нет заказов</div></td></tr>';
    }
  }catch(e){}
}

async function completeOrder(id){
  if(!confirm(`Завершить заказ #${id}?`)) return;
  try{
    const res=await fetch(`/api/orders/complete/${id}`,{method:'POST'});
    const data=await res.json();
    if(data.ok){showNotif('✅ Заказ завершён','success');loadDash();loadOrders();}
    else showNotif('❌ '+data.message,'error');
  }catch(e){}
}

async function cancelOrder(id){
  if(!confirm(`Отменить заказ #${id}?`)) return;
  try{
    const res=await fetch(`/api/orders/cancel/${id}`,{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({reason:'Отменён диспетчером'})});
    const data=await res.json();
    if(data.ok){showNotif('❌ Заказ отменён','warning');loadDash();loadOrders();}
    else showNotif('❌ '+data.message,'error');
  }catch(e){}
}

function quickOrder(car){
  document.getElementById('o-car').value=car;
  switchTab('order',document.querySelectorAll('.tab')[1]);
}

async function loadPins(){
  try{
    const res=await fetch('/api/pins');
    const data=await res.json();
    const tbody=document.getElementById('tbody-pins');
    if(data.ok&&data.pins.length>0){
      const pending=data.pins.filter(p=>p.status==='pending');
      const pc=document.getElementById('pin-count');
      if(pending.length>0){pc.style.display='';pc.textContent=pending.length+' новых';}
      else pc.style.display='none';
      tbody.innerHTML=data.pins.map(p=>`
        <tr>
          <td>${p.name}</td><td>${p.phone}</td>
          <td><span class="car-num">${p.car_number}</span></td>
          <td><span class="pin-code">${p.pin}</span></td>
          <td>${badgeStatus(p.status)}</td>
          <td>${fmtDate(p.created_at)}</td>
          <td>${p.status==='pending'?`<div style="display:flex;gap:4px">
            <button class="btn btn-success" onclick="approvePin('${p.id}')">✅ Одобрить</button>
            <button class="btn btn-danger" onclick="rejectPin('${p.id}')">❌ Отказать</button>
          </div>`:'—'}</td>
        </tr>`).join('');
    } else {
      tbody.innerHTML='<tr><td colspan="7"><div class="empty"><div class="empty-icon">🔑</div>Нет заявок</div></td></tr>';
    }
  }catch(e){}
}

async function approvePin(id){
  try{
    const res=await fetch(`/api/pins/approve/${id}`,{method:'POST'});
    const data=await res.json();
    if(data.ok){showNotif('✅ Водитель одобрен!','success');loadPins();}
    else showNotif('❌ '+data.error,'error');
  }catch(e){}
}

async function rejectPin(id){
  if(!confirm('Отклонить заявку?')) return;
  try{
    const res=await fetch(`/api/pins/reject/${id}`,{method:'POST'});
    const data=await res.json();
    if(data.ok){showNotif('❌ Заявка отклонена','warning');loadPins();}
  }catch(e){}
}

async function loadBalanceReqs(){
  try{
    const res=await fetch('/api/balance/requests');
    const data=await res.json();
    const tbody=document.getElementById('tbody-bal-reqs');
    if(data.ok&&data.requests.length>0){
      tbody.innerHTML=data.requests.map(r=>`
        <tr>
          <td><span class="car-num">${r.car_number}</span></td>
          <td>${fmtMoney(r.amount)}</td>
          <td>${badgeStatus(r.status)}</td>
          <td>${fmtDate(r.created_at)}</td>
          <td>${r.status==='pending'?`<div style="display:flex;gap:4px">
            <button class="btn btn-success" onclick="approveBalance(${r.id})">✅</button>
            <button class="btn btn-danger" onclick="rejectBalance(${r.id})">❌</button>
          </div>`:'—'}</td>
        </tr>`).join('');
    } else {
      tbody.innerHTML='<tr><td colspan="5"><div class="empty"><div class="empty-icon">💳</div>Нет заявок</div></td></tr>';
    }
  }catch(e){}
}

async function approveBalance(id){
  try{
    const res=await fetch(`/api/balance/approve/${id}`,{method:'POST'});
    const data=await res.json();
    if(data.ok){showNotif('✅ Баланс пополнен!','success');loadBalanceReqs();}
    else showNotif('❌ '+data.error,'error');
  }catch(e){}
}

async function rejectBalance(id){
  if(!confirm('Отклонить?')) return;
  try{
    const res=await fetch(`/api/balance/reject/${id}`,{method:'POST'});
    const data=await res.json();
    if(data.ok){showNotif('❌ Отклонено','warning');loadBalanceReqs();}
  }catch(e){}
}

async function loadAllDrivers(){
  try{
    const res=await fetch('/api/drivers');
    const data=await res.json();
    const tbody=document.getElementById('tbody-all-drivers');
    if(data.ok&&data.drivers.length>0){
      tbody.innerHTML=data.drivers.map(d=>`
        <tr>
          <td><span class="car-num">${d.car_number}</span></td>
          <td>${d.name||'—'}</td><td>${d.phone||'—'}</td>
          <td>${badgeStatus(d.status)}</td>
          <td>${fmtMoney(d.balance)}</td>
          <td>${getStars(d.rating)} ${d.rating}</td>
          <td>${d.orders}</td>
          <td><div style="display:flex;gap:4px">
            <button class="btn btn-info" onclick="showDriverModal('${d.car_number}')">👁</button>
            <button class="btn btn-ghost" onclick="openChat('${d.car_number}','${d.name||d.car_number}')">💬</button>
          </div></td>
        </tr>`).join('');
    } else {
      tbody.innerHTML='<tr><td colspan="8"><div class="empty"><div class="empty-icon">👥</div>Нет водителей</div></td></tr>';
    }
  }catch(e){}
}

async function startShift(){
  const car=document.getElementById('shift-car').value;
  if(!car){showToast('shift-toast','❌ Выберите водителя','err');return;}
  try{
    const res=await fetch('/api/shift/start',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({car_number:car})});
    const data=await res.json();
    if(data.ok){showToast('shift-toast','✅ '+data.message,'ok');showNotif(`🟢 Смена начата: <b>${car}</b>`,'success');loadShifts();}
    else showToast('shift-toast','❌ '+data.message,'err');
  }catch(e){}
}

async function endShift(){
  const car=document.getElementById('shift-car').value;
  if(!car){showToast('shift-toast','❌ Выберите водителя','err');return;}
  if(!confirm(`Завершить смену ${car}?`)) return;
  try{
    const res=await fetch('/api/shift/end',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({car_number:car})});
    const data=await res.json();
    if(data.ok){showToast('shift-toast','✅ '+data.message,'ok');showNotif(`🔴 Смена завершена: <b>${car}</b>`,'warning');loadShifts();}
    else showToast('shift-toast','❌ '+data.message,'err');
  }catch(e){}
}

async function endShiftDirect(car){
  if(!confirm(`Завершить смену ${car}?`)) return;
  try{
    const res=await fetch('/api/shift/end',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({car_number:car})});
    const data=await res.json();
    if(data.ok){showNotif(`🔴 Смена завершена: <b>${car}</b>`,'warning');loadShifts();}
  }catch(e){}
}

async function loadShifts(){
  try{
    const res=await fetch('/api/shifts');
    const data=await res.json();
    if(!data.ok) return;
    const active=data.shifts.filter(s=>!s.end_time);
    const history=data.shifts.filter(s=>s.end_time);
    const at=document.getElementById('tbody-active-shifts');
    if(active.length>0){
      at.innerHTML=active.map(s=>`
        <tr>
          <td>${s.name||'—'}</td>
          <td><span class="car-num">${s.car_number}</span></td>
          <td>${fmtDate(s.start_time)}</td>
          <td>${fmtDur(s.start_time,null)}</td>
          <td>${fmtMoney(s.revenue)}</td>
          <td>${s.orders_count}</td>
          <td><button class="btn btn-danger" onclick="endShiftDirect('${s.car_number}')">🔴 Завершить</button></td>
        </tr>`).join('');
    } else {
      at.innerHTML='<tr><td colspan="7"><div class="empty"><div class="empty-icon">⏱</div>Нет активных смен</div></td></tr>';
    }
    const ht=document.getElementById('tbody-shifts-history');
    if(history.length>0){
      ht.innerHTML=history.map(s=>`
        <tr>
          <td>${s.name||'—'}</td>
          <td><span class="car-num">${s.car_number}</span></td>
          <td>${fmtDate(s.start_time)}</td>
          <td>${fmtDate(s.end_time)}</td>
          <td>${fmtMoney(s.revenue)}</td>
          <td>${s.orders_count}</td>
        </tr>`).join('');
    } else {
      ht.innerHTML='<tr><td colspan="6"><div class="empty"><div class="empty-icon">📋</div>Нет истории</div></td></tr>';
    }
  }catch(e){}
}

async function loadRating(){
  try{
    const res=await fetch('/api/rating');
    const data=await res.json();
    const list=document.getElementById('rating-list');
    if(data.ok&&data.ratings.length>0){
      list.innerHTML=data.ratings.map((r,i)=>`
        <div class="driver-card">
          <div class="rank-num ${i===0?'rank-1':i===1?'rank-2':i===2?'rank-3':''}">${i+1}</div>
          <div class="dc-info">
            <div class="dc-car">${r.car_number}</div>
            <div class="dc-sub">${r.name||'—'} • ${r.orders||0} заказов • ${r.cnt} оценок</div>
          </div>
          <div class="dc-right">
            <div class="dc-avg">${r.avg}</div>
            <div>${getStars(r.avg)}</div>
            <div class="rating-bar"><div class="rating-fill" style="width:${r.avg/5*100}%"></div></div>
          </div>
        </div>`).join('');
    } else {
      list.innerHTML='<div class="empty"><div class="empty-icon">🏆</div>Нет данных</div>';
    }
  }catch(e){}
}

async function addRating(){
  const car=document.getElementById('r-car').value;
  const stars=document.getElementById('r-stars').value;
  if(!car){showNotif('❌ Выберите водителя','error');return;}
  try{
    const res=await fetch('/api/rating/add',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({car_number:car,stars:parseInt(stars)})});
    const data=await res.json();
    if(data.ok){showNotif(`⭐ Оценка ${stars} → <b>${car}</b>`,'success');loadRating();}
    else showNotif('❌ '+data.error,'error');
  }catch(e){}
}

async function loadChatDrivers(){
  try{
    const res=await fetch('/api/drivers/online');
    const data=await res.json();
    const list=document.getElementById('chat-driver-list');
    const hdr='<div style="padding:10px 14px;border-bottom:1px solid var(--border);font-size:11px;color:var(--muted);text-transform:uppercase">Водители</div>';
    if(data.ok&&data.drivers.length>0){
      list.innerHTML=hdr+data.drivers.map(d=>`
        <div class="chat-driver-item ${currentChatCar===d.car_number?'active':''}"
             onclick="openChat('${d.car_number}','${d.name||d.car_number}')">
          <div class="chat-driver-name">${d.name||d.car_number}</div>
          <div class="chat-driver-car">${d.car_number}</div>
        </div>`).join('');
    } else {
      list.innerHTML=hdr+'<div class="empty" style="padding:20px"><div class="empty-icon">🚗</div>Нет водителей</div>';
    }
  }catch(e){}
}

function openChat(car,name){
  currentChatCar=car;
  document.getElementById('chat-header').textContent=`💬 ${name||car} (${car})`;
  if(currentTab!=='chat') switchTab('chat',document.querySelectorAll('.tab')[5]);
  loadChatMessages(car);
  loadChatDrivers();
}

async function loadChatMessages(car){
  try{
    const res=await fetch(`/api/chat/${car}`);
    const data=await res.json();
    const box=document.getElementById('chat-messages');
    if(data.ok&&data.messages.length>0){
      box.innerHTML=data.messages.map(m=>`
        <div class="chat-msg ${m.sender==='admin'?'own':'other'}">
          ${m.text}
          <div class="msg-time">${fmtTime(m.created_at)}</div>
        </div>`).join('');
      box.scrollTop=box.scrollHeight;
    } else {
      box.innerHTML='<div class="empty"><div class="empty-icon">💬</div>Нет сообщений</div>';
    }
  }catch(e){}
}

function setMsg(text){
  document.getElementById('chat-input').value=text;
  document.getElementById('chat-input').focus();
}

async function sendMsg(){
  if(!currentChatCar){showNotif('❌ Выберите водителя','error');return;}
  const input=document.getElementById('chat-input');
  const text=input.value.trim();
  if(!text) return;
  try{
    const res=await fetch('/api/chat/send',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({car_number:currentChatCar,text,sender:'admin'})});
    const data=await res.json();
    if(data.ok){input.value='';loadChatMessages(currentChatCar);}
  }catch(e){}
}

async function loadFinance(){
  try{
    const res=await fetch('/api/finance');
    const data=await res.json();
    if(!data.ok) return;
    document.getElementById('f-total-rev').textContent=fmtMoney(data.total_revenue);
    document.getElementById('f-today-rev').textContent=fmtMoney(data.today_revenue);
    document.getElementById('f-orders-count').textContent=data.completed_orders;
    document.getElementById('f-avg-price').textContent=fmtMoney(data.avg_price);
    const list=document.getElementById('revenue-list');
    const maxRev=data.drivers.length>0?data.drivers[0].revenue:1;
    if(data.drivers.length>0){
      list.innerHTML=data.drivers.map(d=>`
        <div class="rev-item">
          <div style="min-width:130px">
            <span class="car-num">${d.car_number}</span><br>
            <span style="color:var(--muted);font-size:11px">${d.name||'—'}</span>
          </div>
          <div class="rev-bar-bg"><div class="rev-bar-fill" style="width:${Math.round(d.revenue/maxRev*100)}%"></div></div>
          <div style="min-width:120px;text-align:right;font-weight:700;color:var(--gold)">${fmtMoney(d.revenue)}</div>
          <div style="min-width:50px;text-align:right;color:var(--muted);font-size:12px">${d.orders} зак.</div>
        </div>`).join('');
    } else {
      list.innerHTML='<div class="empty"><div class="empty-icon">💰</div>Нет данных</div>';
    }
    const tbody=document.getElementById('tbody-transactions');
    if(data.transactions&&data.transactions.length>0){
      tbody.innerHTML=data.transactions.map(t=>`
        <tr>
          <td><span class="car-num">${t.car_number}</span></td>
          <td style="color:${t.amount>0?'var(--green)':'var(--red)'}">
            ${t.amount>0?'+':''}${fmtMoney(t.amount)}</td>
          <td>${t.type}</td><td>${t.comment||'—'}</td>
          <td>${fmtDate(t.created_at)}</td>
        </tr>`).join('');
    } else {
      tbody.innerHTML='<tr><td colspan="5"><div class="empty"><div class="empty-icon">📊</div>Нет транзакций</div></td></tr>';
    }
  }catch(e){}
}

async function loadTariffs(){
  try{
    const res=await fetch('/api/tariffs');
    const data=await res.json();
    if(!data.ok||!data.tariffs) return;
    const t=data.tariffs;
    ['city_day','city_night','suburb_day','suburb_night','airport','vokzal'].forEach(zone=>{
      if(t[zone]){
        const b=document.getElementById(`t-${zone}-base`);
        const k=document.getElementById(`t-${zone}-km`);
        const w=document.getElementById(`t-${zone}-wait`);
        if(b) b.value=t[zone].base_fare;
        if(k) k.value=t[zone].rate_per_km;
        if(w) w.value=t[zone].wait_rate;
      }
    });
  }catch(e){}
}

async function saveTariffs(){
  const zones=['city_day','city_night','suburb_day','suburb_night','airport','vokzal'];
  const tariffs={};
  zones.forEach(zone=>{
    const b=document.getElementById(`t-${zone}-base`);
    const k=document.getElementById(`t-${zone}-km`);
    const w=document.getElementById(`t-${zone}-wait`);
    if(b&&k&&w) tariffs[zone]={base_fare:+b.value,rate_per_km:+k.value,wait_rate:+w.value};
  });
  try{
    const res=await fetch('/api/tariffs/update',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(tariffs)});
    const data=await res.json();
    if(data.ok){showToast('tariff-toast','✅ Тарифы сохранены!','ok');showNotif('💰 Тарифы обновлены','success');}
    else showToast('tariff-toast','❌ '+data.error,'err');
  }catch(e){}
}

async function loadLogs(){
  try{
    const res=await fetch('/api/logs');
    const data=await res.json();
    const tbody=document.getElementById('tbody-logs');
    if(data.ok&&data.logs.length>0){
      tbody.innerHTML=data.logs.map(l=>`
        <tr>
          <td style="font-family:monospace;font-size:12px">${fmtDate(l.created_at)}</td>
          <td><span class="badge badge-pending">${l.action}</span></td>
          <td><span class="car-num">${l.car_number||'—'}</span></td>
          <td style="color:var(--muted);font-size:12px">${l.details||'—'}</td>
        </tr>`).join('');
    } else {
      tbody.innerHTML='<tr><td colspan="4"><div class="empty"><div class="empty-icon">📝</div>Нет логов</div></td></tr>';
    }
  }catch(e){}
}

async function showDriverModal(car){
  try{
    const res=await fetch(`/api/driver/${car}`);
    const data=await res.json();
    if(!data.ok) return;
    const d=data.driver,o=data.active_order,s=data.active_shift;
    document.getElementById('modal-driver-title').textContent=`🚗 ${d.car_number} — ${d.name||'—'}`;
    document.getElementById('modal-driver-body').innerHTML=`
      <div class="info-row"><span class="info-lbl">Статус</span><span class="info-val">${badgeStatus(d.status)}</span></div>
      <div class="info-row"><span class="info-lbl">Телефон</span><span class="info-val">${d.phone||'—'}</span></div>
      <div class="info-row"><span class="info-lbl">Баланс</span><span class="info-val" style="color:var(--gold)">${fmtMoney(d.balance)}</span></div>
      <div class="info-row"><span class="info-lbl">Рейтинг</span><span class="info-val">${getStars(d.rating)} ${d.rating} (${d.rating_count} оц.)</span></div>
      <div class="info-row"><span class="info-lbl">Заказов</span><span class="info-val">${d.orders}</span></div>
      ${o?`<div class="info-row"><span class="info-lbl">Тек.заказ</span><span class="info-val">${o.from_address}→${o.to_address}</span></div>`:''}
      ${s?`<div class="info-row"><span class="info-lbl">Смена</span><span class="info-val">с ${fmtTime(s.start_time)} (${fmtDur(s.start_time,null)})</span></div>`:''}
    `;
    document.getElementById('modal-driver-actions').innerHTML=`
      <button class="btn btn-primary" onclick="quickOrder('${car}');closeModal('modal-driver')">📦 Заказ</button>
      <button class="btn btn-info" onclick="openChat('${car}','${d.name||car}');closeModal('modal-driver')">💬 Чат</button>
      ${!s?`<button class="btn btn-success" onclick="startShiftFor('${car}')">🟢 Начать смену</button>`:''}
      ${s?`<button class="btn btn-danger" onclick="endShiftDirect('${car}');closeModal('modal-driver')">🔴 Завершить смену</button>`:''}
    `;
    document.getElementById('modal-driver').classList.add('open');
  }catch(e){}
}

function showOrderModal(id,o){
  document.getElementById('modal-order-title').textContent=`📦 Заказ #${o.id}`;
  document.getElementById('modal-order-body').innerHTML=`
    <div class="info-row"><span class="info-lbl">Водитель</span><span class="info-val car-num">${o.car_number}</span></div>
    <div class="info-row"><span class="info-lbl">Откуда</span><span class="info-val">${o.from_address}</span></div>
    <div class="info-row"><span class="info-lbl">Куда</span><span class="info-val">${o.to_address}</span></div>
    <div class="info-row"><span class="info-lbl">Цена</span><span class="info-val" style="color:var(--gold)">${fmtMoney(o.price)}</span></div>
    <div class="info-row"><span class="info-lbl">Клиент</span><span class="info-val">${o.client||'—'}</span></div>
    <div class="info-row"><span class="info-lbl">Статус</span><span class="info-val">${badgeStatus(o.status)}</span></div>
    <div class="info-row"><span class="info-lbl">Создан</span><span class="info-val">${fmtDate(o.created_at)}</span></div>
    ${o.completed_at?`<div class="info-row"><span class="info-lbl">Завершён</span><span class="info-val">${fmtDate(o.completed_at)}</span></div>`:''}
    ${o.cancel_reason?`<div class="info-row"><span class="info-lbl">Причина</span><span class="info-val">${o.cancel_reason}</span></div>`:''}
  `;
  document.getElementById('modal-order-actions').innerHTML=o.status==='pending'?`
    <button class="btn btn-success" onclick="completeOrder(${o.id});closeModal('modal-order')">✅ Завершить</button>
    <button class="btn btn-danger" onclick="cancelOrder(${o.id});closeModal('modal-order')">❌ Отменить</button>
  `:'';
  document.getElementById('modal-order').classList.add('open');
}

async function startShiftFor(car){
  try{
    const res=await fetch('/api/shift/start',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({car_number:car})});
    const data=await res.json();
    showNotif(data.ok?`🟢 Смена начата: ${car}`:'❌ '+data.message,data.ok?'success':'error');
    closeModal('modal-driver');loadShifts();
  }catch(e){}
}

document.querySelectorAll('.modal-bg').forEach(bg=>{
  bg.addEventListener('click',function(e){if(e.target===this)this.classList.remove('open');});
});

setInterval(()=>{
  if(currentTab==='dash') loadDash();
  if(currentTab==='chat'&&currentChatCar) loadChatMessages(currentChatCar);
},10000);

loadDash();
loadDriversSelect();
</script>
</body>
</html>"""

# ==================== ГЛАВНАЯ ====================
@app.route("/")
@login_required
def index():
    return render_template_string(ADMIN_HTML)

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    logger.info(f"🚕 TAXI 3042 запущен на порту {port}")
    app.run(host="0.0.0.0",port=port,debug=False)
