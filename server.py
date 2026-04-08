from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import random
import time
import threading
import requests
import os
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

# ==================== БАЗА ДАННЫХ ====================
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    try:
        conn = get_db()
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS driver_list (
            id TEXT PRIMARY KEY,
            name TEXT,
            phone TEXT,
            car_number TEXT,
            pin TEXT,
            balance INTEGER DEFAULT 50000,
            status TEXT DEFAULT 'offline'
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS pending_codes (
            pin_id TEXT PRIMARY KEY,
            name TEXT,
            phone TEXT,
            car_number TEXT,
            pin TEXT,
            status TEXT DEFAULT 'pending',
            created_at REAL
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            car_number TEXT,
            sender TEXT,
            text TEXT,
            time TEXT
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS balances (
            car_number TEXT PRIMARY KEY,
            balance INTEGER DEFAULT 50000
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS tariffs (
            id INTEGER PRIMARY KEY DEFAULT 1,
            base_fare INTEGER DEFAULT 5000,
            city_rate INTEGER DEFAULT 2800,
            suburb_rate INTEGER DEFAULT 3000,
            wait_rate INTEGER DEFAULT 500
        )''')

        c.execute('''INSERT INTO tariffs (id, base_fare, city_rate, suburb_rate, wait_rate)
                     VALUES (1, 5000, 2800, 3000, 500)
                     ON CONFLICT (id) DO NOTHING''')

        # ✅ Заявки на пополнение баланса
        c.execute('''CREATE TABLE IF NOT EXISTS balance_requests (
            id SERIAL PRIMARY KEY,
            car_number TEXT,
            amount INTEGER,
            status TEXT DEFAULT 'pending',
            created_at REAL
        )''')

        # ✅ Заказы
        c.execute('''CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            order_num INTEGER,
            car_number TEXT,
            from_address TEXT,
            to_address TEXT,
            distance TEXT,
            price INTEGER,
            client TEXT,
            status TEXT DEFAULT 'pending',
            created_at REAL
        )''')

        conn.commit()
        conn.close()
        print("✅ База данных инициализирована")
    except Exception as e:
        print(f"❌ Ошибка базы данных: {e}")

init_db()

# ==================== TELEGRAM BOT ====================
TG_TOKEN   = "8757251631:AAHMFD4cg1dU9SdZ8-7HMDxy5qDUpSc5TIs"
TG_CHAT_ID = "1053431273"
TG_API     = f"https://api.telegram.org/bot{TG_TOKEN}"

def tg_send(text, reply_markup=None):
    try:
        data = {
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_markup:
            import json
            data["reply_markup"] = json.dumps(reply_markup)
        requests.post(f"{TG_API}/sendMessage", data=data, timeout=5)
    except Exception as e:
        print(f"❌ Telegram error: {e}")

def tg_notify_new_pin(pin_id, name, car, phone, pin):
    text = (
        f"🔑 <b>Новая заявка на регистрацию</b>\n\n"
        f"👤 Имя: <b>{name}</b>\n"
        f"🚗 Авто: <b>{car}</b>\n"
        f"📱 Тел: <b>{phone}</b>\n\n"
        f"🔐 ПИН-код: <b>{pin}</b>\n\n"
        f"Нажмите кнопку:"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "✅ Одобрить", "callback_data": f"approve:{pin_id}"},
            {"text": "❌ Отказать", "callback_data": f"reject:{pin_id}"}
        ]]
    }
    tg_send(text, markup)

def tg_notify_balance_request(req_id, car, amount):
    text = (
        f"💰 <b>Заявка на пополнение баланса</b>\n\n"
        f"🚗 Авто: <b>{car}</b>\n"
        f"💵 Сумма: <b>{amount:,} сум</b>\n\n"
        f"Подтвердите пополнение:"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "✅ Одобрить", "callback_data": f"bal_approve:{req_id}"},
            {"text": "❌ Отказать", "callback_data": f"bal_reject:{req_id}"}
        ]]
    }
    tg_send(text, markup)

def tg_notify_approved(name, car, pin):
    tg_send(f"✅ <b>{name}</b> ({car}) одобрен!\nПИН: <b>{pin}</b>")

def tg_notify_rejected(name, car):
    tg_send(f"❌ Заявка <b>{name}</b> ({car}) отклонена")

def tg_answer_callback(callback_id, text):
    try:
        requests.post(f"{TG_API}/answerCallbackQuery", data={
            "callback_query_id": callback_id,
            "text": text
        }, timeout=5)
    except:
        pass

# ==================== ДАННЫЕ В ПАМЯТИ ====================
drivers        = {}
order_counter  = 1000
pending_orders = {}

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def get_tariffs_db():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM tariffs WHERE id = 1")
        row = c.fetchone()
        conn.close()
        if row:
            return dict(row)
        return {"base_fare": 5000, "city_rate": 2800, "suburb_rate": 3000, "wait_rate": 500}
    except:
        return {"base_fare": 5000, "city_rate": 2800, "suburb_rate": 3000, "wait_rate": 500}

def get_balance(car_number):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT balance FROM balances WHERE car_number = %s", (car_number,))
        row = c.fetchone()
        conn.close()
        return row["balance"] if row else 50000
    except:
        return 50000

def set_balance(car_number, balance):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''INSERT INTO balances (car_number, balance)
                     VALUES (%s, %s)
                     ON CONFLICT (car_number) DO UPDATE SET balance = %s''',
                  (car_number, balance, balance))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ set_balance error: {e}")

def get_pending_codes_db():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM pending_codes ORDER BY created_at DESC")
        rows = c.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except:
        return []

def get_driver_list_db():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM driver_list")
        rows = c.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except:
        return []

# ==================== TELEGRAM POLLING ====================
tg_offset = 0

def tg_polling():
    global tg_offset
    print("🤖 Telegram бот запущен")
    while True:
        try:
            resp = requests.get(f"{TG_API}/getUpdates", params={
                "offset": tg_offset,
                "timeout": 30
            }, timeout=35)
            updates = resp.json().get("result", [])

            for upd in updates:
                tg_offset = upd["update_id"] + 1

                if "callback_query" in upd:
                    cq    = upd["callback_query"]
                    cq_id = cq["id"]
                    data  = cq.get("data", "")

                    # ✅ Одобрить регистрацию
                    if data.startswith("approve:"):
                        pin_id = data.split(":", 1)[1]
                        try:
                            conn = get_db()
                            c = conn.cursor()
                            c.execute("SELECT * FROM pending_codes WHERE pin_id = %s", (pin_id,))
                            info = c.fetchone()
                            if info:
                                c.execute("UPDATE pending_codes SET status = 'approved' WHERE pin_id = %s", (pin_id,))
                                c.execute('''INSERT INTO driver_list (id, name, phone, car_number, pin, balance, status)
                                             VALUES (%s, %s, %s, %s, %s, 50000, 'offline')
                                             ON CONFLICT (id) DO NOTHING''',
                                          (info["car_number"], info["name"], info["phone"],
                                           info["car_number"], info["pin"]))
                                conn.commit()
                                conn.close()
                                set_balance(info["car_number"], 50000)
                                tg_answer_callback(cq_id, "✅ Одобрено!")
                                tg_notify_approved(info["name"], info["car_number"], info["pin"])
                            else:
                                conn.close()
                                tg_answer_callback(cq_id, "Заявка не найдена")
                        except Exception as e:
                            print(f"approve error: {e}")

                    # ✅ Отклонить регистрацию
                    elif data.startswith("reject:"):
                        pin_id = data.split(":", 1)[1]
                        try:
                            conn = get_db()
                            c = conn.cursor()
                            c.execute("SELECT * FROM pending_codes WHERE pin_id = %s", (pin_id,))
                            info = c.fetchone()
                            if info:
                                c.execute("UPDATE pending_codes SET status = 'rejected' WHERE pin_id = %s", (pin_id,))
                                conn.commit()
                                tg_answer_callback(cq_id, "❌ Отклонено")
                                tg_notify_rejected(info["name"], info["car_number"])
                            conn.close()
                        except Exception as e:
                            print(f"reject error: {e}")

                    # ✅ Одобрить пополнение баланса
                    elif data.startswith("bal_approve:"):
                        req_id = int(data.split(":", 1)[1])
                        try:
                            conn = get_db()
                            c = conn.cursor()
                            c.execute("SELECT * FROM balance_requests WHERE id = %s", (req_id,))
                            req = c.fetchone()
                            if req and req["status"] == "pending":
                                car    = req["car_number"]
                                amount = req["amount"]
                                old_b  = get_balance(car)
                                new_b  = old_b + amount
                                set_balance(car, new_b)
                                if car in drivers:
                                    drivers[car]["balance"] = new_b
                                c.execute("UPDATE balance_requests SET status = 'approved' WHERE id = %s", (req_id,))
                                conn.commit()
                                tg_answer_callback(cq_id, "✅ Баланс пополнен!")
                                tg_send(f"✅ Баланс <b>{car}</b> пополнен на {amount:,} сум\nНовый баланс: {new_b:,} сум")
                            conn.close()
                        except Exception as e:
                            print(f"bal_approve error: {e}")

                    # ✅ Отклонить пополнение баланса
                    elif data.startswith("bal_reject:"):
                        req_id = int(data.split(":", 1)[1])
                        try:
                            conn = get_db()
                            c = conn.cursor()
                            c.execute("UPDATE balance_requests SET status = 'rejected' WHERE id = %s", (req_id,))
                            conn.commit()
                            conn.close()
                            tg_answer_callback(cq_id, "❌ Отклонено")
                            tg_send(f"❌ Заявка на пополнение баланса отклонена")
                        except Exception as e:
                            print(f"bal_reject error: {e}")

                elif "message" in upd:
                    msg  = upd["message"]
                    text = msg.get("text", "")

                    if text == "/start":
                        tg_send(
                            "🚕 <b>TAXI 1229 Samarkand</b>\n\n"
                            "Доступные команды:\n"
                            "/status — статус системы\n"
                            "/pending — заявки на ПИН\n"
                            "/drivers — все водители\n"
                            "/order НОМЕР Откуда;Куда;Цена\n\n"
                            "Пример:\n"
                            "<code>/order 90T785OA Регистон;Аэропорт;28500</code>"
                        )

                    elif text == "/status":
                        online = sum(1 for d in drivers.values() if d.get("status") == "free")
                        busy   = sum(1 for d in drivers.values() if d.get("status") == "busy")
                        total  = len(drivers)
                        codes  = get_pending_codes_db()
                        pend   = sum(1 for p in codes if p.get("status") == "pending")
                        tg_send(
                            f"📊 <b>Статус системы</b>\n\n"
                            f"🟢 Свободны: {online}\n"
                            f"🔴 На заказе: {busy}\n"
                            f"📍 Всего онлайн: {total}\n"
                            f"⏳ Ждут ПИН: {pend}"
                        )

                    elif text == "/drivers":
                        if not drivers:
                            tg_send("Нет водителей онлайн")
                        else:
                            lines = []
                            for d in drivers.values():
                                status = "🟢" if d.get("status") == "free" else "🔴"
                                lines.append(f"{status} {d['car_number']} — {d.get('driver_name','—')}")
                            tg_send("🚗 <b>Водители онлайн:</b>\n" + "\n".join(lines))

                    elif text.startswith("/order "):
                        try:
                            parts     = text.split(" ", 2)
                            car       = parts[1].strip()
                            info      = parts[2].split(";")
                            from_addr = info[0].strip()
                            to_addr   = info[1].strip()
                            price     = int(info[2].strip())
                            # Создаём заказ
                            create_order_internal(car, from_addr, to_addr, price, "Telegram")
                            tg_send(f"✅ Заказ создан для {car}\n{from_addr} → {to_addr}\n💰 {price:,} сум")
                        except Exception as e:
                            tg_send(f"❌ Ошибка: {e}\nФормат: /order НОМЕР Откуда;Куда;Цена")

        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)

threading.Thread(target=tg_polling, daemon=True).start()

# ==================== СОЗДАНИЕ ЗАКАЗА ====================
def create_order_internal(car_number, from_addr, to_addr, price, client="Админ", distance="—"):
    global order_counter
    order_counter += 1
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''INSERT INTO orders (order_num, car_number, from_address, to_address, distance, price, client, status, created_at)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s) RETURNING id''',
                  (order_counter, car_number, from_addr, to_addr, distance, price, client, time.time()))
        order_id = c.fetchone()["id"]
        conn.commit()
        conn.close()
        pending_orders[car_number] = order_id
        return order_id
    except Exception as e:
        print(f"create_order error: {e}")
        return None

# ==================== АДМИН HTML ====================
ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Taxi 1229 Admin</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #111111; color: #eee; min-height: 100vh; }
        .header { background: #000; padding: 20px; text-align: center; border-bottom: 2px solid #FFD600; }
        .header h1 { color: #FFD600; font-size: 26px; }
        .header p { color: #555; margin-top: 5px; font-size: 13px; }
        .tg-badge { display:inline-block; background:#0088cc; color:#fff; padding:4px 12px; border-radius:20px; font-size:12px; margin-top:8px; }
        .stats { display: flex; justify-content: center; gap: 16px; padding: 20px; flex-wrap: wrap; }
        .stat-card { background: #1a1a1a; border-radius: 12px; padding: 18px 26px; text-align: center; min-width: 130px; border: 1px solid #222; }
        .stat-card .number { font-size: 32px; font-weight: bold; color: #FFD600; }
        .stat-card .label { color: #555; margin-top: 4px; font-size: 12px; }
        .section { max-width: 1100px; margin: 16px auto; padding: 0 20px; }
        .section h2 { color: #FFD600; margin-bottom: 12px; border-bottom: 1px solid #222; padding-bottom: 8px; font-size: 15px; }
        table { width: 100%; border-collapse: collapse; background: #1a1a1a; border-radius: 10px; overflow: hidden; margin-bottom: 16px; }
        th { background: #000; padding: 10px 14px; text-align: left; color: #FFD600; font-size: 12px; }
        td { padding: 10px 14px; border-bottom: 1px solid #111; font-size: 13px; }
        tr:hover { background: #1f1f1f; }
        .pin-code { font-size: 22px; font-weight: bold; color: #FFD600; font-family: monospace; letter-spacing: 4px; }
        .btn { padding: 6px 14px; border: none; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: bold; margin: 2px; }
        .btn-approve { background: #FFD600; color: #000; }
        .btn-reject { background: #1a1a1a; color: #666; border: 1px solid #333; }
        .btn-danger { background: #2a0000; color: #FF5252; border: 1px solid #4a0000; }
        .btn-order { background: #003366; color: #fff; border: 1px solid #0055aa; }
        .status-pending { color: #FF9800; font-weight: bold; }
        .status-approved { color: #4CAF50; font-weight: bold; }
        .no-data { text-align: center; padding: 30px; color: #444; }
        .refresh-bar { text-align: center; padding: 10px; color: #333; font-size: 12px; }
        .order-form { background: #1a1a1a; padding: 20px; border-radius: 10px; margin-bottom: 16px; }
        .order-form input, .order-form select {
            background: #111; color: #fff; border: 1px solid #333;
            padding: 8px 12px; border-radius: 6px; margin: 4px; width: 200px; font-size: 13px;
        }
        .order-form button { padding: 8px 20px; background: #FFD600; color: #000; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; margin: 4px; }
        .tariff-box { display: flex; gap: 20px; flex-wrap: wrap; }
        .tariff-item { background: #1a1a1a; padding: 16px; border-radius: 10px; text-align: center; min-width: 150px; }
        .tariff-item .val { font-size: 24px; font-weight: bold; color: #FFD600; }
        .tariff-item .name { color: #555; font-size: 12px; margin-top: 4px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🚕 TAXI 1229 SAMARKAND</h1>
        <p>{{ current_time }}</p>
        <div class="tg-badge">🤖 Telegram бот активен</div>
    </div>

    <div class="stats">
        <div class="stat-card"><div class="number">{{ total_drivers }}</div><div class="label">На линии</div></div>
        <div class="stat-card"><div class="number">{{ free_drivers }}</div><div class="label">Свободны</div></div>
        <div class="stat-card"><div class="number">{{ busy_drivers }}</div><div class="label">На заказе</div></div>
        <div class="stat-card"><div class="number">{{ pending_count }}</div><div class="label">Ждут ПИН</div></div>
        <div class="stat-card"><div class="number">{{ registered_count }}</div><div class="label">Водителей</div></div>
    </div>

    <!-- Создать заказ -->
    <div class="section">
        <h2>📦 Создать заказ</h2>
        <div class="order-form">
            <select id="orderCar">
                <option value="">-- Выбрать водителя --</option>
                <option value="ALL">📢 Всем водителям</option>
                {% for did, d in drivers_list %}
                <option value="{{ d.car_number }}">{{ d.car_number }} — {{ d.driver_name or '—' }}</option>
                {% endfor %}
            </select>
            <input type="text" id="orderFrom" placeholder="Откуда">
            <input type="text" id="orderTo" placeholder="Куда">
            <input type="number" id="orderPrice" placeholder="Цена (сум)">
            <input type="text" id="orderClient" placeholder="Клиент">
            <button onclick="createOrder()">🚀 Отправить заказ</button>
        </div>
    </div>

    <!-- Тарифы -->
    <div class="section">
        <h2>💰 Тарифы</h2>
        <div class="tariff-box">
            <div class="tariff-item">
                <div class="val">{{ tariffs.base_fare }}</div>
                <div class="name">Посадка (сум)</div>
            </div>
            <div class="tariff-item">
                <div class="val">{{ tariffs.city_rate }}</div>
                <div class="name">Город (сум/км)</div>
            </div>
            <div class="tariff-item">
                <div class="val">{{ tariffs.suburb_rate }}</div>
                <div class="name">Загород (сум/км)</div>
            </div>
            <div class="tariff-item">
                <div class="val">{{ tariffs.wait_rate }}</div>
                <div class="name">Ожидание (сум/мин)</div>
            </div>
        </div>
        <div style="margin-top:12px;">
            <input type="number" id="tBase" placeholder="Посадка" style="background:#111;color:#fff;border:1px solid #333;padding:6px;border-radius:6px;margin:4px;width:120px;">
            <input type="number" id="tCity" placeholder="Город" style="background:#111;color:#fff;border:1px solid #333;padding:6px;border-radius:6px;margin:4px;width:120px;">
            <input type="number" id="tSuburb" placeholder="Загород" style="background:#111;color:#fff;border:1px solid #333;padding:6px;border-radius:6px;margin:4px;width:120px;">
            <input type="number" id="tWait" placeholder="Ожидание" style="background:#111;color:#fff;border:1px solid #333;padding:6px;border-radius:6px;margin:4px;width:120px;">
            <button onclick="saveTariffs()" style="padding:6px 16px;background:#FFD600;color:#000;border:none;border-radius:6px;font-weight:bold;cursor:pointer;">Сохранить тарифы</button>
        </div>
    </div>

    {% if pending_list %}
    <div class="section">
        <h2>🔑 Заявки на регистрацию</h2>
        <table>
            <tr><th>Имя</th><th>��елефон</th><th>Авто</th><th>ПИН-код</th><th>Статус</th><th>Действия</th></tr>
            {% for p in pending_list %}
            <tr>
                <td>{{ p.name }}</td>
                <td>{{ p.phone }}</td>
                <td><b style="color:#FFD600">{{ p.car_number }}</b></td>
                <td><span class="pin-code">{{ p.pin }}</span></td>
                <td>
                    {% if p.status == 'pending' %}<span class="status-pending">⏳ Ожидает</span>
                    {% elif p.status == 'approved' %}<span class="status-approved">✅ Одобрен</span>
                    {% else %}<span style="color:#555">{{ p.status }}</span>{% endif %}
                </td>
                <td>
                    {% if p.status == 'pending' %}
                        <button class="btn btn-approve" onclick="approveCode('{{ p.pin_id }}')">✅ Одобрить</button>
                        <button class="btn btn-reject" onclick="rejectCode('{{ p.pin_id }}')">❌ Отказ</button>
                    {% else %}<span style="color:#333">—</span>{% endif %}
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}

    <!-- Заявки на пополнение баланса -->
    {% if balance_requests %}
    <div class="section">
        <h2>💰 Заявки на пополнение баланса</h2>
        <table>
            <tr><th>Авто</th><th>Сумма</th><th>Статус</th><th>Действия</th></tr>
            {% for r in balance_requests %}
            <tr>
                <td><b style="color:#FFD600">{{ r.car_number }}</b></td>
                <td style="color:#4CAF50">{{ "{:,}".format(r.amount) }} сум</td>
                <td>
                    {% if r.status == 'pending' %}<span class="status-pending">⏳ Ожидает</span>
                    {% elif r.status == 'approved' %}<span class="status-approved">✅ Одобрено</span>
                    {% else %}<span style="color:#FF5252">❌ Отклонено</span>{% endif %}
                </td>
                <td>
                    {% if r.status == 'pending' %}
                        <button class="btn btn-approve" onclick="approveBalance({{ r.id }})">✅ Одобрить</button>
                        <button class="btn btn-reject" onclick="rejectBalance({{ r.id }})">❌ Отказ</button>
                    {% else %}—{% endif %}
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}

    <div class="section">
        <h2>🚗 Активные водители</h2>
        {% if drivers_list %}
        <table>
            <tr><th>Авто</th><th>Водитель</th><th>Телефон</th><th>Статус</th><th>Скорость</th><th>Баланс</th><th>Обновлён</th><th>Действия</th></tr>
            {% for did, d in drivers_list %}
            <tr>
                <td><b style="color:#FFD600">{{ d.car_number }}</b></td>
                <td>{{ d.driver_name or '—' }}</td>
                <td>{{ d.phone or '—' }}</td>
                <td>
                    {% if d.status == 'free' %}<span style="color:#4CAF50">🟢 Свободен</span>
                    {% elif d.status == 'busy' %}<span style="color:#FF5252">🔴 На заказе</span>
                    {% else %}<span style="color:#555">⚫ {{ d.status }}</span>{% endif %}
                </td>
                <td>{{ d.speed }} км/ч</td>
                <td style="color:#4CAF50">{{ "{:,}".format(d.balance|int) }} сум</td>
                <td style="color:#444">{{ d.time_str }}</td>
                <td>
                    <button class="btn btn-order" onclick="quickOrder('{{ d.car_number }}')">📦 Заказ</button>
                    <button class="btn btn-danger" onclick="removeDriver('{{ did }}')">🗑 Удалить</button>
                </td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <div class="no-data">Нет активных водителей</div>
        {% endif %}
    </div>

    {% if all_drivers_list %}
    <div class="section">
        <h2>📋 Все зарегистрированные водители</h2>
        <table>
            <tr><th>Имя</th><th>��елефон</th><th>Авто</th><th>ПИН</th><th>Баланс</th><th>Действия</th></tr>
            {% for d in all_drivers_list %}
            <tr>
                <td>{{ d.name }}</td>
                <td>{{ d.phone }}</td>
                <td><b style="color:#FFD600">{{ d.car_number }}</b></td>
                <td><span class="pin-code">{{ d.pin }}</span></td>
                <td style="color:#4CAF50">{{ "{:,}".format(d.balance|int) }} сум</td>
                <td>
                    <button class="btn btn-approve" onclick="addBalance('{{ d.car_number }}')">💰 +Баланс</button>
                    <button class="btn btn-danger" onclick="deleteDriver('{{ d.car_number }}')">🗑 Удалить</button>
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}

    <div class="refresh-bar">Авто-обновление каждые 3 секунды</div>

    <script>
        setTimeout(() => location.reload(), 3000);

        function approveCode(pinId) {
            if (!confirm('Одобрить заявку?')) return;
            fetch('/api/admin/approve_code', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({pin_id: pinId})
            }).then(r => r.json()).then(d => {
                alert(d.success ? '✅ Одобрено!' : '❌ Ошибка');
                location.reload();
            });
        }

        function rejectCode(pinId) {
            if (!confirm('Отклонить заявку?')) return;
            fetch('/api/admin/reject_code', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({pin_id: pinId})
            }).then(() => location.reload());
        }

        function removeDriver(did) {
            if (!confirm('Удалить водителя из онлайн?')) return;
            fetch('/remove_driver', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({driver: did})
            }).then(() => location.reload());
        }

        function deleteDriver(car) {
            if (!confirm('Полностью удалить водителя ' + car + '?')) return;
            fetch('/api/admin/delete_driver', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({car_number: car})
            }).then(() => location.reload());
        }

        function addBalance(car) {
            const amount = prompt('Введите сумму пополнения для ' + car + ':');
            if (!amount || isNaN(amount)) return;
            fetch('/api/admin/add_balance', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({car_number: car, amount: parseInt(amount)})
            }).then(r => r.json()).then(d => {
                alert(d.success ? '✅ Баланс пополнен!' : '❌ Ошибка');
                location.reload();
            });
        }

        function approveBalance(id) {
            if (!confirm('Одобрить пополнение баланса?')) return;
            fetch('/api/admin/approve_balance', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id})
            }).then(() => location.reload());
        }

        function rejectBalance(id) {
            if (!confirm('Отклонить пополнение баланса?')) return;
            fetch('/api/admin/reject_balance', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id})
            }).then(() => location.reload());
        }

        function createOrder() {
            const car    = document.getElementById('orderCar').value;
            const from   = document.getElementById('orderFrom').value;
            const to     = document.getElementById('orderTo').value;
            const price  = document.getElementById('orderPrice').value;
            const client = document.getElementById('orderClient').value || 'Админ';
            if (!from || !to || !price) { alert('Заполните все поля!'); return; }

            const url = car === 'ALL' ? '/api/orders/broadcast' : '/api/orders/create';
            fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    car_number: car, from_address: from,
                    to_address: to, price: parseInt(price), client: client
                })
            }).then(r => r.json()).then(d => {
                alert(d.success ? '✅ Заказ отправлен!' : '❌ Ошибка');
                location.reload();
            });
        }

        function quickOrder(car) {
            document.getElementById('orderCar').value = car;
            document.getElementById('orderFrom').focus();
        }

        function saveTariffs() {
            const base   = document.getElementById('tBase').value;
            const city   = document.getElementById('tCity').value;
            const suburb = document.getElementById('tSuburb').value;
            const wait   = document.getElementById('tWait').value;
            if (!base || !city || !suburb || !wait) { alert('Заполните все тарифы!'); return; }
            fetch('/api/tariffs', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    base_fare: parseInt(base), city_rate: parseInt(city),
                    suburb_rate: parseInt(suburb), wait_rate: parseInt(wait)
                })
            }).then(() => { alert('✅ Тарифы сохранены!'); location.reload(); });
        }
    </script>
</body>
</html>
"""

# ==================== КАРТА ====================
@app.route('/map')
def map_page():
    api_key  = "AIzaSyDbbgIqjyOqzS7gozVqmZ_V4G1T6cpKXC0"
    map_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Карта водителей</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ background:#111; }}
        #map {{ width:100vw; height:100vh; }}
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        let map, markers = {{}}, infoWindows = {{}};

        function initMap() {{
            map = new google.maps.Map(document.getElementById("map"), {{
                center: {{ lat: 39.6542, lng: 66.9597 }},
                zoom: 13,
                styles: [
                    {{ elementType: "geometry", stylers: [{{ color: "#1a1a2e" }}] }},
                    {{ featureType: "road", elementType: "geometry", stylers: [{{ color: "#304a7d" }}] }},
                    {{ featureType: "water", elementType: "geometry", stylers: [{{ color: "#0e1626" }}] }}
                ]
            }});
            updateDrivers();
            setInterval(updateDrivers, 2000);
        }}

        function updateDrivers() {{
            fetch("/api/drivers")
                .then(r => r.json())
                .then(data => {{
                    Object.keys(markers).forEach(key => {{
                        if (!data[key]) {{ markers[key].setMap(null); delete markers[key]; }}
                    }});
                    for (let key in data) {{
                        let d   = data[key];
                        let pos = {{ lat: parseFloat(d.lat), lng: parseFloat(d.lng) }};
                        if (markers[key]) {{
                            markers[key].setPosition(pos);
                        }} else {{
                            markers[key] = new google.maps.Marker({{
                                position: pos, map: map, title: d.car_number,
                                icon: {{
                                    url: d.status === "free"
                                        ? "https://maps.google.com/mapfiles/ms/icons/green-dot.png"
                                        : "https://maps.google.com/mapfiles/ms/icons/red-dot.png",
                                    scaledSize: new google.maps.Size(40, 40)
                                }},
                                label: {{ text: d.car_number, color: "#FFD600", fontSize: "11px", fontWeight: "bold" }}
                            }});
                            markers[key].addListener("click", () => {{
                                Object.values(infoWindows).forEach(w => w.close());
                                infoWindows[key] = new google.maps.InfoWindow({{
                                    content: `<div style="background:#1a1a1a;color:#fff;padding:12px;border-radius:10px;min-width:200px;">
                                        <b style="color:#FFD600;">🚗 ${{d.car_number}}</b><br>
                                        👤 ${{d.driver_name || '—'}}<br>
                                        📱 ${{d.phone || '—'}}<br>
                                        📍 <b style="color:${{d.status==='free'?'#4CAF50':'#FF5252'}}">
                                            ${{d.status==='free'?'Свободен':'На заказе'}}</b><br>
                                        ⚡ ${{d.speed}} км/ч<br>
                                        💰 ${{parseInt(d.balance||0).toLocaleString()}} сум
                                    </div>`
                                }});
                                infoWindows[key].open(map, markers[key]);
                            }});
                        }}
                    }}
                }});
        }}
    </script>
    <script async defer src="https://maps.googleapis.com/maps/api/js?key={api_key}&callback=initMap"></script>
</body>
</html>
"""
    return map_html

# ==================== ЭНДПОИНТЫ ====================

@app.route('/')
def index():
    free          = sum(1 for d in drivers.values() if d.get('status') == 'free')
    busy          = sum(1 for d in drivers.values() if d.get('status') == 'busy')
    codes         = get_pending_codes_db()
    pending_count = sum(1 for p in codes if p.get('status') == 'pending')
    all_drivers   = get_driver_list_db()
    tariffs       = get_tariffs_db()

    # Заявки на пополнение баланса
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM balance_requests ORDER BY created_at DESC LIMIT 20")
        balance_requests = [dict(r) for r in c.fetchall()]
        conn.close()
    except:
        balance_requests = []

    pending_list     = sorted(codes, key=lambda x: 0 if x['status'] == 'pending' else 1)
    all_drivers_list = [{
        "name":       d["name"],
        "phone":      d["phone"],
        "car_number": d["car_number"],
        "pin":        d["pin"],
        "balance":    get_balance(d["car_number"])
    } for d in all_drivers]

    return render_template_string(
        ADMIN_HTML,
        current_time     = datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        total_drivers    = len(drivers),
        free_drivers     = free,
        busy_drivers     = busy,
        pending_count    = pending_count,
        registered_count = len(all_drivers),
        drivers_list     = list(drivers.items()),
        pending_list     = pending_list,
        all_drivers_list = all_drivers_list,
        balance_requests = balance_requests,
        tariffs          = tariffs
    )


@app.route('/api/driver/register', methods=['POST'])
def register_driver():
    data       = request.json
    phone      = data.get('phone', '')
    car_number = data.get('car_number', '')
    name       = data.get('name', 'Новый водитель')
    pin        = str(random.randint(1000, 9999))
    pin_id     = f"pin_{int(time.time())}_{random.randint(100,999)}"
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''INSERT INTO pending_codes (pin_id, name, phone, car_number, pin, status, created_at)
                     VALUES (%s, %s, %s, %s, %s, 'pending', %s)''',
                  (pin_id, name, phone, car_number, pin, time.time()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"register error: {e}")
    tg_notify_new_pin(pin_id, name, car_number, phone, pin)
    return jsonify({"success": True, "message": "Заявка отправлена администратору"})


@app.route('/api/admin/pending_codes', methods=['GET'])
def get_pending_codes():
    return jsonify(get_pending_codes_db())


@app.route('/api/admin/pending_by_car', methods=['GET'])
def pending_by_car():
    codes  = get_pending_codes_db()
    result = {}
    for info in codes:
        if info.get('status') == 'pending':
            car = info.get('car_number', '')
            pin = info.get('pin', '')
            if car and pin:
                result[car] = pin
    return jsonify(result)


@app.route('/api/admin/approve_code', methods=['POST'])
def approve_code():
    data   = request.json
    pin_id = data.get('pin_id', '')
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM pending_codes WHERE pin_id = %s", (pin_id,))
        info = c.fetchone()
        if not info:
            conn.close()
            return jsonify({"success": False, "error": "Заявка не найдена"}), 404
        c.execute("UPDATE pending_codes SET status = 'approved' WHERE pin_id = %s", (pin_id,))
        c.execute('''INSERT INTO driver_list (id, name, phone, car_number, pin, balance, status)
                     VALUES (%s, %s, %s, %s, %s, 50000, 'offline')
                     ON CONFLICT (id) DO NOTHING''',
                  (info["car_number"], info["name"], info["phone"], info["car_number"], info["pin"]))
        conn.commit()
        conn.close()
        set_balance(info["car_number"], 50000)
        tg_notify_approved(info["name"], info["car_number"], info["pin"])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/admin/reject_code', methods=['POST'])
def reject_code():
    data   = request.json
    pin_id = data.get('pin_id', '')
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM pending_codes WHERE pin_id = %s", (pin_id,))
        info = c.fetchone()
        if info:
            c.execute("UPDATE pending_codes SET status = 'rejected' WHERE pin_id = %s", (pin_id,))
            conn.commit()
            tg_notify_rejected(info["name"], info["car_number"])
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/admin/delete_driver', methods=['POST'])
def delete_driver():
    data = request.json
    car  = data.get('car_number', '')
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM driver_list WHERE car_number = %s", (car,))
        c.execute("DELETE FROM balances WHERE car_number = %s", (car,))
        conn.commit()
        conn.close()
        if car in drivers:
            del drivers[car]
        tg_send(f"🗑 Водитель {car} удалён из системы")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ✅ Пополнение баланса через админа
@app.route('/api/admin/add_balance', methods=['POST'])
def admin_add_balance():
    data   = request.json
    car    = data.get('car_number', '')
    amount = int(data.get('amount', 0))
    if amount <= 0:
        return jsonify({"success": False, "error": "Неверная сумма"})
    old_b = get_balance(car)
    new_b = old_b + amount
    set_balance(car, new_b)
    if car in drivers:
        drivers[car]['balance'] = new_b
    tg_send(f"💰 Баланс {car} пополнен на {amount:,} сум\nНовый баланс: {new_b:,} сум")
    return jsonify({"success": True, "new_balance": new_b})


# ✅ Водитель запрашивает пополнение баланса
@app.route('/api/balance/request', methods=['POST'])
def request_balance():
    data   = request.json
    car    = data.get('car_number', '')
    amount = int(data.get('amount', 0))
    if amount <= 0:
        return jsonify({"success": False, "error": "Неверная сумма"})
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''INSERT INTO balance_requests (car_number, amount, status, created_at)
                     VALUES (%s, %s, 'pending', %s) RETURNING id''',
                  (car, amount, time.time()))
        req_id = c.fetchone()["id"]
        conn.commit()
        conn.close()
        tg_notify_balance_request(req_id, car, amount)
        return jsonify({"success": True, "message": "Заявка отправлена администратору"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/admin/approve_balance', methods=['POST'])
def approve_balance():
    data   = request.json
    req_id = int(data.get('id', 0))
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM balance_requests WHERE id = %s", (req_id,))
        req = c.fetchone()
        if req and req["status"] == "pending":
            car    = req["car_number"]
            amount = req["amount"]
            old_b  = get_balance(car)
            new_b  = old_b + amount
            set_balance(car, new_b)
            if car in drivers:
                drivers[car]["balance"] = new_b
            c.execute("UPDATE balance_requests SET status = 'approved' WHERE id = %s", (req_id,))
            conn.commit()
            tg_send(f"✅ Баланс {car} пополнен на {amount:,} сум\nНовый баланс: {new_b:,} сум")
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/admin/reject_balance', methods=['POST'])
def reject_balance():
    data   = request.json
    req_id = int(data.get('id', 0))
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE balance_requests SET status = 'rejected' WHERE id = %s", (req_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/driver/login', methods=['POST'])
def driver_login():
    data = request.json
    pin  = data.get('pin', '')
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM driver_list WHERE pin = %s", (pin,))
        driver = c.fetchone()
        conn.close()
        if driver:
            balance = get_balance(driver["car_number"])
            return jsonify({
                "success":   True,
                "driver_id": driver["id"],
                "name":      driver["name"],
                "balance":   balance
            })
    except Exception as e:
        print(f"login error: {e}")

    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM pending_codes WHERE pin = %s AND status = 'pending'", (pin,))
        info = c.fetchone()
        if info:
            c.execute("UPDATE pending_codes SET status = 'approved' WHERE pin_id = %s", (info["pin_id"],))
            c.execute('''INSERT INTO driver_list (id, name, phone, car_number, pin, balance, status)
                         VALUES (%s, %s, %s, %s, %s, 50000, 'offline')
                         ON CONFLICT (id) DO NOTHING''',
                      (info["car_number"], info["name"], info["phone"], info["car_number"], info["pin"]))
            conn.commit()
            conn.close()
            set_balance(info["car_number"], 50000)
            tg_notify_approved(info["name"], info["car_number"], info["pin"])
            return jsonify({
                "success":   True,
                "driver_id": info["car_number"],
                "name":      info["name"],
                "balance":   50000
            })
        conn.close()
    except Exception as e:
        print(f"login pending error: {e}")

    return jsonify({"success": False, "error": "Неверный ПИН"}), 401


@app.route('/api/driver/<driver_id>/balance', methods=['GET'])
def get_driver_balance(driver_id):
    balance = get_balance(driver_id)
    name    = ""
    if driver_id in drivers:
        name = drivers[driver_id].get("driver_name", "")
    return jsonify({"balance": balance, "name": name})


@app.route('/api/tariffs', methods=['GET'])
def get_tariffs():
    return jsonify(get_tariffs_db())


@app.route('/api/tariffs', methods=['POST'])
def update_tariffs():
    data = request.get_json(force=True)
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''UPDATE tariffs SET
                     base_fare = %s, city_rate = %s,
                     suburb_rate = %s, wait_rate = %s
                     WHERE id = 1''',
                  (int(data.get('base_fare', 5000)),
                   int(data.get('city_rate', 2800)),
                   int(data.get('suburb_rate', 3000)),
                   int(data.get('wait_rate', 500))))
        conn.commit()
        conn.close()
        tg_send(f"💰 Тарифы обновлены:\n"
                f"Посадка: {data.get('base_fare')} сум\n"
                f"Город: {data.get('city_rate')} сум/км\n"
                f"Загород: {data.get('suburb_rate')} сум/км\n"
                f"Ожидание: {data.get('wait_rate')} сум/мин")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)})


@app.route('/location', methods=['POST'])
def location():
    data      = request.get_json(force=True)
    did       = data.get('driver', data.get('car_number', 'unknown'))
    balance   = get_balance(did)
    drivers[did] = {
        'lat':         data.get('lat', 0),
        'lng':         data.get('lng', 0),
        'speed':       data.get('speed', 0),
        'status':      data.get('status', 'free'),
        'car_number':  data.get('car_number', did),
        'balance':     balance,
        'phone':       data.get('phone', ''),
        'driver_name': data.get('driver_name', ''),
        'time_str':    datetime.now().strftime('%H:%M:%S'),
        'timestamp':   time.time()
    }
    return jsonify({'status': 'ok', 'balance': balance})


@app.route('/api/drivers', methods=['GET'])
def get_drivers():
    return jsonify(drivers)


@app.route('/remove_driver', methods=['POST'])
def remove_driver():
    data = request.get_json(force=True)
    did  = data.get('driver', data.get('car_number', ''))
    if did in drivers:
        del drivers[did]
    return jsonify({'status': 'ok'})


@app.route('/api/balance', methods=['POST'])
def update_balance():
    data   = request.get_json(force=True)
    did    = data.get('driver', '')
    amount = int(data.get('amount', 0))
    old_b  = get_balance(did)
    new_b  = old_b + amount
    set_balance(did, new_b)
    if did in drivers:
        drivers[did]['balance'] = new_b
    if amount != 0:
        sign = "+" if amount > 0 else ""
        tg_send(f"💰 Баланс {did}: {sign}{amount:,} сум → {new_b:,} сум")
    return jsonify({'status': 'ok', 'new_balance': new_b})


@app.route('/admin/block_driver', methods=['POST'])
def block_driver():
    data = request.get_json(force=True)
    car  = data.get('car_number', '')
    if car in drivers:
        drivers[car]['status'] = 'blocked'
    tg_send(f"🚫 Водитель {car} заблокирован")
    return jsonify({'status': 'ok'})


# ✅ Создать заказ одному водителю
@app.route('/api/orders/create', methods=['POST'])
def create_order():
    data      = request.json
    car       = data.get('car_number', '')
    from_addr = data.get('from_address', '')
    to_addr   = data.get('to_address', '')
    price     = int(data.get('price', 0))
    client    = data.get('client', 'Клиент')
    distance  = data.get('distance', '—')
    order_id  = create_order_internal(car, from_addr, to_addr, price, client, distance)
    tg_send(f"📦 Заказ назначен водителю <b>{car}</b>\n"
            f"📍 {from_addr} → {to_addr}\n💰 {price:,} сум")
    return jsonify({"success": True, "order_id": order_id})


# ✅ Отправить заказ ВСЕМ водителям
@app.route('/api/orders/broadcast', methods=['POST'])
def broadcast_order():
    data      = request.json
    from_addr = data.get('from_address', '')
    to_addr   = data.get('to_address', '')
    price     = int(data.get('price', 0))
    client    = data.get('client', 'Клиент')

    if not drivers:
        return jsonify({"success": False, "error": "Нет водителей онлайн"})

    count = 0
    for car in list(drivers.keys()):
        if drivers[car].get('status') == 'free':
            create_order_internal(car, from_addr, to_addr, price, client)
            count += 1

    tg_send(f"📢 Заказ отправлен {count} водителям!\n"
            f"📍 {from_addr} → {to_addr}\n💰 {price:,} сум")
    return jsonify({"success": True, "sent_to": count})


@app.route('/api/orders/pending', methods=['GET'])
def get_pending_order():
    car = request.args.get('car', '')
    if car not in pending_orders:
        return jsonify({"has_order": False})
    order_id = pending_orders[car]
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
        order = c.fetchone()
        conn.close()
        if not order or order["status"] != "pending":
            del pending_orders[car]
            return jsonify({"has_order": False})
        return jsonify({"has_order": True, **dict(order)})
    except:
        return jsonify({"has_order": False})


@app.route('/api/orders/respond', methods=['POST'])
def respond_to_order():
    data         = request.json
    order_id     = data.get('order_id')
    car          = data.get('car_number', '')
    response_val = data.get('response', '')
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE orders SET status = %s WHERE id = %s", (response_val, order_id))
        conn.commit()
        conn.close()
        if car in pending_orders:
            del pending_orders[car]
        if response_val == "accepted":
            tg_send(f"✅ Водитель <b>{car}</b> принял заказ")
        else:
            tg_send(f"❌ Водитель <b>{car}</b> отклонил заказ")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/orders/list', methods=['GET'])
def list_orders():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 50")
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify(rows)
    except:
        return jsonify([])


@app.route('/api/chat/send', methods=['POST'])
def chat_send():
    data   = request.json
    car    = data.get('car_number', '')
    driver = data.get('driver', '')
    text   = data.get('text', '')
    t      = datetime.now().strftime('%H:%M')
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO chat_messages (car_number, sender, text, time) VALUES (%s, %s, %s, %s)",
                  (car, car, text, t))
        conn.commit()
        conn.close()
        tg_send(f"💬 <b>{driver}</b> ({car}):\n{text}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/chat/messages', methods=['GET'])
def chat_get():
    car = request.args.get('car', '')
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''SELECT * FROM chat_messages WHERE car_number = %s
                     ORDER BY id DESC LIMIT 50''', (car,))
        rows = c.fetchall()
        conn.close()
        result = []
        for r in reversed(rows):
            result.append({
                "id":         r["id"],
                "car_number": r["car_number"],
                "from":       r["sender"],
                "text":       r["text"],
                "time":       r["time"]
            })
        return jsonify(result)
    except:
        return jsonify([])


@app.route('/api/chat/dispatch', methods=['POST'])
def chat_dispatch():
    data = request.json
    car  = data.get('car_number', '')
    text = data.get('text', '')
    t    = datetime.now().strftime('%H:%M')
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO chat_messages (car_number, sender, text, time) VALUES (%s, %s, %s, %s)",
                  (car, 'dispatcher', text, t))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/ping')
def ping():
    drivers_db = get_driver_list_db()
    codes      = get_pending_codes_db()
    return jsonify({
        'status':             'alive',
        'drivers_online':     len(drivers),
        'drivers_registered': len(drivers_db),
        'pending_requests':   len([p for p in codes if p['status'] == 'pending'])
    })


if __name__ == '__main__':
    print("=" * 50)
    print("🚕 TAXI SERVER STARTED")
    print("📍 Карта: /map")
    print("🗄️  База данных: PostgreSQL")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False)
