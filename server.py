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

ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "taxi3042")
DB_PATH = "taxi.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS drivers (
            car_number TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL,
            status TEXT DEFAULT 'offline', balance INTEGER DEFAULT 50000,
            pin TEXT NOT NULL, last_seen REAL DEFAULT 0, created_at REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, car_number TEXT NOT NULL,
            from_address TEXT NOT NULL, to_address TEXT NOT NULL,
            distance TEXT DEFAULT '—', price INTEGER NOT NULL,
            client TEXT DEFAULT 'Диспетчер', status TEXT DEFAULT 'pending',
            created_at REAL, completed_at REAL, cancelled_at REAL, cancel_reason TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, car_number TEXT NOT NULL,
            stars INTEGER NOT NULL, created_at REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, car_number TEXT NOT NULL,
            amount INTEGER NOT NULL, type TEXT NOT NULL, comment TEXT, created_at REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, car_number TEXT NOT NULL,
            start_time REAL NOT NULL, end_time REAL,
            revenue INTEGER DEFAULT 0, orders_count INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
            car_number TEXT, details TEXT, created_at REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS tariffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, zone TEXT UNIQUE NOT NULL,
            name TEXT, base_fare INTEGER DEFAULT 5000,
            rate_per_km INTEGER DEFAULT 2800, wait_rate INTEGER DEFAULT 500)''')
        c.execute('''CREATE TABLE IF NOT EXISTS pending_pins (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, car_number TEXT NOT NULL,
            phone TEXT NOT NULL, pin TEXT NOT NULL,
            status TEXT DEFAULT 'pending', created_at REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS balance_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, car_number TEXT NOT NULL,
            amount INTEGER NOT NULL, status TEXT DEFAULT 'pending', created_at REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, car_number TEXT NOT NULL,
            text TEXT NOT NULL, sender TEXT DEFAULT 'admin', created_at REAL)''')
        default = [
            ("city_day","🏙️ Город день",5000,2800,500),
            ("city_night","🌙 Город ночь",7000,3500,700),
            ("suburb_day","🌳 Загород день",5000,3000,500),
            ("suburb_night","🌙 Загород ночь",7000,3800,700),
            ("airport","✈️ Аэропорт",10000,3500,500),
            ("vokzal","🚉 Вокзал",8000,3000,500)
        ]
        for zone,name,base,km,wait in default:
            c.execute('INSERT OR IGNORE INTO tariffs (zone,name,base_fare,rate_per_km,wait_rate) VALUES (?,?,?,?,?)',
                     (zone,name,base,km,wait))
        conn.commit()
    print("✅ БД готова")

init_db()

def log_action(action, car_number=None, details=None):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO logs (action,car_number,details,created_at) VALUES (?,?,?,?)",
                        (action,car_number,details,time.time()))
    except:
        pass

def generate_pin():
    return f"{random.randint(1000,9999):04d}"

def format_time(ts):
    if not ts: return "—"
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")

def background_worker():
    while True:
        try:
            now = time.time()
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("UPDATE drivers SET status='offline' WHERE status IN ('free','busy') AND last_seen < ?", (now-120,))
                conn.execute("UPDATE orders SET status='cancelled',cancelled_at=?,cancel_reason='Таймаут' WHERE status IN ('pending','accepted') AND created_at < ?", (now,now-3600))
                conn.commit()
        except:
            pass
        time.sleep(30)

threading.Thread(target=background_worker, daemon=True).start()

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

LOGIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>TAXI 3042</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{background:#0a0a0a;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}.box{background:#111;border:1px solid #222;border-radius:16px;padding:40px;width:360px;text-align:center}.logo{font-size:28px;font-weight:900;color:#f5c518;margin-bottom:8px}.sub{color:#666;font-size:13px;margin-bottom:32px}input{width:100%;background:#1a1a1a;border:1px solid #333;border-radius:10px;padding:14px;color:#fff;margin-bottom:12px;font-size:14px}button{width:100%;background:#f5c518;color:#000;border:none;border-radius:10px;padding:14px;font-weight:700;cursor:pointer;font-size:15px}.error{background:#ff000020;border:1px solid #ff000050;border-radius:8px;padding:10px;margin-bottom:16px;color:#ff6b6b}</style>
</head><body><div class="box">
<div class="logo">🚕 TAXI 3042</div>
<div class="sub">XAZARASP — Диспетчерская</div>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="POST">
<input type="text" name="login" placeholder="Логин" required>
<input type="password" name="password" placeholder="Пароль" required>
<button type="submit">Войти</button>
</form></div></body></html>"""

@app.route("/login", methods=["GET","POST"])
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

# ===== API ВОДИТЕЛЕЙ =====
@app.route("/api/driver/register", methods=["POST"])
def driver_register():
    data = request.json or {}
    name = data.get("name","").strip()
    car  = data.get("car_number","").strip().upper()
    phone= data.get("phone","").strip()
    if not (name and car and phone):
        return jsonify({"success":False,"error":"Заполните все поля"})
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT car_number FROM drivers WHERE car_number=?",(car,)).fetchone():
            return jsonify({"success":False,"error":"Водитель уже зарегистрирован"})
        if conn.execute("SELECT id FROM pending_pins WHERE car_number=? AND status='pending'",(car,)).fetchone():
            return jsonify({"success":False,"error":"Заявка уже отправлена"})
        pin = generate_pin()
        req_id = f"REQ_{car}_{int(time.time())}"
        conn.execute("INSERT INTO pending_pins (id,name,car_number,phone,pin,status,created_at) VALUES (?,?,?,?,?,?,?)",
                    (req_id,name,car,phone,pin,"pending",time.time()))
        conn.commit()
    log_action("register",car,f"Заявка от {name}")
    return jsonify({"success":True,"message":"Заявка отправлена"})

@app.route("/api/driver/login", methods=["POST"])
def driver_login_api():
    data = request.json or {}
    car  = data.get("car_number","").strip().upper()
    pin  = data.get("pin","").strip()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        driver = conn.execute("SELECT * FROM drivers WHERE car_number=? AND pin=?",(car,pin)).fetchone()
    if not driver:
        return jsonify({"success":False,"error":"Неверные данные"})
    return jsonify({"success":True,"name":driver["name"],"car_number":driver["car_number"],
                   "phone":driver["phone"],"balance":driver["balance"],"status":driver["status"]})

@app.route("/api/driver/heartbeat", methods=["POST"])
def driver_heartbeat():
    data = request.json or {}
    car = data.get("car_number","").strip().upper()
    if not car: return jsonify({"success":False})
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE drivers SET last_seen=? WHERE car_number=?",(time.time(),car))
        conn.commit()
    return jsonify({"success":True})

@app.route("/api/driver/status", methods=["POST"])
def driver_status():
    data   = request.json or {}
    car    = data.get("car_number","").strip().upper()
    status = data.get("status")
    if status not in ("free","offline"):
        return jsonify({"success":False,"error":"Неверный статус"})
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if status == "free":
            conn.execute("UPDATE drivers SET status='free',last_seen=? WHERE car_number=?",(time.time(),car))
            if not conn.execute("SELECT id FROM shifts WHERE car_number=? AND end_time IS NULL",(car,)).fetchone():
                conn.execute("INSERT INTO shifts (car_number,start_time) VALUES (?,?)",(car,time.time()))
        else:
            conn.execute("UPDATE drivers SET status='offline' WHERE car_number=?",(car,))
            conn.execute("UPDATE shifts SET end_time=? WHERE car_number=? AND end_time IS NULL",(time.time(),car))
        conn.commit()
    log_action(f"status_{status}",car)
    return jsonify({"success":True})

@app.route("/api/driver/order", methods=["GET"])
def driver_get_order():
    car = request.args.get("car_number","").strip().upper()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        order = conn.execute("SELECT * FROM orders WHERE car_number=? AND status IN ('pending','accepted') ORDER BY created_at DESC LIMIT 1",(car,)).fetchone()
    if order:
        return jsonify({"success":True,"order":dict(order)})
    return jsonify({"success":True,"order":None})

@app.route("/api/driver/order/accept", methods=["POST"])
def driver_accept():
    data     = request.json or {}
    car      = data.get("car_number","").strip().upper()
    order_id = data.get("order_id")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE orders SET status='accepted' WHERE id=? AND car_number=?",(order_id,car))
        conn.execute("UPDATE drivers SET status='busy' WHERE car_number=?",(car,))
        conn.commit()
    return jsonify({"success":True})

@app.route("/api/driver/order/complete", methods=["POST"])
def driver_complete():
    data     = request.json or {}
    car      = data.get("car_number","").strip().upper()
    order_id = data.get("order_id")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        order = conn.execute("SELECT price FROM orders WHERE id=? AND car_number=?",(order_id,car)).fetchone()
        if not order: return jsonify({"success":False,"error":"Заказ не найден"})
        price = order["price"]
        conn.execute("UPDATE orders SET status='completed',completed_at=? WHERE id=?",(time.time(),order_id))
        conn.execute("UPDATE drivers SET status='free',balance=balance+? WHERE car_number=?",(price,car))
        conn.execute("UPDATE shifts SET revenue=revenue+?,orders_count=orders_count+1 WHERE car_number=? AND end_time IS NULL",(price,car))
        conn.execute("INSERT INTO transactions (car_number,amount,type,comment,created_at) VALUES (?,?,?,?,?)",
                    (car,price,"income",f"Заказ #{order_id}",time.time()))
        conn.commit()
    log_action("order_completed",car,f"Заказ #{order_id} +{price}")
    return jsonify({"success":True})

@app.route("/api/driver/order/cancel", methods=["POST"])
def driver_cancel():
    data     = request.json or {}
    car      = data.get("car_number","").strip().upper()
    order_id = data.get("order_id")
    reason   = data.get("reason","Отменено водителем")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE orders SET status='cancelled',cancelled_at=?,cancel_reason=? WHERE id=? AND car_number=?",
                    (time.time(),reason,order_id,car))
        conn.execute("UPDATE drivers SET status='free' WHERE car_number=?",(car,))
        conn.commit()
    log_action("order_cancelled",car,f"Заказ #{order_id}")
    return jsonify({"success":True})

@app.route("/api/driver/balance", methods=["GET"])
def driver_balance():
    car = request.args.get("car_number","").strip().upper()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT balance FROM drivers WHERE car_number=?",(car,)).fetchone()
    if row: return jsonify({"success":True,"balance":row["balance"]})
    return jsonify({"success":False})

@app.route("/api/driver/balance/request", methods=["POST"])
def driver_balance_request():
    data   = request.json or {}
    car    = data.get("car_number","").strip().upper()
    amount = int(data.get("amount",0))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO balance_requests (car_number,amount,status,created_at) VALUES (?,?,?,?)",
                    (car,amount,"pending",time.time()))
        conn.commit()
    log_action("balance_request",car,f"Сумма {amount}")
    return jsonify({"success":True})

@app.route("/api/driver/chat", methods=["GET"])
def driver_chat_get():
    car = request.args.get("car_number","").strip().upper()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        msgs = conn.execute("SELECT * FROM chat_messages WHERE car_number=? ORDER BY created_at ASC LIMIT 50",(car,)).fetchall()
    return jsonify({"success":True,"messages":[{"text":m["text"],"sender":m["sender"],"created_at":format_time(m["created_at"])} for m in msgs]})

@app.route("/api/driver/chat", methods=["POST"])
def driver_chat_send():
    data = request.json or {}
    car  = data.get("car_number","").strip().upper()
    text = data.get("text","").strip()
    if not text: return jsonify({"success":False})
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO chat_messages (car_number,text,sender,created_at) VALUES (?,?,?,?)",
                    (car,text,"driver",time.time()))
        conn.commit()
    return jsonify({"success":True})

# ===== API АДМИНА =====
@app.route("/api/admin/dashboard")
@admin_required
def admin_dashboard():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            total    = conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
            online   = conn.execute("SELECT COUNT(*) FROM drivers WHERE status IN ('free','busy')").fetchone()[0]
            free     = conn.execute("SELECT COUNT(*) FROM drivers WHERE status='free'").fetchone()[0]
            busy     = conn.execute("SELECT COUNT(*) FROM drivers WHERE status='busy'").fetchone()[0]
            pend_cnt = conn.execute("SELECT COUNT(*) FROM pending_pins WHERE status='pending'").fetchone()[0]
            act_ord  = conn.execute("SELECT COUNT(*) FROM orders WHERE status IN ('pending','accepted')").fetchone()[0]
            today    = datetime.now().replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
            today_rev= conn.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='completed' AND completed_at>?",(today,)).fetchone()[0]
            today_cnt= conn.execute("SELECT COUNT(*) FROM orders WHERE status='completed' AND completed_at>?",(today,)).fetchone()[0]
            online_drivers = conn.execute("""
                SELECT d.car_number,d.name,d.status,d.balance,
                       COALESCE(ROUND(AVG(r.stars),1),0) as rating,
                       COUNT(DISTINCT o.id) as orders_count
                FROM drivers d
                LEFT JOIN ratings r ON r.car_number=d.car_number
                LEFT JOIN orders o ON o.car_number=d.car_number AND o.status='completed'
                WHERE d.status IN ('free','busy')
                GROUP BY d.car_number
            """).fetchall()
            active_orders = conn.execute(
                "SELECT * FROM orders WHERE status IN ('pending','accepted') ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        return jsonify({
            "success":True,
            "stats":{
                "total":total,"online":online,"free":free,"busy":busy,
                "pending_pins":pend_cnt,"active_orders":act_ord,
                "today_revenue":today_rev,"today_orders":today_cnt
            },
            "online_drivers":[dict(d) for d in online_drivers],
            "active_orders":[dict(o) for o in active_orders]
        })
    except Exception as e:
        print(f"Dashboard error: {e}")
        return jsonify({"success":False,"error":str(e)}), 500

@app.route("/api/admin/pending_pins")
@admin_required
def admin_pending_pins():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            pending = conn.execute("SELECT * FROM pending_pins WHERE status='pending' ORDER BY created_at DESC").fetchall()
        return jsonify({"success":True,"pending":[dict(p) for p in pending]})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}), 500

@app.route("/api/admin/drivers")
@admin_required
def admin_drivers():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            drivers = conn.execute("""
                SELECT d.*,
                       COALESCE(ROUND(AVG(r.stars),1),0) as rating,
                       COUNT(DISTINCT o.id) as orders_count
                FROM drivers d
                LEFT JOIN ratings r ON r.car_number=d.car_number
                LEFT JOIN orders o ON o.car_number=d.car_number AND o.status='completed'
                GROUP BY d.car_number ORDER BY d.created_at DESC
            """).fetchall()
            pending = conn.execute("SELECT * FROM pending_pins WHERE status='pending' ORDER BY created_at DESC").fetchall()
        return jsonify({"success":True,"drivers":[dict(d) for d in drivers],"pending":[dict(p) for p in pending]})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}), 500

@app.route("/api/admin/approve/<req_id>", methods=["POST"])
@admin_required
def admin_approve(req_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            req = conn.execute("SELECT * FROM pending_pins WHERE id=? AND status='pending'",(req_id,)).fetchone()
            if not req: return jsonify({"success":False,"error":"Заявка не найдена"})
            conn.execute("INSERT OR IGNORE INTO drivers (car_number,name,phone,status,balance,pin,last_seen,created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (req["car_number"],req["name"],req["phone"],"offline",50000,req["pin"],0,time.time()))
            conn.execute("UPDATE pending_pins SET status='approved' WHERE id=?",(req_id,))
            conn.commit()
            pin = req["pin"]
        log_action("driver_approved",req["car_number"])
        return jsonify({"success":True,"pin":pin})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}), 500

@app.route("/api/admin/reject/<req_id>", methods=["POST"])
@admin_required
def admin_reject(req_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE pending_pins SET status='rejected' WHERE id=?",(req_id,))
        conn.commit()
    return jsonify({"success":True})

@app.route("/api/admin/order", methods=["POST"])
@admin_required
def admin_create_order():
    data     = request.json or {}
    car      = data.get("car_number","").strip().upper()
    from_a   = data.get("from_address","").strip()
    to_a     = data.get("to_address","").strip()
    price    = int(data.get("price",0))
    client   = data.get("client","Диспетчер")
    distance = data.get("distance","—")
    if not (car and from_a and to_a and price):
        return jsonify({"success":False,"error":"Не все поля заполнены"})
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO orders (car_number,from_address,to_address,distance,price,client,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (car,from_a,to_a,distance,price,client,"pending",time.time()))
        conn.execute("UPDATE drivers SET status='busy' WHERE car_number=?",(car,))
        conn.commit()
    log_action("order_created",car,f"{from_a}→{to_a} {price}")
    return jsonify({"success":True})

@app.route("/api/admin/orders")
@admin_required
def admin_orders():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        orders = conn.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 200").fetchall()
    return jsonify({"success":True,"orders":[dict(o) for o in orders]})

@app.route("/api/admin/shifts")
@admin_required
def admin_shifts():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        shifts = conn.execute("SELECT s.*,d.name FROM shifts s LEFT JOIN drivers d ON d.car_number=s.car_number ORDER BY s.start_time DESC LIMIT 200").fetchall()
    return jsonify({"success":True,"shifts":[dict(s) for s in shifts]})

@app.route("/api/admin/ratings")
@admin_required
def admin_ratings():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        ratings = conn.execute("""
            SELECT d.car_number,d.name,
                   COALESCE(ROUND(AVG(r.stars),1),0) as avg_rating,
                   COUNT(r.id) as count
            FROM drivers d LEFT JOIN ratings r ON r.car_number=d.car_number
            GROUP BY d.car_number ORDER BY avg_rating DESC
        """).fetchall()
    return jsonify({"success":True,"ratings":[dict(r) for r in ratings]})

@app.route("/api/admin/finances")
@admin_required
def admin_finances():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        bal_req = conn.execute("SELECT * FROM balance_requests WHERE status='pending' ORDER BY created_at DESC").fetchall()
        txns    = conn.execute("SELECT * FROM transactions ORDER BY created_at DESC LIMIT 100").fetchall()
    return jsonify({"success":True,"balance_requests":[dict(b) for b in bal_req],"transactions":[dict(t) for t in txns]})

@app.route("/api/admin/balance/approve/<int:req_id>", methods=["POST"])
@admin_required
def admin_balance_approve(req_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        req = conn.execute("SELECT * FROM balance_requests WHERE id=? AND status='pending'",(req_id,)).fetchone()
        if not req: return jsonify({"success":False})
        conn.execute("UPDATE drivers SET balance=balance+? WHERE car_number=?",(req["amount"],req["car_number"]))
        conn.execute("UPDATE balance_requests SET status='approved' WHERE id=?",(req_id,))
        conn.execute("INSERT INTO transactions (car_number,amount,type,comment,created_at) VALUES (?,?,?,?,?)",
                    (req["car_number"],req["amount"],"topup","Пополнение одобрено",time.time()))
        conn.commit()
    log_action("balance_approve",req["car_number"],f"+{req['amount']}")
    return jsonify({"success":True})

@app.route("/api/admin/chat/<car_number>", methods=["GET"])
@admin_required
def admin_chat_get(car_number):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        msgs = conn.execute("SELECT * FROM chat_messages WHERE car_number=? ORDER BY created_at ASC",(car_number,)).fetchall()
    return jsonify({"success":True,"messages":[{"text":m["text"],"sender":m["sender"],"created_at":format_time(m["created_at"])} for m in msgs]})

@app.route("/api/admin/chat/<car_number>", methods=["POST"])
@admin_required
def admin_chat_send(car_number):
    text = (request.json or {}).get("text","").strip()
    if not text: return jsonify({"success":False})
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO chat_messages (car_number,text,sender,created_at) VALUES (?,?,?,?)",
                    (car_number,text,"admin",time.time()))
        conn.commit()
    return jsonify({"success":True})

@app.route("/api/admin/tariffs")
@admin_required
def admin_tariffs():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        tariffs = conn.execute("SELECT * FROM tariffs").fetchall()
    return jsonify({"success":True,"tariffs":[dict(t) for t in tariffs]})

@app.route("/api/admin/tariffs/<int:tariff_id>", methods=["PUT"])
@admin_required
def admin_tariff_update(tariff_id):
    data = request.json or {}
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE tariffs SET base_fare=?,rate_per_km=?,wait_rate=? WHERE id=?",
                    (data.get("base_fare"),data.get("rate_per_km"),data.get("wait_rate"),tariff_id))
        conn.commit()
    return jsonify({"success":True})

@app.route("/api/admin/driver/<car_number>", methods=["DELETE"])
@admin_required
def admin_delete_driver(car_number):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM drivers WHERE car_number=?",(car_number,))
        conn.commit()
    log_action("driver_deleted",car_number)
    return jsonify({"success":True})

# ===== ГЛАВНАЯ СТРАНИЦА =====
ADMIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAXI 3042</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#fff;font-family:'Segoe UI',sans-serif;min-height:100vh}
.nav{background:#111;border-bottom:2px solid #1e1e1e;padding:0 16px;display:flex;align-items:center;position:sticky;top:0;z-index:100;height:54px}
.nav-logo{font-size:15px;font-weight:900;color:#f5c518;padding-right:20px;border-right:1px solid #222;margin-right:16px;white-space:nowrap}
.nav-tabs{display:flex;flex:1;overflow-x:auto;height:54px}
.nav-tabs::-webkit-scrollbar{height:2px}
.tab{padding:0 16px;cursor:pointer;color:#777;font-size:13px;font-weight:600;border-bottom:3px solid transparent;white-space:nowrap;display:flex;align-items:center;gap:5px;height:100%}
.tab:hover{color:#ccc}
.tab.active{color:#f5c518;border-bottom-color:#f5c518}
.nav-right{display:flex;align-items:center;gap:12px;margin-left:auto;padding-left:16px;flex-shrink:0}
.live{color:#4caf50;font-size:12px;display:flex;align-items:center;gap:4px}
.dot{width:7px;height:7px;background:#4caf50;border-radius:50%;animation:p 1.2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.clock{color:#666;font-family:monospace;font-size:12px}
.logout{background:transparent;border:1px solid #333;color:#777;padding:5px 12px;border-radius:7px;text-decoration:none;font-size:12px}
.logout:hover{color:#fff;border-color:#555}
.cnt{padding:20px;max-width:1400px;margin:0 auto}
.page{display:none}.page.active{display:block}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px}
.card{background:#111;border:1px solid #1e1e1e;border-radius:12px;padding:18px 12px;text-align:center;transition:.2s}
.card:hover{border-color:#333}
.cv{font-size:26px;font-weight:900;line-height:1}
.cl{font-size:11px;color:#555;margin-top:6px;font-weight:500}
.g{color:#4caf50}.y{color:#f5c518}.b{color:#2196f3}.p{color:#9c27b0}.r{color:#f44336}
.sec{background:#111;border:1px solid #1e1e1e;border-radius:12px;padding:20px;margin-bottom:16px}
.st{font-size:14px;font-weight:700;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:#555;text-align:left;padding:10px 12px;border-bottom:1px solid #1a1a1a;font-weight:600;white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid #161616;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#141414}
.badge{padding:3px 9px;border-radius:20px;font-size:11px;font-weight:700;display:inline-block;white-space:nowrap}
.bf{background:#4caf5018;color:#4caf50}
.bb{background:#ff980018;color:#ff9800}
.bo{background:#55555518;color:#888}
.bp{background:#2196f318;color:#2196f3}
.ba{background:#ff980018;color:#ff9800}
.bc{background:#4caf5018;color:#4caf50}
.bx{background:#f4433618;color:#f44336}
.btn{padding:7px 14px;border-radius:7px;border:none;cursor:pointer;font-size:12px;font-weight:600;transition:.15s;white-space:nowrap}
.btn-p{background:#f5c518;color:#000}.btn-p:hover{background:#e6b800}
.btn-s{background:#4caf50;color:#fff}.btn-s:hover{background:#43a047}
.btn-d{background:#f44336;color:#fff}.btn-d:hover{background:#e53935}
.btn-g{background:#1e1e1e;color:#999;border:1px solid #2a2a2a}.btn-g:hover{color:#fff}
.sm{padding:4px 10px;font-size:11px}
.fg{display:flex;flex-direction:column;gap:5px}
.fg label{font-size:11px;color:#777;font-weight:600}
.fg input,.fg select{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:7px;padding:9px 11px;color:#fff;font-size:13px;width:100%}
.fg input:focus,.fg select:focus{outline:none;border-color:#f5c518}
.fr{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:14px}
.pc{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:16px;margin-bottom:10px;display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:space-between}
.pc:hover{border-color:#f5c51840}
.pin{background:#f5c51812;border:1px solid #f5c51850;border-radius:8px;padding:8px 18px;font-size:22px;font-family:monospace;color:#f5c518;letter-spacing:4px;font-weight:700}
.cm{height:340px;overflow-y:auto;background:#0a0a0a;border:1px solid #1a1a1a;border-radius:8px;padding:12px;margin-bottom:10px;display:flex;flex-direction:column;gap:6px}
.msg{max-width:72%;padding:8px 12px;border-radius:10px;font-size:13px;line-height:1.45;word-break:break-word}
.ma{background:#f5c51812;border:1px solid #f5c51830;align-self:flex-end}
.md{background:#1e1e1e;border:1px solid #252525;align-self:flex-start}
.mt{font-size:10px;color:#444;margin-top:3px}
.ci{flex:1;background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:10px 13px;color:#fff;font-size:13px}
.ci:focus{outline:none;border-color:#f5c518}
.cir{display:flex;gap:8px}
.di{background:#1a1a1a;border:1px solid #1e1e1e;border-radius:8px;padding:10px 14px;cursor:pointer;display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;transition:.15s}
.di:hover{border-color:#f5c51840}
.di.act{border-color:#f5c518;background:#f5c51808}
.modal{display:none;position:fixed;inset:0;background:#000c;z-index:1000;align-items:center;justify-content:center}
.modal.open{display:flex}
.mbox{background:#111;border:1px solid #2a2a2a;border-radius:16px;padding:26px;width:520px;max-width:94vw;max-height:90vh;overflow-y:auto}
.mt2{font-size:17px;font-weight:700;margin-bottom:18px;color:#f5c518}
.ma2{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
.nd{text-align:center;padding:40px 20px;color:#444;font-size:13px}
.qb{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}
.nb{background:#f44336;color:#fff;border-radius:50%;width:17px;height:17px;font-size:10px;display:inline-flex;align-items:center;justify-content:center;margin-left:4px;font-weight:700}
</style>
</head>
<body>
<div class="nav">
  <div class="nav-logo">🚕 TAXI 3042 <span style="color:#444;font-size:9px">XAZARASP</span></div>
  <div class="nav-tabs">
    <div class="tab active" data-page="dashboard">📊 Дашборд</div>
    <div class="tab" data-page="orders">📋 Заказы</div>
    <div class="tab" data-page="drivers">🚗 Водители</div>
    <div class="tab" data-page="pins">🔑 Заявки<span id="pb" class="nb" style="display:none">0</span></div>
    <div class="tab" data-page="shifts">⏱ Смены</div>
    <div class="tab" data-page="ratings">⭐ Рейтинг</div>
    <div class="tab" data-page="chat">💬 Чат</div>
    <div class="tab" data-page="finances">💰 Финансы</div>
    <div class="tab" data-page="settings">⚙️ Тарифы</div>
  </div>
  <div class="nav-right">
    <span class="clock" id="clk"></span>
    <span class="live"><span class="dot"></span>Live</span>
    <a href="/logout" class="logout">Выход</a>
  </div>
</div>

<div class="cnt">
  <div class="page active" id="page-dashboard"><div class="nd">⏳ Загрузка...</div></div>
  <div class="page" id="page-orders"><div class="nd">⏳ Загрузка...</div></div>
  <div class="page" id="page-drivers"><div class="nd">⏳ Загрузка...</div></div>
  <div class="page" id="page-pins"><div class="nd">⏳ Загрузка...</div></div>
  <div class="page" id="page-shifts"><div class="nd">⏳ Загрузка...</div></div>
  <div class="page" id="page-ratings"><div class="nd">⏳ Загрузка...</div></div>
  <div class="page" id="page-chat"><div class="nd">⏳ Загрузка...</div></div>
  <div class="page" id="page-finances"><div class="nd">⏳ Загрузка...</div></div>
  <div class="page" id="page-settings"><div class="nd">⏳ Загрузка...</div></div>
</div>

<div class="modal" id="mod-order">
  <div class="mbox">
    <div class="mt2">⚡ Новый заказ</div>
    <div class="fr">
      <div class="fg"><label>Номер авто *</label><input id="oc" placeholder="01A123BC"></div>
      <div class="fg"><label>Клиент</label><input id="ocl" value="Диспетчер"></div>
    </div>
    <div class="fr">
      <div class="fg"><label>Откуда *</label><input id="of" placeholder="Адрес отправления"></div>
      <div class="fg"><label>Куда *</label><input id="ot" placeholder="Адрес назначения"></div>
    </div>
    <div class="fr">
      <div class="fg"><label>Расстояние</label><input id="od" placeholder="напр: 5 км"></div>
      <div class="fg"><label>Цена (сум) *</label><input id="op" type="number" placeholder="15000"></div>
    </div>
    <div class="ma2">
      <button class="btn btn-g" onclick="closeM('mod-order')">Отмена</button>
      <button class="btn btn-p" onclick="createOrder()">✅ Создать заказ</button>
    </div>
  </div>
</div>

<script>
var curPage='dashboard', curCar=null;

function esc(s){if(!s)return'';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function fmtM(n){if(!n&&n!==0)return'0 сум';return Number(n).toLocaleString('ru')+' сум';}
function fmtT(ts){if(!ts)return'—';return new Date(ts*1000).toLocaleString('ru');}
function stLbl(s){return{free:'Свободен',busy:'На заказе',offline:'Офлайн'}[s]||s;}
function ordLbl(s){return{pending:'Ожидает',accepted:'Принят',completed:'Завершён',cancelled:'Отменён'}[s]||s;}
function stBadge(s){return{free:'bf',busy:'bb',offline:'bo'}[s]||'bo';}
function ordBadge(s){return{pending:'bp',accepted:'ba',completed:'bc',cancelled:'bx'}[s]||'bo';}
function openM(id){document.getElementById(id).classList.add('open');}
function closeM(id){document.getElementById(id).classList.remove('open');}

// Закрытие по клику вне
document.querySelectorAll('.modal').forEach(function(m){
  m.addEventListener('click',function(e){if(e.target===m)m.classList.remove('open');});
});

// Часы
setInterval(function(){document.getElementById('clk').textContent=new Date().toLocaleTimeString('ru');},1000);
document.getElementById('clk').textContent=new Date().toLocaleTimeString('ru');

// Навигация
function showPage(pg){
  document.querySelectorAll('.page').forEach(function(p){p.classList.remove('active');});
  document.getElementById('page-'+pg).classList.add('active');
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active');});
  document.querySelector('.tab[data-page="'+pg+'"]').classList.add('active');
  curPage=pg;
  var fn={dashboard:loadDash,orders:loadOrders,drivers:loadDrivers,
          pins:loadPins,shifts:loadShifts,ratings:loadRatings,
          chat:loadChat,finances:loadFinances,settings:loadSettings};
  if(fn[pg]) fn[pg]();
}
document.querySelectorAll('.tab').forEach(function(t){
  t.addEventListener('click',function(){showPage(t.dataset.page);});
});

// Авто-обновление
setInterval(function(){
  if(curPage==='dashboard') loadDash();
  checkBadge();
},30000);

// Бейдж заявок
async function checkBadge(){
  try{
    var r=await fetch('/api/admin/pending_pins');
    var d=await r.json();
    var b=document.getElementById('pb');
    if(d.success&&d.pending&&d.pending.length>0){b.textContent=d.pending.length;b.style.display='inline-flex';}
    else b.style.display='none';
  }catch(e){}
}

// ======= ДАШБОРД =======
async function loadDash(){
  var el=document.getElementById('page-dashboard');
  try{
    var r=await fetch('/api/admin/dashboard');
    if(!r.ok) throw new Error('HTTP '+r.status);
    var d=await r.json();
    if(!d.success) throw new Error(d.error||'Ошибка API');
    var s=d.stats;
    checkBadge();
    el.innerHTML=
      '<div class="cards">'+
        '<div class="card"><div class="cv g">'+s.online+'</div><div class="cl">На линии</div></div>'+
        '<div class="card"><div class="cv g">'+s.free+'</div><div class="cl">Свободны</div></div>'+
        '<div class="card"><div class="cv y">'+s.busy+'</div><div class="cl">На заказе</div></div>'+
        '<div class="card"><div class="cv b">'+s.pending_pins+'</div><div class="cl">Заявок</div></div>'+
        '<div class="card"><div class="cv p">'+s.total+'</div><div class="cl">Водителей</div></div>'+
        '<div class="card"><div class="cv y">'+s.active_orders+'</div><div class="cl">Акт.заказов</div></div>'+
        '<div class="card"><div class="cv g" style="font-size:16px">'+fmtM(s.today_revenue)+'</div><div class="cl">Выручка сегодня</div></div>'+
        '<div class="card"><div class="cv b">'+s.today_orders+'</div><div class="cl">Заказов сегодня</div></div>'+
      '</div>'+
      '<div class="qb">'+
        '<button class="btn btn-p" onclick="openM(\'mod-order\')">⚡ Новый заказ</button>'+
        '<button class="btn btn-g" onclick="showPage(\'pins\')">🔑 Заявки ('+s.pending_pins+')</button>'+
        '<button class="btn btn-g" onclick="showPage(\'chat\')">💬 Чат</button>'+
        '<button class="btn btn-g" onclick="loadDash()">🔄 Обновить</button>'+
      '</div>'+
      '<div class="sec"><div class="st">🚗 Водители онлайн ('+d.online_drivers.length+')</div>'+
      (d.online_drivers.length===0?'<div class="nd">Нет водителей онлайн</div>':
        '<div class="tw"><table><thead><tr><th>Авто</th><th>Имя</th><th>Статус</th><th>Баланс</th><th>Рейтинг</th><th>Заказов</th><th>Действия</th></tr></thead><tbody>'+
        d.online_drivers.map(function(dr){return(
          '<tr><td><b>'+esc(dr.car_number)+'</b></td>'+
          '<td>'+esc(dr.name)+'</td>'+
          '<td><span class="badge '+stBadge(dr.status)+'">'+stLbl(dr.status)+'</span></td>'+
          '<td>'+fmtM(dr.balance)+'</td>'+
          '<td>⭐ '+dr.rating+'</td>'+
          '<td>'+dr.orders_count+'</td>'+
          '<td style="display:flex;gap:4px">'+
            '<button class="btn btn-p sm" onclick="qOrder(\''+esc(dr.car_number)+'\')">📋 Заказ</button>'+
            '<button class="btn btn-g sm" onclick="gotoChat(\''+esc(dr.car_number)+'\')">💬</button>'+
          '</td></tr>'
        );}).join('')+
        '</tbody></table></div>')+
      '</div>'+
      '<div class="sec"><div class="st">📋 Активные заказы</div>'+
      (d.active_orders.length===0?'<div class="nd">Нет активных заказов</div>':
        '<div class="tw"><table><thead><tr><th>#</th><th>Водитель</th><th>Откуда</th><th>Куда</th><th>Цена</th><th>Клиент</th><th>Статус</th><th>Время</th></tr></thead><tbody>'+
        d.active_orders.map(function(o){return(
          '<tr><td>'+o.id+'</td>'+
          '<td><b>'+esc(o.car_number)+'</b></td>'+
          '<td>'+esc(o.from_address)+'</td>'+
          '<td>'+esc(o.to_address)+'</td>'+
          '<td>'+fmtM(o.price)+'</td>'+
          '<td>'+esc(o.client)+'</td>'+
          '<td><span class="badge '+ordBadge(o.status)+'">'+ordLbl(o.status)+'</span></td>'+
          '<td>'+fmtT(o.created_at)+'</td></tr>'
        );}).join('')+
        '</tbody></table></div>')+
      '</div>';
  }catch(e){
    el.innerHTML='<div class="nd" style="color:#f44336">❌ Ошибка загрузки: '+esc(e.message)+'<br><br><button class="btn btn-p" onclick="loadDash()">🔄 Повторить</button></div>';
  }
}

function qOrder(car){document.getElementById('oc').value=car;openM('mod-order');}
function gotoChat(car){curCar=car;showPage('chat');}

async function createOrder(){
  var car=document.getElementById('oc').value.trim();
  var from=document.getElementById('of').value.trim();
  var to=document.getElementById('ot').value.trim();
  var price=parseInt(document.getElementById('op').value)||0;
  var client=document.getElementById('ocl').value.trim()||'Диспетчер';
  var dist=document.getElementById('od').value.trim()||'—';
  if(!car||!from||!to||!price){alert('⚠️ Заполните обязательные поля (*)');return;}
  try{
    var r=await fetch('/api/admin/order',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({car_number:car,from_address:from,to_address:to,price:price,client:client,distance:dist})});
    var d=await r.json();
    if(d.success){
      alert('✅ Заказ создан!');
      closeM('mod-order');
      ['oc','of','ot','op','od'].forEach(function(id){var el=document.getElementById(id);if(el)el.value='';});
      document.getElementById('ocl').value='Диспетчер';
      loadDash();
    }else alert('Ошибка: '+(d.error||'Неизвестная ошибка'));
  }catch(e){alert('Ошибка сети: '+e.message);}
}

// ======= ЗАКАЗЫ =======
async function loadOrders(){
  var el=document.getElementById('page-orders');
  try{
    var r=await fetch('/api/admin/orders');
    var d=await r.json();
    if(!d.success) throw new Error('Ошибка API');
    el.innerHTML=
      '<div class="sec"><div class="st">📋 Все заказы ('+d.orders.length+')'+
        '<button class="btn btn-p sm" onclick="openM(\'mod-order\')">+ Новый</button></div>'+
      (d.orders.length===0?'<div class="nd">Заказов нет</div>':
        '<div class="tw"><table><thead><tr><th>#</th><th>Авто</th><th>Откуда</th><th>Куда</th><th>Расст.</th><th>Цена</th><th>Клиент</th><th>Статус</th><th>Время</th></tr></thead><tbody>'+
        d.orders.map(function(o){return(
          '<tr><td>'+o.id+'</td><td><b>'+esc(o.car_number)+'</b></td>'+
          '<td>'+esc(o.from_address)+'</td><td>'+esc(o.to_address)+'</td>'+
          '<td>'+esc(o.distance||'—')+'</td><td>'+fmtM(o.price)+'</td>'+
          '<td>'+esc(o.client)+'</td>'+
          '<td><span class="badge '+ordBadge(o.status)+'">'+ordLbl(o.status)+'</span></td>'+
          '<td>'+fmtT(o.created_at)+'</td></tr>'
        );}).join('')+
        '</tbody></table></div>')+
      '</div>';
  }catch(e){el.innerHTML='<div class="nd" style="color:#f44336">❌ '+esc(e.message)+'</div>';}
}

// ======= ВОДИТЕЛИ =======
async function loadDrivers(){
  var el=document.getElementById('page-drivers');
  try{
    var r=await fetch('/api/admin/drivers');
    var d=await r.json();
    if(!d.success) throw new Error('Ошибка API');
    el.innerHTML=
      '<div class="sec"><div class="st">🚗 Все водители ('+d.drivers.length+')</div>'+
      (d.drivers.length===0?'<div class="nd">Водителей нет</div>':
        '<div class="tw"><table><thead><tr><th>Авто</th><th>Имя</th><th>Телефон</th><th>Статус</th><th>Баланс</th><th>ПИН</th><th>Рейтинг</th><th>Заказов</th><th>Дата</th><th>Действия</th></tr></thead><tbody>'+
        d.drivers.map(function(dr){return(
          '<tr><td><b>'+esc(dr.car_number)+'</b></td>'+
          '<td>'+esc(dr.name)+'</td>'+
          '<td>'+esc(dr.phone)+'</td>'+
          '<td><span class="badge '+stBadge(dr.status)+'">'+stLbl(dr.status)+'</span></td>'+
          '<td>'+fmtM(dr.balance)+'</td>'+
          '<td><code style="background:#f5c51815;padding:2px 8px;border-radius:5px">'+esc(dr.pin)+'</code></td>'+
          '<td>⭐ '+(dr.rating||0)+'</td>'+
          '<td>'+(dr.orders_count||0)+'</td>'+
          '<td>'+fmtT(dr.created_at)+'</td>'+
          '<td style="display:flex;gap:4px">'+
            '<button class="btn btn-g sm" onclick="gotoChat(\''+esc(dr.car_number)+'\')">💬</button>'+
            '<button class="btn btn-d sm" onclick="delDriver(\''+esc(dr.car_number)+'\')">🗑</button>'+
          '</td></tr>'
        );}).join('')+
        '</tbody></table></div>')+
      '</div>';
  }catch(e){el.innerHTML='<div class="nd" style="color:#f44336">❌ '+esc(e.message)+'</div>';}
}

async function delDriver(car){
  if(!confirm('Удали��ь водителя '+car+'?')) return;
  try{
    var r=await fetch('/api/admin/driver/'+car,{method:'DELETE'});
    var d=await r.json();
    if(d.success){alert('✅ Удалён');loadDrivers();}
    else alert('Ошибка');
  }catch(e){alert('Ошибка сети');}
}

// ======= ЗАЯВКИ =======
async function loadPins(){
  var el=document.getElementById('page-pins');
  el.innerHTML='<div class="sec"><div class="st">🔑 Заявки</div><div class="nd">⏳ Загрузка...</div></div>';
  try{
    var r=await fetch('/api/admin/pending_pins');
    if(!r.ok) throw new Error('HTTP '+r.status);
    var d=await r.json();
    if(!d.success) throw new Error(d.error||'Ошибка');
    var p=d.pending||[];
    var b=document.getElementById('pb');
    if(p.length>0){b.textContent=p.length;b.style.display='inline-flex';}
    else b.style.display='none';
    if(p.length===0){
      el.innerHTML='<div class="sec"><div class="st">🔑 Заявки на регистрацию<button class="btn btn-g sm" onclick="loadPins()" style="margin-left:8px">🔄</button></div><div class="nd">✅ Нет новых заявок</div></div>';
      return;
    }
    el.innerHTML='<div class="sec"><div class="st">🔑 Заявки на регистрацию ('+p.length+')<button class="btn btn-g sm" onclick="loadPins()" style="margin-left:8px">🔄</button></div>'+
      p.map(function(x){return(
        '<div class="pc">'+
          '<div>'+
            '<div style="font-size:15px;font-weight:700;margin-bottom:5px">'+esc(x.name)+'</div>'+
            '<div style="color:#888;font-size:13px">🚗 '+esc(x.car_number)+'</div>'+
            '<div style="color:#888;font-size:13px">📞 '+esc(x.phone)+'</div>'+
            '<div style="color:#555;font-size:12px;margin-top:4px">'+fmtT(x.created_at)+'</div>'+
          '</div>'+
          '<div style="text-align:center">'+
            '<div style="color:#666;font-size:11px;margin-bottom:5px">ПИН-КОД</div>'+
            '<div class="pin">'+esc(x.pin)+'</div>'+
          '</div>'+
          '<div style="display:flex;flex-direction:column;gap:8px">'+
            '<button class="btn btn-s" onclick="approvePin(\''+esc(x.id)+'\')">✅ Одобрить</button>'+
            '<button class="btn btn-d" onclick="rejectPin(\''+esc(x.id)+'\')">❌ Отклонить</button>'+
          '</div>'+
        '</div>'
      );}).join('')+
    '</div>';
  }catch(e){
    el.innerHTML='<div class="sec"><div class="st">🔑 Заявки<button class="btn btn-g sm" onclick="loadPins()" style="margin-left:8px">🔄</button></div>'+
      '<div class="nd" style="color:#f44336">❌ Ошибка: '+esc(e.message)+'<br><br><button class="btn btn-p" onclick="loadPins()">Повторить</button></div></div>';
  }
}

async function approvePin(id){
  if(!confirm('Одобрить заявку?')) return;
  try{
    var r=await fetch('/api/admin/approve/'+id,{method:'POST'});
    var d=await r.json();
    if(d.success){alert('✅ Одобрено!\n\n🔑 ПИН: '+d.pin+'\n\nСообщите водителю этот ПИН.');loadPins();checkBadge();}
    else alert('Ошибка: '+(d.error||''));
  }catch(e){alert('Ошибка сети');}
}

async function rejectPin(id){
  if(!confirm('Отклонить заявку?')) return;
  try{
    var r=await fetch('/api/admin/reject/'+id,{method:'POST'});
    var d=await r.json();
    if(d.success){alert('Отклонено');loadPins();}
    else alert('Ошибка');
  }catch(e){alert('Ошибка сети');}
}

// ======= СМЕНЫ =======
async function loadShifts(){
  var el=document.getElementById('page-shifts');
  try{
    var r=await fetch('/api/admin/shifts');
    var d=await r.json();
    if(!d.success) throw new Error('Ошибка API');
    el.innerHTML='<div class="sec"><div class="st">⏱ Смены ('+d.shifts.length+')</div>'+
      (d.shifts.length===0?'<div class="nd">Смен нет</div>':
        '<div class="tw"><table><thead><tr><th>Авто</th><th>Водитель</th><th>Начало</th><th>Конец</th><th>Длит.</th><th>Выручка</th><th>Заказов</th></tr></thead><tbody>'+
        d.shifts.map(function(s){
          var dur='';
          if(s.start_time){
            var end=s.end_time?s.end_time*1000:Date.now();
            var mins=Math.floor((end-s.start_time*1000)/60000);
            var h=Math.floor(mins/60),m=mins%60;
            dur=h>0?h+'ч '+m+'м':m+'м';
          }
          return('<tr><td><b>'+esc(s.car_number)+'</b></td>'+
            '<td>'+(esc(s.name)||'—')+'</td>'+
            '<td>'+fmtT(s.start_time)+'</td>'+
            '<td>'+(s.end_time?fmtT(s.end_time):'<span style="color:#4caf50;font-size:12px">● Активна</span>')+'</td>'+
            '<td>'+(dur||'—')+'</td>'+
            '<td>'+fmtM(s.revenue)+'</td>'+
            '<td>'+s.orders_count+'</td></tr>');
        }).join('')+
        '</tbody></table></div>')+
      '</div>';
  }catch(e){el.innerHTML='<div class="nd" style="color:#f44336">❌ '+esc(e.message)+'</div>';}
}

// ======= РЕЙТИНГ =======
async function loadRatings(){
  var el=document.getElementById('page-ratings');
  try{
    var r=await fetch('/api/admin/ratings');
    var d=await r.json();
    if(!d.success) throw new Error('Ошибка API');
    el.innerHTML='<div class="sec"><div class="st">⭐ Рейтинг водителей</div>'+
      (d.ratings.length===0?'<div class="nd">Нет данных</div>':
        '<div class="tw"><table><thead><tr><th>#</th><th>Авто</th><th>Водитель</th><th>Рейтинг</th><th>Оценок</th></tr></thead><tbody>'+
        d.ratings.map(function(row,i){return(
          '<tr><td>'+(i+1)+'</td><td><b>'+esc(row.car_number)+'</b></td>'+
          '<td>'+esc(row.name)+'</td>'+
          '<td>'+'⭐'.repeat(Math.round(row.avg_rating))+' <span style="color:#666">('+row.avg_rating+')</span></td>'+
          '<td>'+row.count+'</td></tr>'
        );}).join('')+
        '</tbody></table></div>')+
      '</div>';
  }catch(e){el.innerHTML='<div class="nd" style="color:#f44336">❌ '+esc(e.message)+'</div>';}
}

// ======= ЧАТ =======
async function loadChat(){
  var el=document.getElementById('page-chat');
  try{
    var r=await fetch('/api/admin/drivers');
    var d=await r.json();
    if(!d.success) throw new Error('Ошибка API');
    var drs=d.drivers||[];
    el.innerHTML=
      '<div style="display:grid;grid-template-columns:260px 1fr;gap:14px;min-height:500px">'+
        '<div class="sec" style="padding:12px;overflow-y:auto">'+
          '<div style="font-size:11px;color:#555;font-weight:700;padding:4px 6px 10px">ВОДИТЕЛИ</div>'+
          (drs.length===0?'<div class="nd">Нет водителей</div>':
            drs.map(function(dr){return(
              '<div class="di" id="di-'+esc(dr.car_number)+'" onclick="openChat(\''+esc(dr.car_number)+'\')">'+
                '<div><div style="font-size:13px;font-weight:600">'+esc(dr.car_number)+'</div>'+
                '<div style="font-size:11px;color:#666">'+esc(dr.name)+'</div></div>'+
                '<span class="badge '+stBadge(dr.status)+'" style="font-size:10px">'+stLbl(dr.status)+'</span>'+
              '</div>'
            );}).join(''))+
        '</div>'+
        '<div class="sec" id="chat-panel" style="display:flex;flex-direction:column">'+
          '<div style="flex:1;display:flex;align-items:center;justify-content:center;color:#333;flex-direction:column;gap:8px">'+
            '<div style="font-size:32px">💬</div>'+
            '<div>Выберите водителя</div>'+
          '</div>'+
        '</div>'+
      '</div>';
    if(curCar) openChat(curCar);
  }catch(e){el.innerHTML='<div class="nd" style="color:#f44336">❌ '+esc(e.message)+'</div>';}
}

async function openChat(car){
  curCar=car;
  document.querySelectorAll('.di').forEach(function(el){el.classList.remove('act');});
  var di=document.getElementById('di-'+car);
  if(di) di.classList.add('act');
  var panel=document.getElementById('chat-panel');
  if(!panel) return;
  try{
    var r=await fetch('/api/admin/chat/'+car);
    var d=await r.json();
    var msgs=d.messages||[];
    panel.innerHTML=
      '<div style="font-size:14px;font-weight:700;margin-bottom:12px;color:#f5c518">💬 '+esc(car)+'</div>'+
      '<div class="cm" id="chat-msgs">'+
        (msgs.length===0?'<div style="margin:auto;color:#333">Нет сообщений</div>':
          msgs.map(function(m){return(
            '<div class="msg '+(m.sender==='admin'?'ma':'md')+'">'+
              '<div style="font-size:10px;font-weight:700;margin-bottom:2px;color:'+(m.sender==='admin'?'#f5c518':'#4caf50')+'">'+(m.sender==='admin'?'Диспетчер':'Водитель')+'</div>'+
              esc(m.text)+
              '<div class="mt">'+m.created_at+'</div>'+
            '</div>'
          );}).join(''))+
      '</div>'+
      '<div class="cir">'+
        '<input class="ci" id="ci" placeholder="Сообщение..." onkeypress="if(event.key===\'Enter\')sendChat()">'+
        '<button class="btn btn-p" onclick="sendChat()">Отправить</button>'+
      '</div>';
    var cm=document.getElementById('chat-msgs');
    if(cm) cm.scrollTop=cm.scrollHeight;
  }catch(e){if(panel)panel.innerHTML='<div class="nd" style="color:#f44336">❌ Ошибка</div>';}
}

async function sendChat(){
  var inp=document.getElementById('ci');
  if(!inp||!curCar) return;
  var txt=inp.value.trim();
  if(!txt) return;
  try{
    await fetch('/api/admin/chat/'+curCar,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:txt})});
    inp.value='';
    openChat(curCar);
  }catch(e){alert('Ошибка отправки');}
}

// ======= ФИНАНСЫ =======
async function loadFinances(){
  var el=document.getElementById('page-finances');
  try{
    var r=await fetch('/api/admin/finances');
    var d=await r.json();
    if(!d.success) throw new Error('Ошибка API');
    el.innerHTML=
      '<div class="sec"><div class="st">💳 Заявки на пополнение ('+d.balance_requests.length+')</div>'+
      (d.balance_requests.length===0?'<div class="nd">Нет заявок</div>':
        '<div class="tw"><table><thead><tr><th>Авто</th><th>Сумма</th><th>Время</th><th>Действие</th></tr></thead><tbody>'+
        d.balance_requests.map(function(req){return(
          '<tr><td><b>'+esc(req.car_number)+'</b></td>'+
          '<td style="color:#4caf50;font-weight:700">'+fmtM(req.amount)+'</td>'+
          '<td>'+fmtT(req.created_at)+'</td>'+
          '<td><button class="btn btn-s sm" onclick="approveBal('+req.id+')">✅ Одобрить</button></td></tr>'
        );}).join('')+
        '</tbody></table></div>')+
      '</div>'+
      '<div class="sec"><div class="st">📊 Транзакции</div>'+
      (d.transactions.length===0?'<div class="nd">Нет транзакций</div>':
        '<div class="tw"><table><thead><tr><th>Авто</th><th>Сумма</th><th>Тип</th><th>Комментарий</th><th>Время</th></tr></thead><tbody>'+
        d.transactions.map(function(t){return(
          '<tr><td><b>'+esc(t.car_number)+'</b></td>'+
          '<td style="color:'+(t.type==='income'?'#4caf50':'#2196f3')+';font-weight:700">'+fmtM(t.amount)+'</td>'+
          '<td>'+(t.type==='income'?'Доход':'Пополнение')+'</td>'+
          '<td>'+(esc(t.comment)||'—')+'</td>'+
          '<td>'+fmtT(t.created_at)+'</td></tr>'
        );}).join('')+
        '</tbody></table></div>')+
      '</div>';
  }catch(e){el.innerHTML='<div class="nd" style="color:#f44336">❌ '+esc(e.message)+'</div>';}
}

async function approveBal(id){
  if(!confirm('Одобрить пополнение?')) return;
  try{
    var r=await fetch('/api/admin/balance/approve/'+id,{method:'POST'});
    var d=await r.json();
    if(d.success){alert('✅ Баланс пополнен');loadFinances();}
    else alert('Ошибка');
  }catch(e){alert('Ошибка сети');}
}

// ======= ТАРИФЫ =======
async function loadSettings(){
  var el=document.getElementById('page-settings');
  try{
    var r=await fetch('/api/admin/tariffs');
    var d=await r.json();
    if(!d.success) throw new Error('Ошибка API');
    el.innerHTML='<div class="sec"><div class="st">⚙️ Тарифы</div>'+
      d.tariffs.map(function(t){return(
        '<div style="background:#1a1a1a;border:1px solid #1e1e1e;border-radius:10px;padding:16px;margin-bottom:10px">'+
          '<div style="font-weight:700;font-size:14px;margin-bottom:12px">'+esc(t.name)+'</div>'+
          '<div class="fr">'+
            '<div class="fg"><label>Посадка (сум)</label><input type="number" id="b'+t.id+'" value="'+t.base_fare+'"></div>'+
            '<div class="fg"><label>За км (сум)</label><input type="number" id="k'+t.id+'" value="'+t.rate_per_km+'"></div>'+
            '<div class="fg"><label>Ожидание/мин</label><input type="number" id="w'+t.id+'" value="'+t.wait_rate+'"></div>'+
            '<div class="fg" style="justify-content:flex-end"><label>&nbsp;</label><button class="btn btn-p" onclick="saveTar('+t.id+')">💾 Сохранить</button></div>'+
          '</div>'+
        '</div>'
      );}).join('')+
    '</div>';
  }catch(e){el.innerHTML='<div class="nd" style="color:#f44336">❌ '+esc(e.message)+'</div>';}
}

async function saveTar(id){
  var base=parseInt(document.getElementById('b'+id).value)||0;
  var km=parseInt(document.getElementById('k'+id).value)||0;
  var wait=parseInt(document.getElementById('w'+id).value)||0;
  try{
    var r=await fetch('/api/admin/tariffs/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({base_fare:base,rate_per_km:km,wait_rate:wait})});
    var d=await r.json();
    if(d.success) alert('✅ Сохранено!');
    else alert('Ошибка');
  }catch(e){alert('Ошибка сети');}
}

// СТАРТ
loadDash();
checkBadge();
</script>
</body></html>"""

@app.route("/")
@admin_required
def index():
    return render_template_string(ADMIN_HTML)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚕 TAXI 3042 запущен: http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
