from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import random
import time
import threading
import requests
import json
import sqlite3

app = Flask(__name__)

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS drivers (
        car_number TEXT PRIMARY KEY,
        name TEXT,
        phone TEXT,
        pin TEXT,
        balance INTEGER DEFAULT 50000,
        status TEXT DEFAULT 'offline'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        car_number TEXT,
        from_address TEXT,
        to_address TEXT,
        price INTEGER,
        client TEXT,
        status TEXT DEFAULT 'pending',
        created_at REAL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS ratings (
        car_number TEXT PRIMARY KEY,
        total INTEGER DEFAULT 0,
        count INTEGER DEFAULT 0,
        orders_count INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        car_number TEXT,
        sender TEXT,
        text TEXT,
        created_at REAL
    )''')
    conn.commit()
    conn.close()

init_db()

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
        print(f"Telegram error: {e}")

# ==================== БАЗА ДАННЫХ ФУНКЦИИ ====================
def db_get_all_drivers():
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('SELECT * FROM drivers')
    rows = c.fetchall()
    conn.close()
    return [{'car_number': r[0], 'name': r[1], 'phone': r[2],
             'pin': r[3], 'balance': r[4], 'status': r[5]} for r in rows]

def db_add_driver(car, name, phone, pin):
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO drivers
                (car_number, name, phone, pin, balance, status)
                VALUES (?, ?, ?, ?, 50000, "offline")''',
                (car, name, phone, pin))
    conn.commit()
    conn.close()

def db_delete_driver(car):
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('DELETE FROM drivers WHERE car_number=?', (car,))
    conn.commit()
    conn.close()

def db_update_balance(car, amount):
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('UPDATE drivers SET balance=? WHERE car_number=?', (amount, car))
    conn.commit()
    conn.close()

def db_update_status(car, status):
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('UPDATE drivers SET status=? WHERE car_number=?', (status, car))
    conn.commit()
    conn.close()

def db_get_orders():
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('SELECT * FROM orders ORDER BY created_at DESC')
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'car_number': r[1], 'from_address': r[2],
             'to_address': r[3], 'price': r[4], 'client': r[5],
             'status': r[6], 'created_at': r[7]} for r in rows]

def db_add_order(car, from_addr, to_addr, price, client):
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('''INSERT INTO orders
                (car_number, from_address, to_address, price, client, status, created_at)
                VALUES (?, ?, ?, ?, ?, "pending", ?)''',
                (car, from_addr, to_addr, price, client, time.time()))
    order_id = c.lastrowid
    conn.commit()
    conn.close()
    return order_id

def db_update_order_status(order_id, status):
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('UPDATE orders SET status=? WHERE id=?', (status, order_id))
    conn.commit()
    conn.close()

def db_get_rating(car):
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('SELECT * FROM ratings WHERE car_number=?', (car,))
    r = c.fetchone()
    conn.close()
    if not r:
        return {'avg': 5.0, 'count': 0, 'orders': 0}
    avg = round(r[1]/r[2], 1) if r[2] > 0 else 5.0
    return {'avg': avg, 'count': r[2], 'orders': r[3]}

def db_add_rating(car, stars):
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('''INSERT INTO ratings (car_number, total, count, orders_count)
                VALUES (?, ?, 1, 0)
                ON CONFLICT(car_number) DO UPDATE SET
                total=total+?, count=count+1''',
                (car, stars, stars))
    conn.commit()
    conn.close()

def db_get_messages(car):
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('SELECT * FROM messages WHERE car_number=? ORDER BY created_at',
              (car,))
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'car_number': r[1], 'sender': r[2],
             'text': r[3], 'created_at': r[4]} for r in rows]

def db_add_message(car, sender, text):
    conn = sqlite3.connect('taxi.db')
    c = conn.cursor()
    c.execute('''INSERT INTO messages (car_number, sender, text, created_at)
                VALUES (?, ?, ?, ?)''', (car, sender, text, time.time()))
    conn.commit()
    conn.close()

# ==================== ОНЛАЙН ВОДИТЕЛИ В ПАМЯТИ ====================
online_drivers = {}

# ==================== API МАРШРУТЫ ====================

# --- Водители ---
@app.route('/api/drivers', methods=['GET'])
def get_drivers():
    drivers = db_get_all_drivers()
    for d in drivers:
        if d['car_number'] in online_drivers:
            d['status'] = online_drivers[d['car_number']].get('status', 'free')
            d['speed'] = online_drivers[d['car_number']].get('speed', 0)
            d['lat'] = online_drivers[d['car_number']].get('lat', 0)
            d['lon'] = online_drivers[d['car_number']].get('lon', 0)
        else:
            d['status'] = 'offline'
            d['speed'] = 0
    rating = db_get_rating(d['car_number'])
    d['rating'] = rating['avg']
    return jsonify({'ok': True, 'drivers': drivers})

@app.route('/api/drivers/add', methods=['POST'])
def add_driver():
    data  = request.json
    car   = data.get('car_number', '').strip().upper()
    name  = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    if not car or not name or not phone:
        return jsonify({'ok': False, 'error': 'Заполните все поля!'})
    pin = str(random.randint(1000, 9999))
    db_add_driver(car, name, phone, pin)
    tg_send(f"✅ Новый водитель добавлен!\n🚗 {car}\n👤 {name}\n📱 {phone}\n🔐 ПИН: {pin}")
    return jsonify({'ok': True, 'pin': pin,
                    'message': f'Водитель добавлен! ПИН: {pin}'})

@app.route('/api/drivers/delete', methods=['POST'])
def delete_driver():
    data = request.json
    car  = data.get('car_number', '').strip().upper()
    if not car:
        return jsonify({'ok': False, 'error': 'Укажите номер авто!'})
    db_delete_driver(car)
    online_drivers.pop(car, None)
    return jsonify({'ok': True, 'message': f'Водитель {car} удалён!'})

@app.route('/api/drivers/balance', methods=['POST'])
def update_balance():
    data   = request.json
    car    = data.get('car_number', '').strip().upper()
    amount = data.get('amount', 0)
    if not car:
        return jsonify({'ok': False, 'error': 'Укажите номер авто!'})
    db_update_balance(car, amount)
    return jsonify({'ok': True, 'message': f'Баланс обновлён!'})

# --- Онлайн статус ---
@app.route('/api/driver/ping', methods=['POST'])
def driver_ping():
    data = request.json
    car  = data.get('car_number', '').strip().upper()
    if not car:
        return jsonify({'ok': False})
    online_drivers[car] = {
        'car_number': car,
        'status': data.get('status', 'free'),
        'speed': data.get('speed', 0),
        'lat': data.get('lat', 0),
        'lon': data.get('lon', 0),
        'last_ping': time.time()
    }
    db_update_status(car, data.get('status', 'free'))
    orders = db_get_orders()
    pending = [o for o in orders
               if o['car_number'] == car and o['status'] == 'pending']
    return jsonify({'ok': True, 'orders': pending})

# --- Заказы ---
@app.route('/api/orders', methods=['GET'])
def get_orders():
    orders = db_get_orders()
    return jsonify({'ok': True, 'orders': orders})

@app.route('/api/orders/create', methods=['POST'])
def create_order():
    data  = request.json
    car   = data.get('car_number', '').strip().upper()
    frm   = data.get('from_address', '').strip()
    to    = data.get('to_address', '').strip()
    price = data.get('price', 0)
    client = data.get('client', 'Клиент').strip()
    if not car or not frm or not to or not price:
        return jsonify({'ok': False, 'error': 'Заполните все поля!'})
    order_id = db_add_order(car, frm, to, price, client)
    tg_send(
        f"📦 <b>Новый заказ #{order_id}</b>\n"
        f"🚗 {car}\n"
        f"📍 {frm} → {to}\n"
        f"💰 {price:,} сум\n"
        f"👤 {client}"
    )
    return jsonify({'ok': True, 'order_id': order_id,
                    'message': f'Заказ #{order_id} создан!'})

@app.route('/api/orders/broadcast', methods=['POST'])
def broadcast_order():
    data   = request.json
    frm    = data.get('from_address', '').strip()
    to     = data.get('to_address', '').strip()
    price  = data.get('price', 0)
    client = data.get('client', 'Клиент').strip()
    drivers = db_get_all_drivers()
    count = 0
    for d in drivers:
        if d['car_number'] in online_drivers:
            db_add_order(d['car_number'], frm, to, price, client)
            count += 1
    tg_send(
        f"📢 <b>Рассылка заказа</b>\n"
        f"📍 {frm} → {to}\n"
        f"💰 {price:,} сум\n"
        f"👥 Отправлено: {count} водителям"
    )
    return jsonify({'ok': True, 'message': f'Заказ отправлен {count} водителям!'})

@app.route('/api/orders/complete', methods=['POST'])
def complete_order():
    data     = request.json
    order_id = data.get('order_id')
    stars    = data.get('stars', 5)
    db_update_order_status(order_id, 'completed')
    orders = db_get_orders()
    order  = next((o for o in orders if o['id'] == order_id), None)
    if order:
        db_add_rating(order['car_number'], stars)
        db_update_balance(
            order['car_number'],
            db_get_all_drivers()[0]['balance'] + order['price']
        )
    return jsonify({'ok': True, 'message': 'Заказ завершён!'})

@app.route('/api/orders/cancel', methods=['POST'])
def cancel_order():
    data     = request.json
    order_id = data.get('order_id')
    db_update_order_status(order_id, 'cancelled')
    return jsonify({'ok': True, 'message': 'Заказ отменён!'})

# --- Статистика ---
@app.route('/api/stats', methods=['GET'])
def get_stats():
    drivers = db_get_all_drivers()
    orders  = db_get_orders()
    online  = sum(1 for d in drivers
                  if d['car_number'] in online_drivers)
    free    = sum(1 for d in drivers
                  if online_drivers.get(d['car_number'], {}).get('status') == 'free')
    busy    = sum(1 for d in drivers
                  if online_drivers.get(d['car_number'], {}).get('status') == 'busy')
    active  = sum(1 for o in orders if o['status'] == 'pending')
    revenue = sum(o['price'] for o in orders if o['status'] == 'completed')
    return jsonify({
        'ok': True,
        'online': online,
        'free': free,
        'busy': busy,
        'total_drivers': len(drivers),
        'active_orders': active,
        'revenue': revenue
    })

# --- Чат ---
@app.route('/api/chat/<car>', methods=['GET'])
def get_chat(car):
    messages = db_get_messages(car.upper())
    return jsonify({'ok': True, 'messages': messages})

@app.route('/api/chat/send', methods=['POST'])
def send_message():
    data   = request.json
    car    = data.get('car_number', '').upper()
    text   = data.get('text', '').strip()
    sender = data.get('sender', 'admin')
    if not car or not text:
        return jsonify({'ok': False, 'error': 'Пустое сообщение!'})
    db_add_message(car, sender, text)
    return jsonify({'ok': True, 'message': 'Отправлено!'})

# --- Рейтинг ---
@app.route('/api/rating', methods=['GET'])
def get_rating():
    drivers = db_get_all_drivers()
    result  = []
    for d in drivers:
        r = db_get_rating(d['car_number'])
        result.append({
            'car_number': d['car_number'],
            'name': d['name'],
            'avg': r['avg'],
            'count': r['count'],
            'orders': r['orders']
        })
    result.sort(key=lambda x: x['avg'], reverse=True)
    return jsonify({'ok': True, 'ratings': result})

# --- Авторизация водителя ---
@app.route('/api/driver/login', methods=['POST'])
def driver_login():
    data = request.json
    car  = data.get('car_number', '').strip().upper()
    pin  = data.get('pin', '').strip()
    conn = sqlite3.connect('taxi.db')
    c    = conn.cursor()
    c.execute('SELECT * FROM drivers WHERE car_number=? AND pin=?', (car, pin))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({'ok': False, 'error': 'Неверный номер или ПИН!'})
    return jsonify({
        'ok': True,
        'driver': {
            'car_number': row[0],
            'name': row[1],
            'phone': row[2],
            'balance': row[4]
        }
    })

# ==================== ГЛАВНАЯ СТРАНИЦА ====================
@app.route('/')
def index():
    return render_template_string(ADMIN_HTML)

# ==================== HTML ПАНЕЛЬ ====================
ADMIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TAXI 3042 — Xazarasp</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'Segoe UI', sans-serif;
    background: #0a0a0a;
    color: #fff;
    min-height: 100vh;
}

/* NAVBAR */
.navbar {
    background: #111;
    border-bottom: 2px solid #FFD700;
    padding: 0 20px;
    display: flex;
    align-items: center;
    gap: 10px;
    height: 50px;
    position: sticky;
    top: 0;
    z-index: 100;
}
.navbar .logo {
    font-size: 18px;
    font-weight: bold;
    color: #FFD700;
    margin-right: 20px;
}
.nav-btn {
    background: none;
    border: none;
    color: #aaa;
    padding: 8px 16px;
    cursor: pointer;
    border-radius: 6px;
    font-size: 13px;
    transition: all 0.2s;
}
.nav-btn:hover, .nav-btn.active {
    background: #FFD700;
    color: #000;
    font-weight: bold;
}
.navbar .right {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 10px;
}
.live-badge {
    background: #1a3a1a;
    color: #4CAF50;
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 12px;
    border: 1px solid #4CAF50;
}
.clock {
    color: #FFD700;
    font-size: 14px;
    font-weight: bold;
}

/* СТРАНИЦЫ */
.page { display: none; padding: 20px; }
.page.active { display: block; }

/* КАРТОЧКИ СТАТИСТИКИ */
.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 15px;
    margin-bottom: 20px;
}
.stat-card {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 12px;
    padding: 20px;
    text-align: center;
    transition: transform 0.2s;
}
.stat-card:hover { transform: translateY(-3px); }
.stat-card .num {
    font-size: 32px;
    font-weight: bold;
    margin-bottom: 5px;
}
.stat-card .label {
    font-size: 11px;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 1px;
}

/* БЫСТРЫЕ ДЕЙСТВИЯ */
.quick-actions {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 20px;
}
.quick-actions h3 {
    color: #FFD700;
    margin-bottom: 15px;
    font-size: 14px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
.btn-row {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
}
.btn {
    padding: 10px 20px;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-size: 13px;
    font-weight: bold;
    transition: all 0.2s;
    display: flex;
    align-items: center;
    gap: 6px;
}
.btn:hover { opacity: 0.85; transform: translateY(-1px); }
.btn-yellow  { background: #FFD700; color: #000; }
.btn-blue    { background: #1E88E5; color: #fff; }
.btn-green   { background: #43A047; color: #fff; }
.btn-red     { background: #E53935; color: #fff; }
.btn-purple  { background: #8E24AA; color: #fff; }
.btn-gray    { background: #333; color: #fff; }

/* ТАБЛИЦА */
.table-box {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 12px;
    overflow: hidden;
}
.table-box h3 {
    padding: 15px 20px;
    color: #FFD700;
    font-size: 14px;
    text-transform: uppercase;
    letter-spacing: 1px;
    border-bottom: 1px solid #333;
}
table {
    width: 100%;
    border-collapse: collapse;
}
th {
    background: #222;
    padding: 12px 15px;
    text-align: left;
    font-size: 11px;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 1px;
}
td {
    padding: 12px 15px;
    border-bottom: 1px solid #1a1a1a;
    font-size: 13px;
}
tr:hover td { background: #222; }
.badge {
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: bold;
}
.badge-green  { background: #1a3a1a; color: #4CAF50; }
.badge-red    { background: #3a1a1a; color: #f44336; }
.badge-yellow { background: #3a3a1a; color: #FFD700; }
.badge-gray   { background: #2a2a2a; color: #888; }

/* МОДАЛЬНОЕ ОКНО */
.modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.8);
    z-index: 1000;
    align-items: center;
    justify-content: center;
}
.modal-overlay.open { display: flex; }
.modal {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 16px;
    padding: 30px;
    width: 90%;
    max-width: 500px;
    position: relative;
}
.modal h2 {
    color: #FFD700;
    margin-bottom: 20px;
    font-size: 18px;
}
.modal-close {
    position: absolute;
    top: 15px;
    right: 15px;
    background: none;
    border: none;
    color: #666;
    font-size: 20px;
    cursor: pointer;
}
.modal-close:hover { color: #fff; }
.form-group {
    margin-bottom: 15px;
}
.form-group label {
    display: block;
    font-size: 12px;
    color: #888;
    margin-bottom: 6px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
.form-group input,
.form-group select,
.form-group textarea {
    width: 100%;
    background: #111;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 10px 14px;
    color: #fff;
    font-size: 14px;
    outline: none;
    transition: border 0.2s;
}
.form-group input:focus,
.form-group select:focus {
    border-color: #FFD700;
}
.form-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
}
.addr-btns {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 8px;
}
.addr-btn {
    background: #222;
    border: 1px solid #444;
    color: #ccc;
    padding: 5px 10px;
    border-radius: 6px;
    font-size: 11px;
    cursor: pointer;
    transition: all 0.2s;
}
.addr-btn:hover {
    background: #FFD700;
    color: #000;
    border-color: #FFD700;
}

/* ЧАТ */
.chat-layout {
    display: grid;
    grid-template-columns: 250px 1fr;
    gap: 15px;
    height: calc(100vh - 140px);
}
.chat-list {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 12px;
    overflow-y: auto;
}
.chat-list h3 {
    padding: 15px;
    color: #FFD700;
    font-size: 13px;
    border-bottom: 1px solid #333;
}
.chat-item {
    padding: 12px 15px;
    cursor: pointer;
    border-bottom: 1px solid #222;
    transition: background 0.2s;
}
.chat-item:hover, .chat-item.active { background: #222; }
.chat-item .car { font-weight: bold; font-size: 13px; }
.chat-item .preview { font-size: 11px; color: #666; margin-top: 3px; }
.chat-window {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 12px;
    display: flex;
    flex-direction: column;
}
.chat-header {
    padding: 15px;
    border-bottom: 1px solid #333;
    font-weight: bold;
    color: #FFD700;
}
.chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 15px;
    display: flex;
    flex-direction: column;
    gap: 10px;
}
.msg {
    max-width: 70%;
    padding: 10px 14px;
    border-radius: 12px;
    font-size: 13px;
    line-height: 1.4;
}
.msg.admin {
    background: #FFD700;
    color: #000;
    align-self: flex-end;
    border-bottom-right-radius: 4px;
}
.msg.driver {
    background: #222;
    color: #fff;
    align-self: flex-start;
    border-bottom-left-radius: 4px;
}
.chat-input-row {
    padding: 15px;
    border-top: 1px solid #333;
    display: flex;
    gap: 10px;
}
.chat-input-row input {
    flex: 1;
    background: #111;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 10px 14px;
    color: #fff;
    font-size: 14px;
    outline: none;
}
.chat-input-row input:focus { border-color: #FFD700; }

/* УВЕДОМЛЕНИЕ */
.toast {
    position: fixed;
    bottom: 30px;
    right: 30px;
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 10px;
    padding: 14px 20px;
    font-size: 14px;
    z-index: 9999;
    display: none;
    animation: slideIn 0.3s ease;
    max-width: 300px;
}
@keyframes slideIn {
    from { transform: translateX(100px); opacity: 0; }
    to   { transform: translateX(0);     opacity: 1; }
}

/* ПУСТОЕ СОСТОЯНИЕ */
.empty-state {
    text-align: center;
    padding: 60px 20px;
    color: #444;
}
.empty-state .icon { font-size: 50px; margin-bottom: 15px; }
.empty-state p { font-size: 14px; }

/* ФИНАНСЫ */
.finance-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 15px;
    margin-bottom: 20px;
}
.finance-card {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 12px;
    padding: 20px;
}
.finance-card h4 {
    color: #888;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 10px;
}
.finance-card .amount {
    font-size: 24px;
    font-weight: bold;
    color: #FFD700;
}
</style>
</head>
<body>

<!-- NAVBAR -->
<nav class="navbar">
    <div class="logo">🚕 TAXI 3042</div>
    <button class="nav-btn active" onclick="showPage('dashboard')">📊 Дашборд</button>
    <button class="nav-btn" onclick="showPage('orders')">📦 Заказы</button>
    <button class="nav-btn" onclick="showPage('drivers')">🚗 Водители</button>
    <button class="nav-btn" onclick="showPage('rating')">⭐ Рейтинг</button>
    <button class="nav-btn" onclick="showPage('chat')">💬 Чат</button>
    <button class="nav-btn" onclick="showPage('finance')">💰 Финансы</button>
    <button class="nav-btn" onclick="showPage('settings')">⚙️ Настройки</button>
    <div class="right">
        <span class="clock" id="clock">00:00:00</span>
        <span class="live-badge">🟢 Live</span>
    </div>
</nav>

<!-- ДАШБОРД -->
<div class="page active" id="page-dashboard">
    <div class="stats-grid">
        <div class="stat-card">
            <div class="num" id="stat-online" style="color:#4CAF50">0</div>
            <div class="label">На линии</div>
        </div>
        <div class="stat-card">
            <div class="num" id="stat-free" style="color:#2196F3">0</div>
            <div class="label">Свободны</div>
        </div>
        <div class="stat-card">
            <div class="num" id="stat-busy" style="color:#f44336">0</div>
            <div class="label">На заказе</div>
        </div>
        <div class="stat-card">
            <div class="num" id="stat-total" style="color:#FFD700">0</div>
            <div class="label">Всего водит.</div>
        </div>
        <div class="stat-card">
            <div class="num" id="stat-orders" style="color:#9C27B0">0</div>
            <div class="label">Активн. заказов</div>
        </div>
        <div class="stat-card">
            <div class="num" id="stat-revenue" style="color:#4CAF50">0</div>
            <div class="label">Выручка (сум)</div>
        </div>
    </div>

    <div class="quick-actions">
        <h3>⚡ Быстрые действия</h3>
        <div class="btn-row">
            <button class="btn btn-yellow" onclick="openModal('modalOrder')">
                🚖 Новый заказ
            </button>
            <button class="btn btn-blue" onclick="showPage('chat')">
                💬 Открыть чат
            </button>
            <button class="btn btn-green" onclick="openModal('modalDriver')">
                ➕ Добавить водителя
            </button>
            <button class="btn btn-purple" onclick="exportReport()">
                📊 Экспорт отчёта
            </button>
            <button class="btn btn-red" onclick="resetShift()">
                🔄 Сбросить смену
            </button>
        </div>
    </div>

    <div class="table-box">
        <h3>🚗 Водители онлайн</h3>
        <table>
            <thead>
                <tr>
                    <th>Авто</th>
                    <th>Водитель</th>
                    <th>Статус</th>
                    <th>Скорость</th>
                    <th>Баланс</th>
                    <th>Рейтинг</th>
                    <th>Действия</th>
                </tr>
            </thead>
            <tbody id="driversTable">
                <tr>
                    <td colspan="7">
                        <div class="empty-state">
                            <div class="icon">🚗</div>
                            <p>Водители выйдут на линию</p>
                        </div>
                    </td>
                </tr>
            </tbody>
        </table>
    </div>
</div>

<!-- ЗАКАЗЫ -->
<div class="page" id="page-orders">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;">
        <h2 style="color:#FFD700;">📦 Заказы</h2>
        <button class="btn btn-yellow" onclick="openModal('modalOrder')">
            ➕ Новый заказ
        </button>
    </div>
    <div class="table-box">
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Авто</th>
                    <th>Откуда</th>
                    <th>Куда</th>
                    <th>Цена</th>
                    <th>Клиент</th>
                    <th>Статус</th>
                    <th>Действия</th>
                </tr>
            </thead>
            <tbody id="ordersTable">
                <tr><td colspan="8">
                    <div class="empty-state">
                        <div class="icon">📦</div>
                        <p>Нет заказов</p>
                    </div>
                </td></tr>
            </tbody>
        </table>
    </div>
</div>

<!-- ВОДИТЕЛИ -->
<div class="page" id="page-drivers">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;">
        <h2 style="color:#FFD700;">🚗 Водители</h2>
        <button class="btn btn-yellow" onclick="openModal('modalDriver')">
            ➕ Добавить водителя
        </button>
    </div>
    <div class="table-box">
        <table>
            <thead>
                <tr>
                    <th>Авто</th>
                    <th>Имя</th>
                    <th>Телефон</th>
                    <th>ПИН</th>
                    <th>Баланс</th>
                    <th>Статус</th>
                    <th>Рейтинг</th>
                    <th>Действия</th>
                </tr>
            </thead>
            <tbody id="allDriversTable">
                <tr><td colspan="8">
                    <div class="empty-state">
                        <div class="icon">🚗</div>
                        <p>Нет водителей</p>
                    </div>
                </td></tr>
            </tbody>
        </table>
    </div>
</div>

<!-- РЕЙТИНГ -->
<div class="page" id="page-rating">
    <h2 style="color:#FFD700;margin-bottom:15px;">⭐ Рейтинг водителей</h2>
    <div class="table-box">
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Авто</th>
                    <th>Имя</th>
                    <th>Рейтинг</th>
                    <th>Оценок</th>
                    <th>Заказов</th>
                </tr>
            </thead>
            <tbody id="ratingTable">
                <tr><td colspan="6">
                    <div class="empty-state">
                        <div class="icon">⭐</div>
                        <p>Нет данных</p>
                    </div>
                </td></tr>
            </tbody>
        </table>
    </div>
</div>

<!-- ЧАТ -->
<div class="page" id="page-chat">
    <h2 style="color:#FFD700;margin-bottom:15px;">💬 Чат с водителями</h2>
    <div class="chat-layout">
        <div class="chat-list">
            <h3>Водители</h3>
            <div id="chatDriversList"></div>
        </div>
        <div class="chat-window">
            <div class="chat-header" id="chatHeader">
                Выберите водителя
            </div>
            <div class="chat-messages" id="chatMessages">
                <div class="empty-state">
                    <div class="icon">💬</div>
                    <p>Выберите водителя для чата</p>
                </div>
            </div>
            <div class="chat-input-row">
                <input type="text" id="chatInput"
                       placeholder="Написать сообщение..."
                       onkeypress="if(event.key==='Enter') sendChat()">
                <button class="btn btn-yellow" onclick="sendChat()">
                    ➤ Отправить
                </button>
            </div>
        </div>
    </div>
</div>

<!-- ФИНАНСЫ -->
<div class="page" id="page-finance">
    <h2 style="color:#FFD700;margin-bottom:15px;">💰 Финансы</h2>
    <div class="finance-grid">
        <div class="finance-card">
            <h4>Общая выручка</h4>
            <div class="amount" id="finRevenue">0 сум</div>
        </div>
        <div class="finance-card">
            <h4>Завершённых заказов</h4>
            <div class="amount" id="finCompleted">0</div>
        </div>
        <div class="finance-card">
            <h4>Отменённых заказов</h4>
            <div class="amount" id="finCancelled">0</div>
        </div>
        <div class="finance-card">
            <h4>Всего заказов</h4>
            <div class="amount" id="finTotal">0</div>
        </div>
    </div>
    <div class="table-box">
        <h3>💳 Балансы водителей</h3>
        <table>
            <thead>
                <tr>
                    <th>Авто</th>
                    <th>Имя</th>
                    <th>Баланс</th>
                    <th>Действия</th>
                </tr>
            </thead>
            <tbody id="balanceTable"></tbody>
        </table>
    </div>
</div>

<!-- НАСТРОЙКИ -->
<div class="page" id="page-settings">
    <h2 style="color:#FFD700;margin-bottom:20px;">⚙️ Настройки</h2>
    <div class="quick-actions">
        <h3>🚕 Тарифы</h3>
        <div class="form-row">
            <div class="form-group">
                <label>Базовая ставка (сум)</label>
                <input type="number" id="setBaseFare" value="5000">
            </div>
            <div class="form-group">
                <label>Город (сум/км)</label>
                <input type="number" id="setCityRate" value="2800">
            </div>
            <div class="form-group">
                <label>Пригород (сум/км)</label>
                <input type="number" id="setSuburbRate" value="3000">
            </div>
            <div class="form-group">
                <label>Ожидание (сум/мин)</label>
                <input type="number" id="setWaitRate" value="500">
            </div>
        </div>
        <button class="btn btn-yellow" onclick="saveTariffs()">
            💾 Сохранить тарифы
        </button>
    </div>
</div>

<!-- МОДАЛ: НОВЫЙ ЗАКАЗ -->
<div class="modal-overlay" id="modalOrder">
    <div class="modal">
        <button class="modal-close" onclick="closeModal('modalOrder')">✕</button>
        <h2>🚖 Новый заказ</h2>
        <div class="form-group">
            <label>Водитель</label>
            <select id="orderCar">
                <option value="ALL">📢 Всем водителям</option>
            </select>
        </div>
        <div class="form-group">
            <label>Откуда</label>
            <input type="text" id="orderFrom" placeholder="Адрес отправления">
            <div class="addr-btns">
                <button class="addr-btn" onclick="setAddr('orderFrom','Bozor')">📍 Bozor</button>
                <button class="addr-btn" onclick="setAddr('orderFrom','Aeroport')">✈️ Aeroport</button>
                <button class="addr-btn" onclick="setAddr('orderFrom','Kasalxona')">🏥 Kasalxona</button>
                <button class="addr-btn" onclick="setAddr('orderFrom','Vokzal')">🚉 Vokzal</button>
                <button class="addr-btn" onclick="setAddr('orderFrom','Maktab')">🏫 Maktab</button>
                <button class="addr-btn" onclick="setAddr('orderFrom','Markaziy')">🛒 Markaziy</button>
            </div>
        </div>
        <div class="form-group">
            <label>Куда</label>
            <input type="text" id="orderTo" placeholder="Адрес назначения">
            <div class="addr-btns">
                <button class="addr-btn" onclick="setAddr('orderTo','Bozor')">📍 Bozor</button>
                <button class="addr-btn" onclick="setAddr('orderTo','Aeroport')">✈️ Aeroport</button>
                <button class="addr-btn" onclick="setAddr('orderTo','Kasalxona')">🏥 Kasalxona</button>
                <button class="addr-btn" onclick="setAddr('orderTo','Vokzal')">🚉 Vokzal</button>
                <button class="addr-btn" onclick="setAddr('orderTo','Maktab')">🏫 Maktab</button>
                <button class="addr-btn" onclick="setAddr('orderTo','Markaziy')">🛒 Markaziy</button>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>Цена (сум)</label>
                <input type="number" id="orderPrice" placeholder="25000">
            </div>
            <div class="form-group">
                <label>Клиент</label>
                <input type="text" id="orderClient" placeholder="Имя клиента">
            </div>
        </div>
        <button class="btn btn-yellow" style="width:100%;justify-content:center;"
                onclick="submitOrder()">
            ✅ Создать заказ
        </button>
    </div>
</div>

<!-- МОДАЛ: ДОБАВИТЬ ВОДИТЕЛЯ -->
<div class="modal-overlay" id="modalDriver">
    <div class="modal">
        <button class="modal-close" onclick="closeModal('modalDriver')">✕</button>
        <h2>➕ Добавить водителя</h2>
        <div class="form-group">
            <label>Номер авто</label>
            <input type="text" id="driverCar" placeholder="90T785OA">
        </div>
        <div class="form-group">
            <label>Имя водителя</label>
            <input type="text" id="driverName" placeholder="Иван Иванов">
        </div>
        <div class="form-group">
            <label>Телефон</label>
            <input type="text" id="driverPhone" placeholder="+998901234567">
        </div>
        <button class="btn btn-yellow" style="width:100%;justify-content:center;"
                onclick="submitDriver()">
            ✅ Добавить
        </button>
        <div id="driverMsg" style="margin-top:15px;text-align:center;font-size:14px;"></div>
    </div>
</div>

<!-- МОДАЛ: ПОПОЛНИТЬ БАЛАНС -->
<div class="modal-overlay" id="modalBalance">
    <div class="modal">
        <button class="modal-close" onclick="closeModal('modalBalance')">✕</button>
        <h2>💰 Пополнить баланс</h2>
        <input type="hidden" id="balanceCar">
        <div class="form-group">
            <label>Новый баланс (сум)</label>
            <input type="number" id="balanceAmount" placeholder="50000">
        </div>
        <button class="btn btn-yellow" style="width:100%;justify-content:center;"
                onclick="submitBalance()">
            ✅ Обновить
        </button>
    </div>
</div>

<!-- УВЕДОМЛЕНИЕ -->
<div class="toast" id="toast"></div>

<script>
// ==================== НАВИГАЦИЯ ====================
function showPage(name) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('page-' + name).classList.add('active');
    event.target.classList.add('active');

    if (name === 'orders')  loadOrders();
    if (name === 'drivers') loadAllDrivers();
    if (name === 'rating')  loadRating();
    if (name === 'chat')    loadChatDrivers();
    if (name === 'finance') loadFinance();
}

// ==================== МОДАЛЫ ====================
function openModal(id) {
    document.getElementById(id).classList.add('open');
    if (id === 'modalOrder') loadDriversSelect();
}
function closeModal(id) {
    document.getElementById(id).classList.remove('open');
}
document.querySelectorAll('.modal-overlay').forEach(m => {
    m.addEventListener('click', function(e) {
        if (e.target === this) this.classList.remove('open');
    });
});

// ==================== ЧАСЫ ====================
function updateClock() {
    const now = new Date();
    document.getElementById('clock').textContent =
        now.toTimeString().slice(0,8);
}
setInterval(updateClock, 1000);
updateClock();

// ==================== УВЕДОМЛЕНИЯ ====================
function toast(msg, color='#4CAF50') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.style.color = color;
    t.style.display = 'block';
    setTimeout(() => t.style.display = 'none', 3000);
}

// ==================== СТАТИСТИКА ====================
async function loadStats() {
    try {
        const r = await fetch('/api/stats');
        const d = await r.json();
        if (!d.ok) return;
        document.getElementById('stat-online').textContent  = d.online;
        document.getElementById('stat-free').textContent   = d.free;
        document.getElementById('stat-busy').textContent   = d.busy;
        document.getElementById('stat-total').textContent  = d.total_drivers;
        document.getElementById('stat-orders').textContent = d.active_orders;
        document.getElementById('stat-revenue').textContent =
            d.revenue.toLocaleString();
    } catch(e) { console.error(e); }
}

// ==================== ВОДИТЕЛИ ОНЛАЙН ====================
async function loadOnlineDrivers() {
    try {
        const r = await fetch('/api/drivers');
        const d = await r.json();
        if (!d.ok) return;
        const tbody = document.getElementById('driversTable');
        const online = d.drivers.filter(dr => dr.status !== 'offline');
        if (!online.length) {
            tbody.innerHTML = `<tr><td colspan="7">
                <div class="empty-state">
                    <div class="icon">🚗</div>
                    <p>Водители выйдут на линию</p>
                </div></td></tr>`;
            return;
        }
        tbody.innerHTML = online.map(dr => `
            <tr>
                <td><b>${dr.car_number}</b></td>
                <td>${dr.name}</td>
                <td>${statusBadge(dr.status)}</td>
                <td>${dr.speed || 0} км/ч</td>
                <td>${(dr.balance||0).toLocaleString()} сум</td>
                <td>${stars(dr.rating||5.0)} ${dr.rating||5.0}</td>
                <td>
                    <button class="btn btn-yellow" style="padding:5px 10px;font-size:11px;"
                        onclick="openOrder('${dr.car_number}')">📦 Заказ</button>
                    <button class="btn btn-blue" style="padding:5px 10px;font-size:11px;"
                        onclick="openChatWith('${dr.car_number}')">💬</button>
                </td>
            </tr>
        `).join('');
    } catch(e) { console.error(e); }
}

// ==================== ВСЕ ВОДИТЕЛИ ====================
async function loadAllDrivers() {
    try {
        const r = await fetch('/api/drivers');
        const d = await r.json();
        if (!d.ok) return;
        const tbody = document.getElementById('allDriversTable');
        if (!d.drivers.length) {
            tbody.innerHTML = `<tr><td colspan="8">
                <div class="empty-state">
                    <div class="icon">🚗</div>
                    <p>Нет водителей. Добавьте первого!</p>
                </div></td></tr>`;
            return;
        }
        tbody.innerHTML = d.drivers.map(dr => `
            <tr>
                <td><b>${dr.car_number}</b></td>
                <td>${dr.name}</td>
                <td>${dr.phone}</td>
                <td><span class="badge badge-yellow">${dr.pin}</span></td>
                <td>${(dr.balance||0).toLocaleString()} сум</td>
                <td>${statusBadge(dr.status)}</td>
                <td>${stars(dr.rating||5.0)} ${dr.rating||5.0}</td>
                <td>
                    <button class="btn btn-green" style="padding:5px 10px;font-size:11px;"
                        onclick="editBalance('${dr.car_number}',${dr.balance||0})">
                        💰 Баланс
                    </button>
                    <button class="btn btn-red" style="padding:5px 10px;font-size:11px;"
                        onclick="deleteDriver('${dr.car_number}')">
                        🗑️
                    </button>
                </td>
            </tr>
        `).join('');
    } catch(e) { console.error(e); }
}

// ==================== ЗАКАЗЫ ====================
async function loadOrders() {
    try {
        const r = await fetch('/api/orders');
        const d = await r.json();
        if (!d.ok) return;
        const tbody = document.getElementById('ordersTable');
        if (!d.orders.length) {
            tbody.innerHTML = `<tr><td colspan="8">
                <div class="empty-state">
                    <div class="icon">📦</div>
                    <p>Нет заказов</p>
                </div></td></tr>`;
            return;
        }
        tbody.innerHTML = d.orders.map(o => `
            <tr>
                <td>#${o.id}</td>
                <td><b>${o.car_number}</b></td>
                <td>${o.from_address}</td>
                <td>${o.to_address}</td>
                <td>${(o.price||0).toLocaleString()} сум</td>
                <td>${o.client||'—'}</td>
                <td>${orderBadge(o.status)}</td>
                <td>
                    ${o.status === 'pending' ? `
                    <button class="btn btn-green" style="padding:5px 10px;font-size:11px;"
                        onclick="completeOrder(${o.id})">✅</button>
                    <button class="btn btn-red" style="padding:5px 10px;font-size:11px;"
                        onclick="cancelOrder(${o.id})">❌</button>
                    ` : '—'}
                </td>
            </tr>
        `).join('');
    } catch(e) { console.error(e); }
}

// ==================== РЕЙТИНГ ====================
async function loadRating() {
    try {
        const r = await fetch('/api/rating');
        const d = await r.json();
        if (!d.ok) return;
        const tbody = document.getElementById('ratingTable');
        if (!d.ratings.length) {
            tbody.innerHTML = `<tr><td colspan="6">
                <div class="empty-state">
                    <div class="icon">⭐</div>
                    <p>Нет данных</p>
                </div></td></tr>`;
            return;
        }
        tbody.innerHTML = d.ratings.map((r, i) => `
            <tr>
                <td>${i+1}</td>
                <td><b>${r.car_number}</b></td>
                <td>${r.name}</td>
                <td>${stars(r.avg)} ${r.avg}</td>
                <td>${r.count}</td>
                <td>${r.orders}</td>
            </tr>
        `).join('');
    } catch(e) { console.error(e); }
}

// ==================== ЧАТ ====================
let currentChatCar = null;
let chatInterval   = null;

async function loadChatDrivers() {
    try {
        const r = await fetch('/api/drivers');
        const d = await r.json();
        if (!d.ok) return;
        const list = document.getElementById('chatDriversList');
        if (!d.drivers.length) {
            list.innerHTML = '<div style="padding:15px;color:#666;font-size:13px;">Нет водителей</div>';
            return;
        }
        list.innerHTML = d.drivers.map(dr => `
            <div class="chat-item" onclick="openChatWith('${dr.car_number}')">
                <div class="car">${dr.car_number}</div>
                <div class="preview">${dr.name}</div>
            </div>
        `).join('');
    } catch(e) { console.error(e); }
}

async function openChatWith(car) {
    currentChatCar = car;
    document.getElementById('chatHeader').textContent = `💬 Чат с ${car}`;
    document.querySelectorAll('.chat-item').forEach(i => i.classList.remove('active'));
    showPage('chat');
    await loadChatMessages();
    if (chatInterval) clearInterval(chatInterval);
    chatInterval = setInterval(loadChatMessages, 3000);
}

async function loadChatMessages() {
    if (!currentChatCar) return;
    try {
        const r = await fetch(`/api/chat/${currentChatCar}`);
        const d = await r.json();
        if (!d.ok) return;
        const box = document.getElementById('chatMessages');
        if (!d.messages.length) {
            box.innerHTML = '<div class="empty-state"><div class="icon">💬</div><p>Нет сообщений</p></div>';
            return;
        }
        box.innerHTML = d.messages.map(m => `
            <div class="msg ${m.sender === 'admin' ? 'admin' : 'driver'}">
                ${m.text}
            </div>
        `).join('');
        box.scrollTop = box.scrollHeight;
    } catch(e) { console.error(e); }
}

async function sendChat() {
    if (!currentChatCar) { toast('Выберите водителя!', '#f44336'); return; }
    const input = document.getElementById('chatInput');
    const text  = input.value.trim();
    if (!text) return;
    input.value = '';
    try {
        const r = await fetch('/api/chat/send', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({car_number: currentChatCar, text, sender: 'admin'})
        });
        const d = await r.json();
        if (d.ok) await loadChatMessages();
    } catch(e) { console.error(e); }
}

// ==================== ФИНАНСЫ ====================
async function loadFinance() {
    try {
        const r1 = await fetch('/api/orders');
        const d1 = await r1.json();
        if (d1.ok) {
            const completed = d1.orders.filter(o => o.status === 'completed');
            const cancelled = d1.orders.filter(o => o.status === 'cancelled');
            const revenue   = completed.reduce((s, o) => s + o.price, 0);
            document.getElementById('finRevenue').textContent   = revenue.toLocaleString() + ' сум';
            document.getElementById('finCompleted').textContent = completed.length;
            document.getElementById('finCancelled').textContent = cancelled.length;
            document.getElementById('finTotal').textContent     = d1.orders.length;
        }
        const r2 = await fetch('/api/drivers');
        const d2 = await r2.json();
        if (d2.ok) {
            const tbody = document.getElementById('balanceTable');
            tbody.innerHTML = d2.drivers.map(dr => `
                <tr>
                    <td><b>${dr.car_number}</b></td>
                    <td>${dr.name}</td>
                    <td>${(dr.balance||0).toLocaleString()} сум</td>
                    <td>
                        <button class="btn btn-green" style="padding:5px 10px;font-size:11px;"
                            onclick="editBalance('${dr.car_number}',${dr.balance||0})">
                            💰 Изменить
                        </button>
                    </td>
                </tr>
            `).join('');
        }
    } catch(e) { console.error(e); }
}

// ==================== ДЕЙСТВИЯ ====================
async function loadDriversSelect() {
    try {
        const r = await fetch('/api/drivers');
        const d = await r.json();
        if (!d.ok) return;
        const sel = document.getElementById('orderCar');
        sel.innerHTML = '<option value="ALL">📢 Всем водителям</option>' +
            d.drivers.map(dr =>
                `<option value="${dr.car_number}">${dr.car_number} — ${dr.name}</option>`
            ).join('');
    } catch(e) { console.error(e); }
}

function setAddr(fieldId, addr) {
    document.getElementById(fieldId).value = addr;
}

function openOrder(car) {
    openModal('modalOrder');
    setTimeout(() => {
        const sel = document.getElementById('orderCar');
        for (let o of sel.options) {
            if (o.value === car) { o.selected = true; break; }
        }
    }, 300);
}

async function submitOrder() {
    const car    = document.getElementById('orderCar').value;
    const from   = document.getElementById('orderFrom').value.trim();
    const to     = document.getElementById('orderTo').value.trim();
    const price  = parseInt(document.getElementById('orderPrice').value);
    const client = document.getElementById('orderClient').value.trim() || 'Клиент';

    if (!from)          { toast('❌ Введите откуда!',          '#f44336'); return; }
    if (!to)            { toast('❌ Введите куда!',             '#f44336'); return; }
    if (!price || price <= 0) { toast('❌ Введите цену!',      '#f44336'); return; }

    const url  = car === 'ALL' ? '/api/orders/broadcast' : '/api/orders/create';
    const body = car === 'ALL'
        ? {from_address: from, to_address: to, price, client}
        : {car_number: car, from_address: from, to_address: to, price, client};

    try {
        const r = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        const d = await r.json();
        if (d.ok) {
            toast('✅ ' + d.message);
            closeModal('modalOrder');
            document.getElementById('orderFrom').value  = '';
            document.getElementById('orderTo').value    = '';
            document.getElementById('orderPrice').value = '';
            document.getElementById('orderClient').value = '';
        } else {
            toast('❌ ' + d.error, '#f44336');
        }
    } catch(e) { toast('❌ Ошибка сети', '#f44336'); }
}

async function submitDriver() {
    const car   = document.getElementById('driverCar').value.trim();
    const name  = document.getElementById('driverName').value.trim();
    const phone = document.getElementById('driverPhone').value.trim();
    const msg   = document.getElementById('driverMsg');

    if (!car || !name || !phone) {
        msg.style.color = '#f44336';
        msg.textContent = '❌ Заполните все поля!';
        return;
    }
    try {
        const r = await fetch('/api/drivers/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({car_number: car, name, phone})
        });
        const d = await r.json();
        if (d.ok) {
            msg.style.color = '#4CAF50';
            msg.textContent = `✅ Добавлен! ПИН: ${d.pin}`;
            document.getElementById('driverCar').value   = '';
            document.getElementById('driverName').value  = '';
            document.getElementById('driverPhone').value = '';
            loadAllDrivers();
            loadStats();
            setTimeout(() => closeModal('modalDriver'), 2000);
        } else {
            msg.style.color = '#f44336';
            msg.textContent = '❌ ' + d.error;
        }
    } catch(e) {
        msg.style.color = '#f44336';
        msg.textContent = '❌ Ошибка сети';
    }
}

function editBalance(car, current) {
    document.getElementById('balanceCar').value    = car;
    document.getElementById('balanceAmount').value = current;
    openModal('modalBalance');
}

async function submitBalance() {
    const car    = document.getElementById('balanceCar').value;
    const amount = parseInt(document.getElementById('balanceAmount').value);
    try {
        const r = await fetch('/api/drivers/balance', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({car_number: car, amount})
        });
        const d = await r.json();
        if (d.ok) {
            toast('✅ Баланс обновлён!');
            closeModal('modalBalance');
            loadAllDrivers();
            loadFinance();
        }
    } catch(e) { toast('❌ Ошибка', '#f44336'); }
}

async function deleteDriver(car) {
    if (!confirm(`Удалить водителя ${car}?`)) return;
    try {
        const r = await fetch('/api/drivers/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({car_number: car})
        });
        const d = await r.json();
        if (d.ok) { toast('✅ Удалён!'); loadAllDrivers(); loadStats(); }
    } catch(e) { toast('❌ Ошибка', '#f44336'); }
}

async function completeOrder(id) {
    try {
        const r = await fetch('/api/orders/complete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({order_id: id, stars: 5})
        });
        const d = await r.json();
        if (d.ok) { toast('✅ Заказ завершён!'); loadOrders(); }
    } catch(e) { toast('❌ Ошибка', '#f44336'); }
}

async function cancelOrder(id) {
    try {
        const r = await fetch('/api/orders/cancel', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({order_id: id})
        });
        const d = await r.json();
        if (d.ok) { toast('✅ Заказ отменён!'); loadOrders(); }
    } catch(e) { toast('❌ Ошибка', '#f44336'); }
}

async function saveTariffs() {
    toast('✅ Тарифы сохранены!');
}

function exportReport() {
    fetch('/api/orders')
    .then(r => r.json())
    .then(d => {
        if (!d.ok) return;
        let csv = 'ID,Авто,Откуда,Куда,Цена,Клиент,Статус\n';
        d.orders.forEach(o => {
            csv += `${o.id},${o.car_number},${o.from_address},${o.to_address},${o.price},${o.client},${o.status}\n`;
        });
        const blob = new Blob([csv], {type: 'text/csv;charset=utf-8;'});
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement('a');
        a.href     = url;
        a.download = 'taxi_report.csv';
        a.click();
        toast('✅ Отчёт скачан!');
    });
}

function resetShift() {
    if (!confirm('Сбросить смену? Все данные о выручке будут обнулены.')) return;
    toast('✅ Смена сброшена!');
}

// ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
function statusBadge(status) {
    const map = {
        'free':    '<span class="badge badge-green">🟢 Свободен</span>',
        'busy':    '<span class="badge badge-red">🔴 На заказе</span>',
        'offline': '<span class="badge badge-gray">⚫ Офлайн</span>'
    };
    return map[status] || '<span class="badge badge-gray">—</span>';
}

function orderBadge(status) {
    const map = {
        'pending':   '<span class="badge badge-yellow">⏳ Ожидает</span>',
        'completed': '<span class="badge badge-green">✅ Завершён</span>',
        'cancelled': '<span class="badge badge-red">❌ Отменён</span>'
    };
    return map[status] || status;
}

function stars(avg) {
    if (avg >= 4.8) return '⭐⭐⭐⭐⭐';
    if (avg >= 4.0) return '⭐⭐⭐⭐';
    if (avg >= 3.0) return '⭐⭐⭐';
    if (avg >= 2.0) return '⭐⭐';
    return '⭐';
}

// ==================== АВТООБНОВЛЕНИЕ ====================
setInterval(() => {
    loadStats();
    loadOnlineDrivers();
}, 5000);

loadStats();
loadOnlineDrivers();
</script>
</body>
</html>"""

# ==================== ЗАПУСК ====================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
