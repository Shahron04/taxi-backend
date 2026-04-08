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
                            "/orders — активные заказы\n"
                            "/order НОМЕР Откуда;Куда;Цена\n\n"
                            "Пример:\n"
                            "<code>/order 90T785OA Bozor;Aeroport;25000</code>"
                        )
                    elif text == "/status":
                        online = sum(1 for d in drivers.values() if d.get("status") == "free")
                        busy   = sum(1 for d in drivers.values() if d.get("status") == "busy")
                        pend   = sum(1 for p in pending_codes.values() if p.get("status") == "pending")
                        active = sum(1 for o in orders_db if o.get("status") == "pending")
                        tg_send(
                            f"📊 <b>Статус системы</b>\n\n"
                            f"🟢 Свободны: {online}\n"
                            f"🔴 На заказе: {busy}\n"
                            f"📍 Сейчас онлайн: {len(drivers)}\n"
                            f"👥 Всего водителей: {len(driver_list)}\n"
                            f"📦 Активных заказов: {active}\n"
                            f"⏳ Ждут ПИН: {pend}"
                        )
                    elif text == "/drivers":
                        if not drivers:
                            tg_send("Нет водителей онлайн")
                        else:
                            lines = []
                            for d in drivers.values():
                                icon = "🟢" if d.get("status") == "free" else "🔴"
                                bal  = get_balance(d['car_number'])
                                lines.append(
                                    f"{icon} {d['car_number']} — {d.get('driver_name','—')}\n"
                                    f"   💰 {bal:,} сум"
                                )
                            tg_send("🚗 <b>Водители онлайн:</b>\n\n" + "\n".join(lines))
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
                    elif text == "/orders":
                        active = [o for o in orders_db if o.get("status") == "pending"]
                        if not active:
                            tg_send("📦 Нет активных заказов")
                        else:
                            for o in active:
                                tg_send(
                                    f"📦 <b>Заказ #{o['order_num']}</b>\n"
                                    f"🚗 {o['car_number']}\n"
                                    f"📍 {o['from_address']} → {o['to_address']}\n"
                                    f"💰 {o['price']:,} сум"
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
    <title>TAXI 3042 — Диспетчерская</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family:'Segoe UI',sans-serif; background:#0d0d0d; color:#eee; min-height:100vh; }

        /* HEADER */
        .header {
            background:linear-gradient(135deg,#1a1a1a,#000);
            padding:16px 24px;
            display:flex;
            align-items:center;
            justify-content:space-between;
            border-bottom:2px solid #FFD600;
            position:sticky;
            top:0;
            z-index:100;
        }
        .header-left h1 { color:#FFD600; font-size:22px; }
        .header-left p  { color:#555; font-size:12px; margin-top:2px; }
        .header-right   { display:flex; gap:10px; align-items:center; }
        .badge { padding:4px 12px; border-radius:20px; font-size:12px; font-weight:bold; }
        .badge-tg   { background:#0088cc; color:#fff; }
        .badge-live { background:#1a3a1a; color:#4CAF50; border:1px solid #2a5a2a; }

        /* STATS */
        .stats { display:flex; justify-content:center; gap:12px; padding:16px; flex-wrap:wrap; }
        .stat-card {
            background:linear-gradient(135deg,#1a1a1a,#111);
            border-radius:14px;
            padding:16px 24px;
            text-align:center;
            min-width:120px;
            border:1px solid #222;
            transition:transform 0.2s;
        }
        .stat-card:hover { transform:translateY(-2px); }
        .stat-card .number { font-size:28px; font-weight:bold; color:#FFD600; }
        .stat-card .label  { color:#555; margin-top:4px; font-size:11px; text-transform:uppercase; }

        /* SECTIONS */
        .section { max-width:1200px; margin:12px auto; padding:0 16px; }
        .section-header {
            display:flex;
            align-items:center;
            gap:8px;
            margin-bottom:12px;
            border-bottom:1px solid #222;
            padding-bottom:8px;
        }
        .section-header h2 { color:#FFD600; font-size:14px; text-transform:uppercase; letter-spacing:1px; }

        /* TABLES */
        table { width:100%; border-collapse:collapse; background:#111; border-radius:12px; overflow:hidden; margin-bottom:12px; }
        th { background:#000; padding:10px 14px; text-align:left; color:#FFD600; font-size:11px; text-transform:uppercase; letter-spacing:1px; }
        td { padding:10px 14px; border-bottom:1px solid #1a1a1a; font-size:13px; }
        tr:hover td { background:#161616; }
        tr:last-child td { border-bottom:none; }

        /* BUTTONS */
        .btn { padding:5px 12px; border:none; border-radius:8px; cursor:pointer; font-size:12px; font-weight:bold; margin:2px; transition:opacity 0.2s; }
        .btn:hover { opacity:0.85; }
        .btn-approve { background:#FFD600; color:#000; }
        .btn-reject  { background:#1a1a1a; color:#666; border:1px solid #333; }
        .btn-danger  { background:#2a0000; color:#FF5252; border:1px solid #4a0000; }
        .btn-order   { background:#003366; color:#4da6ff; border:1px solid #004499; }
        .btn-chat    { background:#1a3a1a; color:#4CAF50; border:1px solid #2a5a2a; }
        .btn-info    { background:#1a1a3a; color:#6699ff; border:1px solid #2a2a5a; }

        /* ORDER FORM */
        .order-form {
            background:#111;
            padding:20px;
            border-radius:12px;
            margin-bottom:12px;
            border:1px solid #1a1a1a;
        }
        .form-row { display:flex; gap:10px; flex-wrap:wrap; align-items:flex-end; margin-bottom:10px; }
        .form-group { display:flex; flex-direction:column; gap:4px; }
        .form-group label { color:#666; font-size:11px; text-transform:uppercase; letter-spacing:1px; }
        .form-group input,
        .form-group select {
            background:#0d0d0d;
            color:#fff;
            border:1px solid #333;
            padding:8px 12px;
            border-radius:8px;
            font-size:13px;
            outline:none;
            transition:border-color 0.2s;
        }
        .form-group input:focus,
        .form-group select:focus { border-color:#FFD600; }
        .btn-send {
            padding:9px 24px;
            background:#FFD600;
            color:#000;
            border:none;
            border-radius:8px;
            font-weight:bold;
            font-size:14px;
            cursor:pointer;
            transition:background 0.2s;
            white-space:nowrap;
        }
        .btn-send:hover    { background:#e6c200; }
        .btn-send:disabled { background:#444; color:#888; cursor:not-allowed; }
        .order-msg {
            display:none;
            margin-top:10px;
            padding:10px 16px;
            border-radius:8px;
            font-size:13px;
            font-weight:bold;
            background:#1a1a1a;
        }

        /* TARIFFS */
        .tariff-grid { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:12px; }
        .tariff-card {
            background:#111;
            border:1px solid #222;
            border-radius:12px;
            padding:16px 20px;
            text-align:center;
            min-width:140px;
            flex:1;
        }
        .tariff-card .val { font-size:22px; font-weight:bold; color:#FFD600; }
        .tariff-card .lbl { color:#555; font-size:11px; margin-top:4px; text-transform:uppercase; }

        /* CHAT */
        .chat-section { background:#111; border-radius:12px; padding:16px; border:1px solid #1a1a1a; }
        .chat-messages {
            height:280px;
            overflow-y:auto;
            background:#0a0a0a;
            border-radius:10px;
            padding:12px;
            margin-bottom:10px;
            scroll-behavior:smooth;
        }
        .chat-messages::-webkit-scrollbar { width:4px; }
        .chat-messages::-webkit-scrollbar-thumb { background:#333; border-radius:2px; }
        .chat-msg { padding:8px 12px; margin:4px 0; border-radius:10px; max-width:75%; font-size:13px; line-height:1.4; }
        .chat-msg.own   { background:#FFD600; color:#000; margin-left:auto; border-bottom-right-radius:2px; }
        .chat-msg.other { background:#1a1a1a; color:#eee; border-bottom-left-radius:2px; }
        .chat-msg small { opacity:0.6; font-size:10px; display:block; margin-top:2px; }
        .chat-input { display:flex; gap:8px; }
        .chat-input input {
            flex:1;
            background:#0d0d0d;
            color:#fff;
            border:1px solid #333;
            padding:9px 12px;
            border-radius:8px;
            font-size:13px;
            outline:none;
        }
        .chat-input input:focus { border-color:#FFD600; }
        .chat-input button {
            padding:9px 18px;
            background:#FFD600;
            color:#000;
            border:none;
            border-radius:8px;
            font-weight:bold;
            cursor:pointer;
        }
        .quick-btns { display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }
        .quick-btn {
            padding:5px 12px;
            background:#1a1a1a;
            color:#aaa;
            border:1px solid #333;
            border-radius:20px;
            font-size:12px;
            cursor:pointer;
            transition:all 0.2s;
        }
        .quick-btn:hover { background:#FFD600; color:#000; border-color:#FFD600; }

        /* STATUS BADGES */
        .status-free    { color:#4CAF50; font-weight:bold; }
        .status-busy    { color:#FF5252; font-weight:bold; }
        .status-pending { color:#FF9800; font-weight:bold; }
        .status-ok      { color:#4CAF50; font-weight:bold; }
        .pin-code { font-size:20px; font-weight:bold; color:#FFD600; font-family:monospace; letter-spacing:4px; }
        .car-num  { color:#FFD600; font-weight:bold; }
        .no-data  { text-align:center; padding:30px; color:#333; font-size:14px; }
        .refresh-bar { text-align:center; padding:12px; color:#222; font-size:11px; }

        /* DRIVER INFO MODAL */
        .modal-overlay {
            display:none;
            position:fixed;
            top:0; left:0; right:0; bottom:0;
            background:rgba(0,0,0,0.8);
            z-index:200;
            justify-content:center;
            align-items:center;
        }
        .modal-overlay.active { display:flex; }
        .modal {
            background:#111;
            border-radius:16px;
            padding:24px;
            min-width:320px;
            max-width:480px;
            border:1px solid #333;
            position:relative;
        }
        .modal h3 { color:#FFD600; margin-bottom:16px; font-size:18px; }
        .modal-close {
            position:absolute;
            top:12px; right:16px;
            background:none;
            border:none;
            color:#666;
            font-size:20px;
            cursor:pointer;
        }
        .info-row { display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #1a1a1a; font-size:13px; }
        .info-row:last-child { border-bottom:none; }
        .info-label { color:#666; }
        .info-value { color:#eee; font-weight:bold; }
        .modal-actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:16px; }
        .modal-actions .btn { flex:1; padding:10px; text-align:center; font-size:13px; }
    </style>
</head>
<body>

<!-- HEADER -->
<div class="header">
    <div class="header-left">
        <h1>🚕 TAXI 3042 XAZARASP</h1>
        <p>Диспетчерская панель • {{ current_time }}</p>
    </div>
    <div class="header-right">
        <span class="badge badge-live">🟢 Онлайн</span>
        <span class="badge badge-tg">🤖 Telegram</span>
    </div>
</div>

<!-- STATS -->
<div class="stats">
    <div class="stat-card">
        <div class="number">{{ total_drivers }}</div>
        <div class="label">На линии</div>
    </div>
    <div class="stat-card">
        <div class="number" style="color:#4CAF50">{{ free_drivers }}</div>
        <div class="label">Свободны</div>
    </div>
    <div class="stat-card">
        <div class="number" style="color:#FF5252">{{ busy_drivers }}</div>
        <div class="label">На заказе</div>
    </div>
    <div class="stat-card">
        <div class="number" style="color:#FF9800">{{ pending_count }}</div>
        <div class="label">Ждут ПИН</div>
    </div>
    <div class="stat-card">
        <div class="number">{{ registered_count }}</div>
        <div class="label">Всего</div>
    </div>
    <div class="stat-card">
        <div class="number" style="color:#6699ff">{{ active_orders }}</div>
        <div class="label">Заказов</div>
    </div>
</div>

<!-- СОЗДАТЬ ЗАКАЗ -->
<div class="section">
    <div class="section-header">
        <h2>📦 Создать заказ</h2>
    </div>
    <div class="order-form">
        <div class="form-row">
            <div class="form-group">
                <label>Водитель</label>
                <select id="orderCar" style="width:220px;">
                    <option value="">-- Выбрать --</option>
                    <option value="ALL">📢 Всем свободным</option>
                    {% for did, d in drivers_list %}
                    <option value="{{ d.car_number }}">
                        {{ d.car_number }} — {{ d.driver_name or '—' }}
                        {% if d.status == 'free' %}🟢{% else %}🔴{% endif %}
                    </option>
                    {% endfor %}
                </select>
            </div>
            <div class="form-group">
                <label>Откуда</label>
                <input type="text" id="orderFrom" placeholder="Адрес подачи" style="width:180px;">
            </div>
            <div class="form-group">
                <label>Куда</label>
                <input type="text" id="orderTo" placeholder="Адрес назначения" style="width:180px;">
            </div>
            <div class="form-group">
                <label>Цена (сум)</label>
                <input type="number" id="orderPrice" placeholder="0" style="width:130px;" min="0">
            </div>
            <div class="form-group">
                <label>Клиент</label>
                <input type="text" id="orderClient" placeholder="Имя клиента" style="width:140px;">
            </div>
            <div class="form-group">
                <label>&nbsp;</label>
                <button class="btn-send" id="btnSendOrder" onclick="createOrder()">
                    🚀 Отправить
                </button>
            </div>
        </div>
        <!-- Быстрые адреса -->
        <div style="margin-top:8px;">
            <span style="color:#555;font-size:11px;margin-right:8px;">БЫСТРО:</span>
            <button class="quick-btn" onclick="setAddr('Bozor','')">Bozor</button>
            <button class="quick-btn" onclick="setAddr('Aeroport','')">Aeroport</button>
            <button class="quick-btn" onclick="setAddr('Kasalxona','')">Kasalxona</button>
            <button class="quick-btn" onclick="setAddr('Vokzal','')">Vokzal</button>
            <button class="quick-btn" onclick="setAddr('Maktab','')">Maktab</button>
        </div>
        <div id="orderMsg" class="order-msg"></div>
    </div>
</div>

<!-- ТАРИФЫ -->
<div class="section">
    <div class="section-header">
        <h2>💰 Тарифы</h2>
    </div>
    <div class="tariff-grid">
        <div class="tariff-card">
            <div class="val">{{ tariffs.base_fare }}</div>
            <div class="lbl">Посадка (сум)</div>
        </div>
        <div class="tariff-card">
            <div class="val">{{ tariffs.city_rate }}</div>
            <div class="lbl">Город (сум/км)</div>
        </div>
        <div class="tariff-card">
            <div class="val">{{ tariffs.suburb_rate }}</div>
            <div class="lbl">Загород (сум/км)</div>
        </div>
        <div class="tariff-card">
            <div class="val">{{ tariffs.wait_rate }}</div>
            <div class="lbl">Ожидание (сум/мин)</div>
        </div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
        <input type="number" id="tBase"   placeholder="Посадка"   style="background:#111;color:#fff;border:1px solid #333;padding:7px 10px;border-radius:8px;width:120px;outline:none;">
        <input type="number" id="tCity"   placeholder="Город"     style="background:#111;color:#fff;border:1px solid #333;padding:7px 10px;border-radius:8px;width:120px;outline:none;">
        <input type="number" id="tSuburb" placeholder="Загород"   style="background:#111;color:#fff;border:1px solid #333;padding:7px 10px;border-radius:8px;width:120px;outline:none;">
        <input type="number" id="tWait"   placeholder="Ожидание"  style="background:#111;color:#fff;border:1px solid #333;padding:7px 10px;border-radius:8px;width:120px;outline:none;">
        <button onclick="saveTariffs()" class="btn-send" style="padding:7px 20px;">💾 Сохранить</button>
    </div>
</div>

<!-- ЗАЯВКИ ПИН -->
{% if pending_list %}
<div class="section">
    <div class="section-header">
        <h2>🔑 Заявки на регистрацию</h2>
        <span style="background:#FF9800;color:#000;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:bold;">
            {{ pending_list|selectattr('status','eq','pending')|list|length }} новых
        </span>
    </div>
    <table>
        <tr>
            <th>Имя</th><th>Телефон</th><th>Авто</th>
            <th>ПИН-код</th><th>Статус</th><th>Действия</th>
        </tr>
        {% for p in pending_list %}
        <tr>
            <td>{{ p.name }}</td>
            <td>{{ p.phone }}</td>
            <td><span class="car-num">{{ p.car_number }}</span></td>
            <td><span class="pin-code">{{ p.pin }}</span></td>
            <td>
                {% if p.status == 'pending' %}
                    <span class="status-pending">⏳ Ожидает</span>
                {% elif p.status == 'approved' %}
                    <span class="status-ok">✅ Одобрен</span>
                {% else %}
                    <span style="color:#555">❌</span>
                {% endif %}
            </td>
            <td>
                {% if p.status == 'pending' %}
                <button class="btn btn-approve" onclick="approveCode('{{ p.pin_id }}')">✅ Одобрить</button>
                <button class="btn btn-reject"  onclick="rejectCode('{{ p.pin_id }}')">❌ Отказ</button>
                {% else %}
                <span style="color:#333">—</span>
                {% endif %}
            </td>
        </tr>
        {% endfor %}
    </table>
</div>
{% endif %}

<!-- ЗАЯВКИ БАЛАНС -->
{% if balance_requests %}
<div class="section">
    <div class="section-header">
        <h2>💰 Заявки на пополнение баланса</h2>
    </div>
    <table>
        <tr><th>Авто</th><th>Сумма</th><th>Статус</th><th>Действия</th></tr>
        {% for r in balance_requests %}
        <tr>
            <td><span class="car-num">{{ r.car_number }}</span></td>
            <td style="color:#4CAF50;font-weight:bold;">{{ "{:,}".format(r.amount) }} сум</td>
            <td>
                {% if r.status == 'pending' %}
                    <span class="status-pending">⏳ Ожидает</span>
                {% elif r.status == 'approved' %}
                    <span class="status-ok">✅ Одобрено</span>
                {% else %}
                    <span style="color:#FF5252">❌ Отклонено</span>
                {% endif %}
            </td>
            <td>
                {% if r.status == 'pending' %}
                <button class="btn btn-approve" onclick="approveBalance({{ r.id }})">✅</button>
                <button class="btn btn-reject"  onclick="rejectBalance({{ r.id }})" >❌</button>
                {% else %}—{% endif %}
            </td>
        </tr>
        {% endfor %}
    </table>
</div>
{% endif %}

<!-- АКТИВНЫЕ ЗАКАЗЫ -->
{% if active_orders_list %}
<div class="section">
    <div class="section-header">
        <h2>📦 Активные заказы</h2>
    </div>
    <table>
        <tr><th>#</th><th>Водитель</th><th>Откуда</th><th>Куда</th><th>Цена</th><th>Клиент</th><th>Статус</th></tr>
        {% for o in active_orders_list %}
        <tr>
            <td style="color:#555">{{ o.order_num }}</td>
            <td><span class="car-num">{{ o.car_number }}</span></td>
            <td>{{ o.from_address }}</td>
            <td>{{ o.to_address }}</td>
            <td style="color:#4CAF50;font-weight:bold;">{{ "{:,}".format(o.price) }} сум</td>
            <td>{{ o.client }}</td>
            <td>
                {% if o.status == 'pending' %}
                    <span class="status-pending">⏳ Ожидает</span>
                {% elif o.status == 'accepted' %}
                    <span class="status-ok">✅ Принят</span>
                {% else %}
                    <span style="color:#555">{{ o.status }}</span>
                {% endif %}
            </td>
        </tr>
        {% endfor %}
    </table>
</div>
{% endif %}

<!-- ВОДИТЕЛИ ОНЛАЙН -->
<div class="section">
    <div class="section-header">
        <h2>🚗 Водители онлайн</h2>
    </div>
    {% if drivers_list %}
    <table>
        <tr>
            <th>Авто</th><th>Водитель</th><th>Телефон</th>
            <th>Статус</th><th>Скорость</th><th>Баланс</th>
            <th>Обновлён</th><th>Действия</th>
        </tr>
        {% for did, d in drivers_list %}
        <tr>
            <td><span class="car-num">{{ d.car_number }}</span></td>
            <td>{{ d.driver_name or '—' }}</td>
            <td>{{ d.phone or '—' }}</td>
            <td>
                {% if d.status == 'free' %}
                    <span class="status-free">🟢 Свободен</span>
                {% elif d.status == 'busy' %}
                    <span class="status-busy">🔴 На заказе</span>
                {% else %}
                    <span style="color:#555">⚫ {{ d.status }}</span>
                {% endif %}
            </td>
            <td>{{ d.speed }} км/ч</td>
            <td style="color:#4CAF50;font-weight:bold;">
                {{ "{:,}".format(d.balance|int) }} сум
            </td>
            <td style="color:#444;font-size:12px;">{{ d.time_str }}</td>
            <td>
                <button class="btn btn-order" onclick="quickOrder('{{ d.car_number }}')">📦</button>
                <button class="btn btn-chat"  onclick="openChat('{{ d.car_number }}')">💬</button>
                <button class="btn btn-approve" onclick="addBalance('{{ d.car_number }}')">💰</button>
                <button class="btn btn-info"  onclick="showInfo('{{ d.car_number }}','{{ d.driver_name or '' }}','{{ d.phone or '' }}','{{ d.status }}',{{ d.balance|int }},{{ d.speed }})">ℹ️</button>
                <button class="btn btn-danger" onclick="removeDriver('{{ did }}')">🗑</button>
            </td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <div class="no-data">🚗 Нет водителей онлайн</div>
    {% endif %}
</div>

<!-- ЧАТ -->
<div class="section" id="chatSection" style="display:none;">
    <div class="section-header">
        <h2>💬 Чат: <span id="chatCarNumber" style="color:#fff;font-weight:normal;"></span></h2>
        <button onclick="closeChat()" class="btn btn-danger" style="margin-left:auto;">✕ Закрыть</button>
    </div>
    <div class="chat-section">
        <div class="chat-messages" id="chatMessages"></div>
        <div class="chat-input">
            <input type="text" id="chatText"
                   placeholder="Введите сообщение..."
                   onkeypress="if(event.key==='Enter')sendChat()">
            <button onclick="sendChat()">📤</button>
        </div>
        <div class="quick-btns">
            <button class="quick-btn" onclick="sendQuick('Принято ✅')">Принято</button>
            <button class="quick-btn" onclick="sendQuick('Подождите ⏳')">Подождите</button>
            <button class="quick-btn" onclick="sendQuick('Есть заказ 📦')">Есть заказ</button>
            <button class="quick-btn" onclick="sendQuick('Вы свободны 🟢')">Свободны</button>
            <button class="quick-btn" onclick="sendQuick('Выезжайте на линию!')">На линию</button>
            <button class="quick-btn" onclick="sendQuick('Клиент ждёт!')">Клиент ждёт</button>
        </div>
    </div>
</div>

<!-- ВСЕ ВОДИТЕЛИ -->
{% if all_drivers_list %}
<div class="section">
    <div class="section-header">
        <h2>📋 Все зарегистрированные водители</h2>
    </div>
    <table>
        <tr><th>Имя</th><th>Телефон</th><th>Авто</th><th>ПИН</th><th>Баланс</th><th>Действия</th></tr>
        {% for d in all_drivers_list %}
        <tr>
            <td>{{ d.name }}</td>
            <td>{{ d.phone }}</td>
            <td><span class="car-num">{{ d.car_number }}</span></td>
            <td><span class="pin-code">{{ d.pin }}</span></td>
            <td style="color:#4CAF50;font-weight:bold;">
                {{ "{:,}".format(d.balance|int) }} сум
            </td>
            <td>
                <button class="btn btn-approve" onclick="addBalance('{{ d.car_number }}')">💰 Баланс</button>
                <button class="btn btn-danger"  onclick="deleteDriver('{{ d.car_number }}')">🗑 Удалить</button>
            </td>
        </tr>
        {% endfor %}
    </table>
</div>
{% endif %}

<!-- МОДАЛ ИНФО -->
<div class="modal-overlay" id="driverModal">
    <div class="modal">
        <button class="modal-close" onclick="closeModal()">✕</button>
        <h3>🚗 <span id="modalCar"></span></h3>
        <div class="info-row"><span class="info-label">Водитель</span><span class="info-value" id="modalName"></span></div>
        <div class="info-row"><span class="info-label">Телефон</span><span class="info-value" id="modalPhone"></span></div>
        <div class="info-row"><span class="info-label">Статус</span><span class="info-value" id="modalStatus"></span></div>
        <div class="info-row"><span class="info-label">Баланс</span><span class="info-value" id="modalBalance"></span></div>
        <div class="info-row"><span class="info-label">Скорость</span><span class="info-value" id="modalSpeed"></span></div>
        <div class="modal-actions">
            <button class="btn btn-order"   onclick="closeModal();quickOrder(currentModalCar)">📦 Заказ</button>
            <button class="btn btn-chat"    onclick="closeModal();openChat(currentModalCar)">💬 Чат</button>
            <button class="btn btn-approve" onclick="closeModal();addBalance(currentModalCar)">💰 Баланс</button>
            <button class="btn btn-danger"  onclick="closeModal();removeDriver(currentModalCar)">🗑 Убрать</button>
        </div>
    </div>
</div>

<div class="refresh-bar">🔄 Авто-обновление каждые 5 секунд</div>

<script>
    // ==================== АВТО-ОБНОВЛЕНИЕ ====================
    setTimeout(() => location.reload(), 5000);

    // ==================== ЧАТ ====================
    let currentCar     = '';
    let chatLoaded     = new Set();
    let chatInterval   = null;
    let currentModalCar = '';

    function openChat(car) {
        currentCar = car;
        document.getElementById('chatSection').style.display = 'block';
        document.getElementById('chatCarNumber').textContent  = car;
        document.getElementById('chatMessages').innerHTML     = '';
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
                    div.innerHTML =
                        `<b>${m.from === 'dispatcher' ? '👨‍💼 Диспетчер' : '🚗 ' + m.car_number}</b>: ${m.text}` +
                        `<small>${m.time}</small>`;
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
        if (!currentCar) { showMsg('❌ Сначала откройте чат!', 'red'); return; }
        fetch('/api/chat/dispatch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({car_number: currentCar, text: text})
        }).then(() => loadChat());
    }

    // ==================== ЗАКАЗЫ ====================
    function setAddr(from, to) {
        if (from) document.getElementById('orderFrom').value = from;
        if (to)   document.getElementById('orderTo').value   = to;
    }

    function createOrder() {
        const car    = document.getElementById('orderCar').value;
        const from   = document.getElementById('orderFrom').value.trim();
        const to     = document.getElementById('orderTo').value.trim();
        const price  = document.getElementById('orderPrice').value;
        const client = document.getElementById('orderClient').value.trim() || 'Клиент';

        if (!car)                          { showMsg('❌ Выберите водителя!',    'red'); return; }
        if (!from)                         { showMsg('❌ Введите откуда!',        'red'); return; }
        if (!to)                           { showMsg('❌ Введите куда!',           'red'); return; }
        if (!price || parseInt(price) <= 0){ showMsg('❌ Введите корректную цену!','red'); return; }

        const btn = document.getElementById('btnSendOrder');
        btn.disabled    = true;
        btn.textContent = '⏳ Отправляем...';

        const url = car === 'ALL' ? '/api/orders/broadcast' : '/api/orders/create';
        fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                car_number:   car,
                from_address: from,
                to_address:   to,
                price:        parseInt(price),
                client:       client
            })
        })
        .then(r => r.json())
        .then(d => {
            if (d.success) {
                showMsg('✅ Заказ отправлен! ' + (d.sent_to ? `(${d.sent_to} водителям)` : ''), 'green');
                document.getElementById('orderFrom').value   = '';
                document.getElementById('orderTo').value     = '';
                document.getElementById('orderPrice').value  = '';
                document.getElementById('orderClient').value = '';
            } else {
                showMsg('❌ Ошибка: ' + (d.error || 'попробуйте снова'), 'red');
            }
        })
        .catch(() => showMsg('❌ Нет соединения с сервером!', 'red'))
        .finally(() => {
            btn.disabled    = false;
            btn.textContent = '🚀 Отправить';
        });
    }

    function quickOrder(car) {
        document.getElementById('orderCar').value = car;
        document.getElementById('orderFrom').focus();
        window.scrollTo({top: 0, behavior: 'smooth'});
    }

    function showMsg(text, color) {
        const el = document.getElementById('orderMsg');
        el.textContent       = text;
        el.style.color       = color === 'green' ? '#4CAF50' : '#FF5252';
        el.style.display     = 'block';
        el.style.borderLeft  = `3px solid ${color === 'green' ? '#4CAF50' : '#FF5252'}`;
        setTimeout(() => { el.style.display = 'none'; }, 4000);
    }

    // ==================== ТАРИФЫ ====================
    function saveTariffs() {
        const base   = document.getElementById('tBase').value;
        const city   = document.getElementById('tCity').value;
        const suburb = document.getElementById('tSuburb').value;
        const wait   = document.getElementById('tWait').value;
        if (!base||!city||!suburb||!wait) { alert('Заполните все тарифы!'); return; }
        fetch('/api/tariffs', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                base_fare:   parseInt(base),
                city_rate:   parseInt(city),
                suburb_rate: parseInt(suburb),
                wait_rate:   parseInt(wait)
            })
        }).then(() => { alert('✅ Тарифы сохранены!'); location.reload(); });
    }

    // ==================== ПИН-КОДЫ ====================
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

    // ==================== БАЛАНС ====================
    function addBalance(car) {
        const amount = prompt(`💰 Сумма пополнения для ${car}:`);
        if (!amount || isNaN(amount) || parseInt(amount) <= 0) return;
        fetch('/api/admin/add_balance', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({car_number: car, amount: parseInt(amount)})
        }).then(r => r.json()).then(d => {
            alert(d.success
                ? `✅ Баланс пополнен!\n${car}: ${d.new_balance.toLocaleString()} сум`
                : '❌ Ошибка');
            location.reload();
        });
    }

    function approveBalance(id) {
        if (!confirm('Одобрить пополнение?')) return;
        fetch('/api/admin/approve_balance', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: id})
        }).then(() => location.reload());
    }

    function rejectBalance(id) {
        if (!confirm('Отклонить?')) return;
        fetch('/api/admin/reject_balance', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: id})
        }).then(() => location.reload());
    }

    // ==================== ВОДИТЕЛИ ====================
    function showInfo(car, name, phone, status, balance, speed) {
        currentModalCar = car;
        document.getElementById('modalCar').textContent     = car;
        document.getElementById('modalName').textContent    = name || '—';
        document.getElementById('modalPhone').textContent   = phone || '—';
        document.getElementById('modalStatus').textContent  =
            status === 'free' ? '🟢 Свободен' : status === 'busy' ? '🔴 На заказе' : '⚫ ' + status;
        document.getElementById('modalBalance').textContent = parseInt(balance).toLocaleString() + ' сум';
        document.getElementById('modalSpeed').textContent   = speed + ' км/ч';
        document.getElementById('driverModal').classList.add('active');
    }

    function closeModal() {
        document.getElementById('driverModal').classList.remove('active');
    }

    function removeDriver(did) {
        if (!confirm('Убрать из онлайн?')) return;
        fetch('/remove_driver', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({driver: did})
        }).then(() => location.reload());
    }

    function deleteDriver(car) {
        if (!confirm(`Полностью удалить ${car}?`)) return;
        fetch('/api/admin/delete_driver', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({car_number: car})
        }).then(() => location.reload());
    }

    // Закрыть модал по клику вне
    document.getElementById('driverModal').addEventListener('click', function(e) {
        if (e.target === this) closeModal();
    });
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
    <style>
        *{{margin:0;padding:0;}}
        #map{{width:100vw;height:100vh;}}
        #info{{
            position:absolute;top:10px;right:10px;
            background:rgba(0,0,0,0.85);color:#FFD600;
            padding:10px 16px;border-radius:10px;
            font-size:13px;z-index:100;
            border:1px solid #333;
        }}
    </style>
</head>
<body>
<div id="map"></div>
<div id="info">🚗 Онлайн: <b id="cnt">0</b></div>
<script>
    let map,markers={{}},iws={{}},first=true;
    function initMap(){{
        map=new google.maps.Map(document.getElementById('map'),{{
            center:{{lat:41.3069,lng:61.0838}},zoom:13,
            styles:[
                {{elementType:'geometry',stylers:[{{color:'#1a1a2e'}}]}},
                {{elementType:'labels.text.fill',stylers:[{{color:'#8ec3b9'}}]}},
                {{featureType:'road',elementType:'geometry',stylers:[{{color:'#304a7d'}}]}},
                {{featureType:'water',elementType:'geometry',stylers:[{{color:'#0e1626'}}]}}
            ]
        }});
        update();setInterval(update,2000);
    }}
    function update(){{
        fetch('/api/drivers').then(r=>r.json()).then(data=>{{
            document.getElementById('cnt').textContent=Object.keys(data).length;
            let bounds=new google.maps.LatLngBounds(),has=false;
            Object.keys(markers).forEach(k=>{{
                if(!data[k]){{markers[k].setMap(null);delete markers[k];
                    if(iws[k]){{iws[k].close();delete iws[k];}}}}
            }});
            for(let k in data){{
                let d=data[k],lat=parseFloat(d.lat),lng=parseFloat(d.lng);
                if(isNaN(lat)||isNaN(lng))continue;
                let pos={{lat,lng}};bounds.extend(pos);has=true;
                let icon={{
                    url:d.status==='free'
                        ?'https://maps.google.com/mapfiles/ms/icons/green-dot.png'
                        :'https://maps.google.com/mapfiles/ms/icons/red-dot.png',
                    scaledSize:new google.maps.Size(40,40)
                }};
                if(markers[k]){{markers[k].setPosition(pos);markers[k].setIcon(icon);}}
                else{{
                    markers[k]=new google.maps.Marker({{
                        position:pos,map,title:d.car_number,icon,
                        label:{{text:d.car_number,color:'#FFD600',fontSize:'11px',fontWeight:'bold'}}
                    }});
                    markers[k].addListener('click',()=>{{
                        Object.values(iws).forEach(w=>w.close());
                        iws[k]=new google.maps.InfoWindow({{content:`
                            <div style="background:#1a1a1a;color:#fff;padding:14px;border-radius:10px;min-width:200px;font-family:sans-serif;">
                                <b style="color:#FFD600;font-size:16px;">🚗 ${{d.car_number}}</b><br><br>
                                👤 ${{d.driver_name||'—'}}<br>
                                📱 ${{d.phone||'—'}}<br>
                                📍 <b style="color:${{d.status==='free'?'#4CAF50':'#FF5252'}}">
                                    ${{d.status==='free'?'🟢 Свободен':'🔴 На заказе'}}
                                </b><br>
                                ⚡ ${{d.speed}} км/ч<br>
                                💰 ${{parseInt(d.balance||0).toLocaleString()}} сум<br>
                                <small style="color:#555;">🕐 ${{d.time_str}}</small>
                            </div>`
                        }});
                        iws[k].open(map,markers[k]);
                    }});
                }}
            }}
            if(has&&first){{first=false;map.fitBounds(bounds);
                if(Object.keys(data).length===1)map.setZoom(15);}}
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

    active_orders_list = [o for o in reversed(orders_db) if o["status"] in ["pending", "accepted"]]

    return render_template_string(
        ADMIN_HTML,
        current_time       = datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        total_drivers      = len(drivers),
        free_drivers       = sum(1 for d in drivers.values() if d.get('status') == 'free'),
        busy_drivers       = sum(1 for d in drivers.values() if d.get('status') == 'busy'),
        pending_count      = sum(1 for p in pending_codes.values() if p.get('status') == 'pending'),
        registered_count   = len(driver_list),
        active_orders      = len(active_orders_list),
        drivers_list       = list(drivers.items()),
        pending_list       = get_pending_codes_db(),
        balance_requests   = sorted(balance_requests, key=lambda x: x['id'], reverse=True)[:20],
        all_drivers_list   = all_drivers_list,
        active_orders_list = active_orders_list,
        tariffs            = get_tariffs_db()
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
        "pin_id": pin_id, "name": name, "phone": phone,
        "car_number": car, "pin": pin,
        "status": "pending", "created_at": time.time()
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
    tg_send(f"💰 Тарифы обновлены:\n"
            f"Посадка: {tariffs_data['base_fare']:,} сум\n"
            f"Город: {tariffs_data['city_rate']:,} сум/км\n"
            f"Загород: {tariffs_data['suburb_rate']:,} сум/км\n"
            f"Ожидание: {tariffs_data['wait_rate']:,} сум/мин")
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
    tg_send(f"📦 Заказ → <b>{car}</b>\n📍 {from_a} → {to_a}\n💰 {price:,} сум\n👤 {client}")
    return jsonify({"success": True, "order_id": order_id})

@app.route('/api/orders/broadcast', methods=['POST'])
def broadcast_order():
    data   = request.json
    from_a = data.get('from_address', '')
    to_a   = data.get('to_address', '')
    price  = int(data.get('price', 0))
    client = data.get('client', 'Клиент')
    if not drivers:
        return jsonify({"success": False, "error": "Нет водителей онлайн"})
    count = 0
    for car, d in list(drivers.items()):
        if d.get('status') == 'free':
            create_order_internal(car, from_a, to_a, price, client)
            count += 1
    if count == 0:
        return jsonify({"success": False, "error": "Нет свободных водителей"})
    tg_send(f"📢 Заказ → {count} водителям\n📍 {from_a} → {to_a}\n💰 {price:,} сум")
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
        tg_send(f"✅ <b>{car}</b> принял заказ")
    else:
        tg_send(f"❌ <b>{car}</b> отклонил заказ")
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
    tg_send(f"💬 <b>{drv}</b> ({car}):\n{text}")
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
        'drivers_total':  len(driver_list),
        'orders_active':  len([o for o in orders_db if o['status'] == 'pending'])
    })

if __name__ == '__main__':
    print("🚕 TAXI 3042 XAZARASP — STARTED")
    app.run(host='0.0.0.0', port=5000, debug=False)
