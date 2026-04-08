from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import random
import time
import threading
import requests
import os
import json

app = Flask(__name__)

# ==================== ДАННЫЕ В ПАМЯТИ ====================
drivers          = {}
order_counter    = 1000
chat_messages    = {}
balance_data     = {}
pending_codes    = {}
driver_list      = {}
balance_requests = []
orders_db        = []
tariffs_data     = {
    "base_fare":   5000,
    "city_rate":   2800,
    "suburb_rate": 3000,
    "wait_rate":   500
}

# ==================== TELEGRAM BOT ====================
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

def tg_answer_callback(callback_id, text):
    try:
        requests.post(f"{TG_API}/answerCallbackQuery",
                      data={"callback_query_id": callback_id, "text": text}, timeout=5)
    except:
        pass

def tg_notify_new_pin(pin_id, name, car, phone, pin):
    text = (
        f"🔑 <b>Новая заявка на регистрацию</b>\n\n"
        f"👤 Имя: <b>{name}</b>\n"
        f"🚗 Авто: <b>{car}</b>\n"
        f"📱 Тел: <b>{phone}</b>\n\n"
        f"🔐 ПИН-код: <b>{pin}</b>"
    )
    markup = {"inline_keyboard": [[
        {"text": "✅ Одобрить", "callback_data": f"approve:{pin_id}"},
        {"text": "❌ Отказать", "callback_data": f"reject:{pin_id}"}
    ]]}
    tg_send(text, markup)

def tg_notify_balance_request(req_id, car, amount):
    text = (
        f"💰 <b>Заявка на пополнение баланса</b>\n\n"
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

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
def get_balance(car):
    return balance_data.get(car, 50000)

def set_balance(car, amount):
    balance_data[car] = amount

def get_tariffs_db():
    return tariffs_data

def get_pending_codes_db():
    return sorted(pending_codes.values(),
                  key=lambda x: 0 if x['status'] == 'pending' else 1)

def get_driver_list_db():
    return list(driver_list.values())

def create_order_internal(car_number, from_addr, to_addr, price, client="Админ", distance="—"):
    global order_counter
    order_counter += 1
    # Отменяем старые заказы
    for o in orders_db:
        if o["car_number"] == car_number and o["status"] == "pending":
            o["status"] = "cancelled"
    order = {
        "id":           order_counter,
        "order_num":    order_counter,
        "car_number":   car_number,
        "from_address": from_addr,
        "to_address":   to_addr,
        "distance":     distance,
        "price":        price,
        "client":       client,
        "status":       "pending",
        "created_at":   time.time()
    }
    orders_db.append(order)
    return order_counter

# ==================== TELEGRAM POLLING ====================
tg_offset = 0

def tg_polling():
    global tg_offset
    print("🤖 Telegram бот запущен")
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
                    data  = cq.get("data", "")

                    if data.startswith("approve:"):
                        pin_id = data.split(":", 1)[1]
                        info   = pending_codes.get(pin_id)
                        if info and info["status"] == "pending":
                            info["status"] = "approved"
                            car = info["car_number"]
                            driver_list[car] = info
                            set_balance(car, 50000)
                            tg_answer_callback(cq_id, "✅ Одобрено!")
                            tg_notify_approved(info["name"], car, info["pin"])
                        else:
                            tg_answer_callback(cq_id, "Заявка не найдена")

                    elif data.startswith("reject:"):
                        pin_id = data.split(":", 1)[1]
                        info   = pending_codes.get(pin_id)
                        if info:
                            info["status"] = "rejected"
                            tg_answer_callback(cq_id, "❌ Отклонено")
                            tg_notify_rejected(info["name"], info["car_number"])

                    elif data.startswith("bal_approve:"):
                        req_id = int(data.split(":", 1)[1])
                        for req in balance_requests:
                            if req["id"] == req_id and req["status"] == "pending":
                                car    = req["car_number"]
                                amount = req["amount"]
                                new_b  = get_balance(car) + amount
                                set_balance(car, new_b)
                                if car in drivers:
                                    drivers[car]["balance"] = new_b
                                req["status"] = "approved"
                                tg_answer_callback(cq_id, "✅ Баланс пополнен!")
                                tg_send(f"✅ Баланс <b>{car}</b> пополнен!\nНовый: {new_b:,} сум")
                                break

                    elif data.startswith("bal_reject:"):
                        req_id = int(data.split(":", 1)[1])
                        for req in balance_requests:
                            if req["id"] == req_id:
                                req["status"] = "rejected"
                                tg_answer_callback(cq_id, "❌ Отклонено")
                                break

                elif "message" in upd:
                    msg  = upd["message"]
                    text = msg.get("text", "")

                    if text == "/start":
                        tg_send(
                            "🚕 <b>TAXI 3042 Xazarasp</b>\n\n"
                            "Доступные команды:\n"
                            "/status — статус системы\n"
                            "/drivers — водители онлайн\n"
                            "/pending — заявки на ПИН\n"
                            "/order НОМЕР Откуда;Куда;Цена\n\n"
                            "Пример:\n"
                            "<code>/order 90T785OA Bozor;Aeroport;25000</code>"
                        )

                    elif text == "/status":
                        online = sum(1 for d in drivers.values() if d.get("status") == "free")
                        busy   = sum(1 for d in drivers.values() if d.get("status") == "busy")
                        pend   = sum(1 for p in pending_codes.values() if p.get("status") == "pending")
                        tg_send(
                            f"📊 <b>Статус системы</b>\n\n"
                            f"🟢 Свободны: {online}\n"
                            f"🔴 На заказе: {busy}\n"
                            f"📍 Сейчас онлайн: {len(drivers)}\n"
                            f"👥 Всего водителей: {len(driver_list)}\n"
                            f"⏳ Ждут ПИН: {pend}"
                        )

                    elif text == "/drivers":
                        if not drivers:
                            tg_send("Нет водителей онлайн")
                        else:
                            lines = []
                            for d in drivers.values():
                                icon = "🟢" if d.get("status") == "free" else "🔴"
                                lines.append(f"{icon} {d['car_number']} — {d.get('driver_name','—')}")
                            tg_send("🚗 <b>Водители онлайн:</b>\n" + "\n".join(lines))

                    elif text == "/pending":
                        plist = [p for p in pending_codes.values() if p.get("status") == "pending"]
                        if not plist:
                            tg_send("✅ Нет новых заявок")
                        else:
                            for p in plist:
                                tg_send(
                                    f"⏳ <b>Заявка</b>\n"
                                    f"👤 {p['name']}\n"
                                    f"🚗 {p['car_number']}\n"
                                    f"📱 {p['phone']}\n"
                                    f"🔐 ПИН: <b>{p['pin']}</b>"
                                )

                    elif text.startswith("/order "):
                        try:
                            parts     = text.split(" ", 2)
                            car       = parts[1].strip()
                            info      = parts[2].split(";")
                            from_addr = info[0].strip()
                            to_addr   = info[1].strip()
                            price     = int(info[2].strip())
                            create_order_internal(car, from_addr, to_addr, price, "Telegram")
                            tg_send(f"✅ Заказ создан для {car}\n{from_addr} → {to_addr}\n💰 {price:,} сум")
                        except Exception as e:
                            tg_send(f"❌ Ошибка: {e}\nФормат: /order НОМЕР Откуда;Куда;Цена")

        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)

threading.Thread(target=tg_polling, daemon=True).start()

# ==================== АДМИН HTML ====================
ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Taxi 3042 Admin</title>
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
        .btn-chat { background: #1a3a1a; color: #4CAF50; border: 1px solid #2a5a2a; }
        .status-pending { color: #FF9800; font-weight: bold; }
        .status-approved { color: #4CAF50; font-weight: bold; }
        .no-data { text-align: center; padding: 30px; color: #444; }
        .refresh-bar { text-align: center; padding: 10px; color: #333; font-size: 12px; }
        .order-form { background: #1a1a1a; padding: 20px; border-radius: 10px; margin-bottom: 16px; }
        .order-form input, .order-form select { background: #111; color: #fff; border: 1px solid #333; padding: 8px 12px; border-radius: 6px; margin: 4px; font-size: 13px; }
        .order-form button { padding: 8px 20px; background: #FFD600; color: #000; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; margin: 4px; }
        .tariff-box { display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 16px; }
        .tariff-item { background: #1a1a1a; padding: 16px; border-radius: 10px; text-align: center; min-width: 150px; }
        .tariff-item .val { font-size: 24px; font-weight: bold; color: #FFD600; }
        .tariff-item .lbl { color: #555; font-size: 12px; margin-top: 4px; }
        .chat-box { background: #0a0a0a; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
        .chat-msg { padding: 8px 12px; margin: 4px 0; border-radius: 8px; max-width: 70%; }
        .chat-msg.own { background: #FFD600; color: #000; margin-left: auto; }
        .chat-msg.other { background: #1a1a1a; color: #fff; }
        .chat-input { display: flex; gap: 8px; margin-top: 12px; }
        .chat-input input { flex: 1; background: #111; color: #fff; border: 1px solid #333; padding: 8px 12px; border-radius: 6px; }
        .chat-input button { padding: 8px 16px; background: #FFD600; color: #000; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🚕 TAXI 3042 XAZARASP</h1>
        <p>{{ current_time }}</p>
        <div class="tg-badge">🤖 Telegram бот активен</div>
    </div>

    <div class="stats">
        <div class="stat-card"><div class="number">{{ total_drivers }}</div><div class="label">На линии</div></div>
        <div class="stat-card"><div class="number">{{ free_drivers }}</div><div class="label">Свободны</div></div>
        <div class="stat-card"><div class="number">{{ busy_drivers }}</div><div class="label">На заказе</div></div>
        <div class="stat-card"><div class="number">{{ pending_count }}</div><div class="label">Ждут ПИН</div></div>
        <div class="stat-card"><div class="number">{{ registered_count }}</div><div class="label">Всего</div></div>
    </div>

    <div class="section">
        <h2>📦 Создать заказ</h2>
        <div class="order-form">
            <select id="orderCar" style="width:220px;">
                <option value="">-- Выбрать водителя --</option>
                <option value="ALL">📢 Всем свободным</option>
                {% for did, d in drivers_list %}
                <option value="{{ d.car_number }}">{{ d.car_number }} — {{ d.driver_name or '—' }}</option>
                {% endfor %}
            </select>
            <input type="text" id="orderFrom" placeholder="Откуда" style="width:180px;">
            <input type="text" id="orderTo" placeholder="Куда" style="width:180px;">
            <input type="number" id="orderPrice" placeholder="Цена (сум)" style="width:140px;">
            <input type="text" id="orderClient" placeholder="Клиент" style="width:140px;">
            <button onclick="createOrder()">🚀 Отправить</button>
        </div>
    </div>

    <div class="section">
        <h2>💰 Тарифы</h2>
        <div class="tariff-box">
            <div class="tariff-item"><div class="val">{{ tariffs.base_fare }}</div><div class="lbl">Посадка (сум)</div></div>
            <div class="tariff-item"><div class="val">{{ tariffs.city_rate }}</div><div class="lbl">Город (сум/км)</div></div>
            <div class="tariff-item"><div class="val">{{ tariffs.suburb_rate }}</div><div class="lbl">Загород (сум/км)</div></div>
            <div class="tariff-item"><div class="val">{{ tariffs.wait_rate }}</div><div class="lbl">Ожидание (сум/мин)</div></div>
        </div>
        <div>
            <input type="number" id="tBase" placeholder="Посадка" style="background:#111;color:#fff;border:1px solid #333;padding:6px;border-radius:6px;margin:4px;width:120px;">
            <input type="number" id="tCity" placeholder="Город" style="background:#111;color:#fff;border:1px solid #333;padding:6px;border-radius:6px;margin:4px;width:120px;">
            <input type="number" id="tSuburb" placeholder="Загород" style="background:#111;color:#fff;border:1px solid #333;padding:6px;border-radius:6px;margin:4px;width:120px;">
            <input type="number" id="tWait" placeholder="Ожидание" style="background:#111;color:#fff;border:1px solid #333;padding:6px;border-radius:6px;margin:4px;width:120px;">
            <button onclick="saveTariffs()" style="padding:6px 16px;background:#FFD600;color:#000;border:none;border-radius:6px;font-weight:bold;cursor:pointer;margin:4px;">💾 Сохранить</button>
        </div>
    </div>

    {% if pending_list %}
    <div class="section">
        <h2>🔑 Заявки на регистрацию</h2>
        <table>
            <tr><th>Имя</th><th>Телефон</th><th>Авто</th><th>ПИН-код</th><th>Статус</th><th>Действия</th></tr>
            {% for p in pending_list %}
            <tr>
                <td>{{ p.name }}</td>
                <td>{{ p.phone }}</td>
                <td><b style="color:#FFD600">{{ p.car_number }}</b></td>
                <td><span class="pin-code">{{ p.pin }}</span></td>
                <td>
                    {% if p.status == 'pending' %}<span class="status-pending">⏳ Ожидает</span>
                    {% elif p.status == 'approved' %}<span class="status-approved">✅ Одобрен</span>
                    {% else %}<span style="color:#555">❌</span>{% endif %}
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

    {% if balance_requests %}
    <div class="section">
        <h2>💰 Заявки на баланс</h2>
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
                    <button class="btn btn-order" onclick="quickOrder('{{ d.car_number }}')">📦</button>
                    <button class="btn btn-chat" onclick="openChat('{{ d.car_number }}')">💬</button>
                    <button class="btn btn-approve" onclick="addBalance('{{ d.car_number }}')">💰</button>
                    <button class="btn btn-danger" onclick="removeDriver('{{ did }}')">🗑</button>
                </td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <div class="no-data">Нет активных водителей</div>
        {% endif %}
    </div>

    <div class="section" id="chatSection" style="display:none;">
        <h2>💬 Чат: <span id="chatCarNumber"></span></h2>
        <div class="chat-box" id="chatMessages" style="height:300px;overflow-y:auto;"></div>
        <div class="chat-input">
            <input type="text" id="chatText" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sendChat()">
            <button onclick="sendChat()">Отправить</button>
        </div>
        <div style="margin-top:8px;">
            <button onclick="sendQuick('Принято ✅')" class="btn btn-approve">Принято</button>
            <button onclick="sendQuick('Подождите ⏳')" class="btn btn-reject">Подождите</button>
            <button onclick="sendQuick('Есть заказ 📦')" class="btn btn-order">Заказ</button>
            <button onclick="sendQuick('Вы свободны 🟢')" class="btn btn-chat">Свободны</button>
            <button onclick="closeChat()" class="btn btn-danger">Закрыть</button>
        </div>
    </div>

    {% if all_drivers_list %}
    <div class="section">
        <h2>📋 Все водители</h2>
        <table>
            <tr><th>Имя</th><th>Телефон</th><th>Авто</th><th>ПИН</th><th>Баланс</th><th>Действия</th></tr>
            {% for d in all_drivers_list %}
            <tr>
                <td>{{ d.name }}</td>
                <td>{{ d.phone }}</td>
                <td><b style="color:#FFD600">{{ d.car_number }}</b></td>
                <td><span class="pin-code">{{ d.pin }}</span></td>
                <td style="color:#4CAF50">{{ "{:,}".format(d.balance|int) }} сум</td>
                <td>
                    <button class="btn btn-approve" onclick="addBalance('{{ d.car_number }}')">💰</button>
                    <button class="btn btn-danger" onclick="deleteDriver('{{ d.car_number }}')">🗑</button>
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}

    <div class="refresh-bar">Авто-обновление каждые 3 секунды</div>

    <script>
        setTimeout(() => location.reload(), 3000);
        let currentCar = '';
        let chatLoaded = new Set();
        let chatInterval = null;

        function openChat(car) {
            currentCar = car;
            document.getElementById('chatSection').style.display = 'block';
            document.getElementById('chatCarNumber').textContent = car;
            document.getElementById('chatMessages').innerHTML = '';
            chatLoaded.clear();
            loadChat();
            if (chatInterval) clearInterval(chatInterval);
            chatInterval = setInterval(loadChat, 2000);
            document.getElementById('chatSection').scrollIntoView({behavior:'smooth'});
        }

        function closeChat() {
            document.getElementById('chatSection').style.display = 'none';
            currentCar = '';
            if (chatInterval) clearInterval(chatInterval);
        }

        function loadChat() {
            if (!currentCar) return;
            fetch('/api/chat/messages?car=' + currentCar)
                .then(r => r.json())
                .then(msgs => {
                    const box = document.getElementById('chatMessages');
                    msgs.forEach(m => {
                        if (chatLoaded.has(m.id)) return;
                        chatLoaded.add(m.id);
                        const div = document.createElement('div');
                        div.className = 'chat-msg ' + (m.from === 'dispatcher' ? 'own' : 'other');
                        div.innerHTML = `<b>${m.from === 'dispatcher' ? 'Диспетчер' : m.car_number}</b>: ${m.text} <small style="opacity:0.5">${m.time}</small>`;
                        box.appendChild(div);
                    });
                    box.scrollTop = box.scrollHeight;
                });
        }

        function sendChat() {
            const text = document.getElementById('chatText').value.trim();
            if (!text || !currentCar) return;
            document.getElementById('chatText').value = '';
            fetch('/api/chat/dispatch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({car_number: currentCar, text: text})
            }).then(() => loadChat());
        }

        function sendQuick(text) {
            if (!currentCar) { alert('Откройте чат!'); return; }
            fetch('/api/chat/dispatch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({car_number: currentCar, text: text})
            }).then(() => loadChat());
        }

        function createOrder() {
            const car    = document.getElementById('orderCar').value;
            const from   = document.getElementById('orderFrom').value.trim();
            const to     = document.getElementById('orderTo').value.trim();
            const price  = document.getElementById('orderPrice').value;
            const client = document.getElementById('orderClient').value || 'Клиент';
            if (!from || !to || !price) { alert('Заполните все поля!'); return; }
            const url = car === 'ALL' ? '/api/orders/broadcast' : '/api/orders/create';
            fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({car_number: car, from_address: from, to_address: to, price: parseInt(price), client: client})
            }).then(r => r.json()).then(d => {
                alert(d.success ? '✅ Заказ отправлен!' : '❌ ' + (d.error || ''));
                location.reload();
            });
        }

        function quickOrder(car) {
            document.getElementById('orderCar').value = car;
            document.getElementById('orderFrom').focus();
            window.scrollTo({top:0, behavior:'smooth'});
        }

        function saveTariffs() {
            const base   = document.getElementById('tBase').value;
            const city   = document.getElementById('tCity').value;
            const suburb = document.getElementById('tSuburb').value;
            const wait   = document.getElementById('tWait').value;
            if (!base||!city||!suburb||!wait) { alert('Заполните все!'); return; }
            fetch('/api/tariffs', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({base_fare:parseInt(base),city_rate:parseInt(city),suburb_rate:parseInt(suburb),wait_rate:parseInt(wait)})
            }).then(() => { alert('✅ Сохранено!'); location.reload(); });
        }

        function approveCode(pinId) {
            if (!confirm('Одобрить?')) return;
            fetch('/api/admin/approve_code', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({pin_id: pinId})
            }).then(r=>r.json()).then(d=>{ alert(d.success?'✅':'❌'); location.reload(); });
        }

        function rejectCode(pinId) {
            if (!confirm('Отклонить?')) return;
            fetch('/api/admin/reject_code', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({pin_id: pinId})
            }).then(() => location.reload());
        }

        function addBalance(car) {
            const amount = prompt('Сумма для ' + car + ':');
            if (!amount || isNaN(amount) || parseInt(amount) <= 0) return;
            fetch('/api/admin/add_balance', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({car_number: car, amount: parseInt(amount)})
            }).then(r=>r.json()).then(d=>{
                alert(d.success ? '✅ ' + d.new_balance.toLocaleString() + ' сум' : '❌');
                location.reload();
            });
        }

        function approveBalance(id) {
            if (!confirm('Одобрить?')) return;
            fetch('/api/admin/approve_balance', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({id: id})
            }).then(() => location.reload());
        }

        function rejectBalance(id) {
            if (!confirm('Отклонить?')) return;
            fetch('/api/admin/reject_balance', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({id: id})
            }).then(() => location.reload());
        }

        function removeDriver(did) {
            if (!confirm('Убрать?')) return;
            fetch('/remove_driver', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({driver: did})
            }).then(() => location.reload());
        }

        function deleteDriver(car) {
            if (!confirm('Удалить ' + car + '?')) return;
            fetch('/api/admin/delete_driver', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({car_number: car})
            }).then(() => location.reload());
        }
    </script>
</body>
</html>
"""

# ==================== КАРТА ====================
@app.route('/map')
def map_page():
    api_key = "AIzaSyDbbgIqjyOqzS7gozVqmZ_V4G1T6cpKXC0"
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Карта — TAXI 3042</title>
    <style>*{{margin:0;padding:0;}}#map{{width:100vw;height:100vh;}}
    #info{{position:absolute;top:10px;right:10px;background:rgba(0,0,0,0.8);color:#FFD600;padding:10px 16px;border-radius:10px;font-size:13px;z-index:100;}}</style>
</head>
<body>
<div id="map"></div>
<div id="info">🚗 Онлайн: <b id="cnt">0</b></div>
<script>
    let map,markers={{}},iws={{}},first=true;
    function initMap(){{
        map=new google.maps.Map(document.getElementById('map'),{{
            center:{{lat:41.3069,lng:61.0838}},zoom:12,
            styles:[{{elementType:'geometry',stylers:[{{color:'#1a1a2e'}}]}},
                    {{featureType:'road',elementType:'geometry',stylers:[{{color:'#304a7d'}}]}},
                    {{featureType:'water',elementType:'geometry',stylers:[{{color:'#0e1626'}}]}}]
        }});
        update();setInterval(update,2000);
    }}
    function update(){{
        fetch('/api/drivers').then(r=>r.json()).then(data=>{{
            document.getElementById('cnt').textContent=Object.keys(data).length;
            let bounds=new google.maps.LatLngBounds(),has=false;
            Object.keys(markers).forEach(k=>{{if(!data[k]){{markers[k].setMap(null);delete markers[k];}}}});
            for(let k in data){{
                let d=data[k],lat=parseFloat(d.lat),lng=parseFloat(d.lng);
                if(isNaN(lat)||isNaN(lng))continue;
                let pos={{lat,lng}};bounds.extend(pos);has=true;
                let icon={{url:d.status==='free'?'https://maps.google.com/mapfiles/ms/icons/green-dot.png':'https://maps.google.com/mapfiles/ms/icons/red-dot.png',scaledSize:new google.maps.Size(40,40)}};
                if(markers[k]){{markers[k].setPosition(pos);markers[k].setIcon(icon);}}
                else{{
                    markers[k]=new google.maps.Marker({{position:pos,map,title:d.car_number,icon,
                        label:{{text:d.car_number,color:'#FFD600',fontSize:'11px',fontWeight:'bold'}}}});
                    markers[k].addListener('click',()=>{{
                        Object.values(iws).forEach(w=>w.close());
                        iws[k]=new google.maps.InfoWindow({{content:`<div style="background:#1a1a1a;color:#fff;padding:12px;border-radius:8px;min-width:180px;">
                            <b style="color:#FFD600">🚗 ${{d.car_number}}</b><br><br>
                            👤 ${{d.driver_name||'—'}}<br>
                            📍 ${{d.status==='free'?'🟢 Свободен':'🔴 На заказе'}}<br>
                            ⚡ ${{d.speed}} км/ч<br>
                            💰 ${{parseInt(d.balance||0).toLocaleString()}} сум</div>`}});
                        iws[k].open(map,markers[k]);
                    }});
                }}
            }}
            if(has&&first){{first=false;map.fitBounds(bounds);if(Object.keys(data).length===1)map.setZoom(14);}}
        }});
    }}
</script>
<script async defer src="https://maps.googleapis.com/maps/api/js?key={api_key}&callback=initMap"></script>
</body></html>"""

# ==================== РОУТЫ ====================
@app.route('/')
def index():
    all_drivers_list = [{
        "name":       d.get("name", ""),
        "phone":      d.get("phone", ""),
        "car_number": d.get("car_number", car),
        "pin":        d.get("pin", ""),
        "balance":    get_balance(car)
    } for car, d in driver_list.items()]

    return render_template_string(
        ADMIN_HTML,
        current_time     = datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        total_drivers    = len(drivers),
        free_drivers     = sum(1 for d in drivers.values() if d.get('status') == 'free'),
        busy_drivers     = sum(1 for d in drivers.values() if d.get('status') == 'busy'),
        pending_count    = sum(1 for p in pending_codes.values() if p.get('status') == 'pending'),
        registered_count = len(driver_list),
        drivers_list     = list(drivers.items()),
        pending_list     = get_pending_codes_db(),
        balance_requests = sorted(balance_requests, key=lambda x: x['id'], reverse=True)[:20],
        all_drivers_list = all_drivers_list,
        tariffs          = get_tariffs_db()
    )

@app.route('/api/driver/register', methods=['POST'])
def register_driver():
    data   = request.json
    phone  = data.get('phone', '')
    car    = data.get('car_number', '')
    name   = data.get('name', 'Водитель')
    pin    = str(random.randint(1000, 9999))
    pin_id = f"pin_{int(time.time())}_{random.randint(100,999)}"
    pending_codes[pin_id] = {
        "pin_id":     pin_id,
        "name":       name,
        "phone":      phone,
        "car_number": car,
        "pin":        pin,
        "status":     "pending",
        "created_at": time.time()
    }
    tg_notify_new_pin(pin_id, name, car, phone, pin)
    return jsonify({"success": True, "message": "Заявка отправлена"})

@app.route('/api/driver/login', methods=['POST'])
def driver_login():
    data = request.json
    pin  = data.get('pin', '')
    for car, d in driver_list.items():
        if d.get('pin') == pin:
            return jsonify({"success": True, "driver_id": car,
                            "name": d.get('name', ''), "balance": get_balance(car)})
    for pid, info in pending_codes.items():
        if info.get('pin') == pin and info.get('status') == 'pending':
            info['status'] = 'approved'
            car = info['car_number']
            driver_list[car] = info
            set_balance(car, 50000)
            tg_notify_approved(info['name'], car, pin)
            return jsonify({"success": True, "driver_id": car,
                            "name": info.get('name', ''), "balance": 50000})
    return jsonify({"success": False, "error": "Неверный ПИН"}), 401

@app.route('/api/admin/pending_codes', methods=['GET'])
def get_pending_codes_route():
    return jsonify(get_pending_codes_db())

@app.route('/api/admin/pending_by_car', methods=['GET'])
def pending_by_car():
    result = {}
    for info in pending_codes.values():
        if info.get('status') == 'pending':
            result[info['car_number']] = info['pin']
    return jsonify(result)

@app.route('/api/admin/approve_code', methods=['POST'])
def approve_code():
    pin_id = request.json.get('pin_id', '')
    info   = pending_codes.get(pin_id)
    if not info:
        return jsonify({"success": False, "error": "Не найдено"})
    info['status'] = 'approved'
    car = info['car_number']
    driver_list[car] = info
    set_balance(car, 50000)
    tg_notify_approved(info['name'], car, info['pin'])
    return jsonify({"success": True})

@app.route('/api/admin/reject_code', methods=['POST'])
def reject_code():
    pin_id = request.json.get('pin_id', '')
    info   = pending_codes.get(pin_id)
    if info:
        info['status'] = 'rejected'
        tg_notify_rejected(info['name'], info['car_number'])
    return jsonify({"success": True})

@app.route('/api/admin/delete_driver', methods=['POST'])
def delete_driver():
    car = request.json.get('car_number', '')
    driver_list.pop(car, None)
    drivers.pop(car, None)
    tg_send(f"🗑 Водитель {car} удалён")
    return jsonify({"success": True})

@app.route('/api/admin/add_balance', methods=['POST'])
def admin_add_balance():
    car    = request.json.get('car_number', '')
    amount = int(request.json.get('amount', 0))
    if amount <= 0:
        return jsonify({"success": False})
    new_b = get_balance(car) + amount
    set_balance(car, new_b)
    if car in drivers:
        drivers[car]['balance'] = new_b
    tg_send(f"💰 {car}: +{amount:,} → {new_b:,} сум")
    return jsonify({"success": True, "new_balance": new_b})

@app.route('/api/balance/request', methods=['POST'])
def request_balance():
    car    = request.json.get('car_number', '')
    amount = int(request.json.get('amount', 0))
    if amount <= 0:
        return jsonify({"success": False})
    req_id = len(balance_requests) + 1
    balance_requests.append({
        "id": req_id, "car_number": car,
        "amount": amount, "status": "pending", "created_at": time.time()
    })
    tg_notify_balance_request(req_id, car, amount)
    return jsonify({"success": True})

@app.route('/api/admin/approve_balance', methods=['POST'])
def approve_balance():
    req_id = int(request.json.get('id', 0))
    for req in balance_requests:
        if req['id'] == req_id and req['status'] == 'pending':
            car   = req['car_number']
            new_b = get_balance(car) + req['amount']
            set_balance(car, new_b)
            if car in drivers:
                drivers[car]['balance'] = new_b
            req['status'] = 'approved'
            tg_send(f"✅ {car}: +{req['amount']:,} → {new_b:,} сум")
            break
    return jsonify({"success": True})

@app.route('/api/admin/reject_balance', methods=['POST'])
def reject_balance():
    req_id = int(request.json.get('id', 0))
    for req in balance_requests:
        if req['id'] == req_id:
            req['status'] = 'rejected'
            break
    return jsonify({"success": True})

@app.route('/api/driver/<driver_id>/balance', methods=['GET'])
def get_driver_balance(driver_id):
    return jsonify({"balance": get_balance(driver_id)})

@app.route('/api/tariffs', methods=['GET'])
def get_tariffs():
    return jsonify(get_tariffs_db())

@app.route('/api/tariffs', methods=['POST'])
def update_tariffs():
    data = request.get_json(force=True)
    tariffs_data.update({
        "base_fare":   int(data.get('base_fare',   5000)),
        "city_rate":   int(data.get('city_rate',   2800)),
        "suburb_rate": int(data.get('suburb_rate', 3000)),
        "wait_rate":   int(data.get('wait_rate',   500))
    })
    tg_send(f"💰 Тарифы обновлены")
    return jsonify({'status': 'ok'})

@app.route('/location', methods=['POST'])
def location():
    data = request.get_json(force=True)
    did  = data.get('driver', data.get('car_number', 'unknown'))
    bal  = get_balance(did)
    drivers[did] = {
        'lat':         data.get('lat', 0),
        'lng':         data.get('lng', 0),
        'speed':       data.get('speed', 0),
        'status':      data.get('status', 'free'),
        'car_number':  data.get('car_number', did),
        'balance':     bal,
        'phone':       data.get('phone', ''),
        'driver_name': data.get('driver_name', ''),
        'time_str':    datetime.now().strftime('%H:%M:%S'),
        'timestamp':   time.time()
    }
    return jsonify({'status': 'ok', 'balance': bal})

@app.route('/api/drivers', methods=['GET'])
def get_drivers():
    return jsonify(drivers)

@app.route('/remove_driver', methods=['POST'])
def remove_driver():
    data = request.get_json(force=True)
    did  = data.get('driver', data.get('car_number', ''))
    drivers.pop(did, None)
    return jsonify({'status': 'ok'})

@app.route('/api/balance', methods=['POST'])
def update_balance():
    data   = request.get_json(force=True)
    did    = data.get('driver', '')
    amount = int(data.get('amount', 0))
    new_b  = get_balance(did) + amount
    set_balance(did, new_b)
    if did in drivers:
        drivers[did]['balance'] = new_b
    return jsonify({'status': 'ok', 'new_balance': new_b})

@app.route('/admin/block_driver', methods=['POST'])
def block_driver():
    car = request.get_json(force=True).get('car_number', '')
    if car in drivers:
        drivers[car]['status'] = 'blocked'
    tg_send(f"🚫 {car} заблокирован")
    return jsonify({'status': 'ok'})

@app.route('/api/orders/create', methods=['POST'])
def create_order():
    data     = request.json
    car      = data.get('car_number', '')
    from_a   = data.get('from_address', '')
    to_a     = data.get('to_address', '')
    price    = int(data.get('price', 0))
    client   = data.get('client', 'Клиент')
    distance = data.get('distance', '—')
    order_id = create_order_internal(car, from_a, to_a, price, client, distance)
    tg_send(f"📦 Заказ → {car}\n{from_a} → {to_a}\n💰 {price:,} сум")
    return jsonify({"success": True, "order_id": order_id})

@app.route('/api/orders/broadcast', methods=['POST'])
def broadcast_order():
    data   = request.json
    from_a = data.get('from_address', '')
    to_a   = data.get('to_address', '')
    price  = int(data.get('price', 0))
    client = data.get('client', 'Клиент')
    if not drivers:
        return jsonify({"success": False, "error": "Нет водителей"})
    count = 0
    for car, d in list(drivers.items()):
        if d.get('status') == 'free':
            create_order_internal(car, from_a, to_a, price, client)
            count += 1
    tg_send(f"📢 Заказ → {count} водителям\n{from_a} → {to_a}\n💰 {price:,} сум")
    return jsonify({"success": True, "sent_to": count})

@app.route('/api/orders/pending', methods=['GET'])
def get_pending_order():
    car = request.args.get('car', '')
    if not car:
        return jsonify({"has_order": False})
    for order in reversed(orders_db):
        if order['car_number'] == car and order['status'] == 'pending':
            return jsonify({"has_order": True, **order})
    return jsonify({"has_order": False})

@app.route('/api/orders/respond', methods=['POST'])
def respond_to_order():
    data     = request.json
    order_id = int(data.get('order_id', 0))
    car      = data.get('car_number', '')
    action   = data.get('response', '')
    for order in orders_db:
        if order['id'] == order_id:
            order['status'] = action
            break
    if action == 'accepted':
        tg_send(f"✅ {car} принял заказ")
    else:
        tg_send(f"❌ {car} отклонил заказ")
    return jsonify({"success": True})

@app.route('/api/orders/list', methods=['GET'])
def list_orders():
    return jsonify(list(reversed(orders_db))[:50])

@app.route('/api/chat/send', methods=['POST'])
def chat_send():
    data = request.json
    car  = data.get('car_number', '')
    text = data.get('text', '')
    drv  = data.get('driver', '')
    t    = datetime.now().strftime('%H:%M')
    if car not in chat_messages:
        chat_messages[car] = []
    chat_messages[car].append({
        "id":         int(time.time() * 1000),
        "car_number": car,
        "from":       "driver",
        "text":       text,
        "time":       t
    })
    tg_send(f"💬 {drv} ({car}):\n{text}")
    return jsonify({"success": True})

@app.route('/api/chat/messages', methods=['GET'])
def chat_get():
    car = request.args.get('car', '')
    return jsonify(chat_messages.get(car, [])[-50:])

@app.route('/api/chat/dispatch', methods=['POST'])
def chat_dispatch():
    data = request.json
    car  = data.get('car_number', '')
    text = data.get('text', '')
    t    = datetime.now().strftime('%H:%M')
    if car not in chat_messages:
        chat_messages[car] = []
    chat_messages[car].append({
        "id":         int(time.time() * 1000),
        "car_number": car,
        "from":       "dispatcher",
        "text":       text,
        "time":       t
    })
    return jsonify({"success": True})

@app.route('/ping')
def ping():
    return jsonify({
        'status':         'alive',
        'drivers_online': len(drivers),
        'drivers_total':  len(driver_list)
    })

if __name__ == '__main__':
    print("🚕 TAXI 3042 — STARTED")
    app.run(host='0.0.0.0', port=5000, debug=False)
