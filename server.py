from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import random
import time
import threading
import requests

app = Flask(__name__)

# ==================== TELEGRAM BOT ====================
TG_TOKEN   = "8757251631:AAHMFD4cg1dU9SdZ8-7HMDxy5qDUpSc5TIs"
TG_CHAT_ID = "1053431273"
TG_API     = f"https://api.telegram.org/bot{TG_TOKEN}"

def tg_send(text, reply_markup=None):
    """Отправить сообщение в Telegram"""
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
    """Уведомление о новой заявке с кнопками"""
    text = (
        f"🔑 <b>Новая заявка на регистрацию</b>\n\n"
        f"👤 Имя: <b>{name}</b>\n"
        f"🚗 Авто: <b>{car}</b>\n"
        f"📱 Тел: <b>{phone}</b>\n\n"
        f"🔐 ПИН-код: <b>{pin}</b>\n\n"
        f"Сообщите код водителю и нажмите кнопку:"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "✅ Одобрить", "callback_data": f"approve:{pin_id}"},
            {"text": "❌ Отказать", "callback_data": f"reject:{pin_id}"}
        ]]
    }
    tg_send(text, markup)

def tg_notify_approved(name, car, pin):
    tg_send(f"✅ <b>{name}</b> ({car}) одобрен!\nПИН: <b>{pin}</b>")

def tg_notify_rejected(name, car):
    tg_send(f"❌ Заявка <b>{name}</b> ({car}) отклонена")

def tg_answer_callback(callback_id, text):
    """Ответ на нажатие кнопки"""
    try:
        requests.post(f"{TG_API}/answerCallbackQuery", data={
            "callback_query_id": callback_id,
            "text": text
        }, timeout=5)
    except:
        pass

# ==================== ДАННЫЕ В ПАМЯТИ ====================
drivers        = {}
driver_list_full = []
pending_codes  = {}
driver_balances = {}
orders         = {}
chat_messages  = []
chat_counter   = 0
orders_db      = {}   # order_id → order
order_counter  = 1000
pending_orders = {}   # car_number → order_id (ожидает ответа водителя)

# ==================== ТАРИФЫ ====================
TARIF_INFO = {
    "base_fare":   5000,
    "city_rate":   2800,
    "suburb_rate": 3000,
    "wait_rate":   500
}

# ==================== TELEGRAM POLLING ====================
tg_offset = 0

def tg_polling():
    """Фоновый поток — получаем нажатия кнопок от Telegram"""
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

                # Обработка нажатия кнопок
                if "callback_query" in upd:
                    cq      = upd["callback_query"]
                    cq_id   = cq["id"]
                    data    = cq.get("data", "")

                    if data.startswith("approve:"):
                        pin_id = data.split(":", 1)[1]
                        if pin_id in pending_codes:
                            info = pending_codes[pin_id]
                            info["status"] = "approved"
                            new_driver = {
                                "id":         info["car_number"],
                                "name":       info["name"],
                                "phone":      info["phone"],
                                "car_number": info["car_number"],
                                "pin":        info["pin"],
                                "balance":    50000,
                                "status":     "offline"
                            }
                            driver_list_full.append(new_driver)
                            driver_balances[new_driver["id"]] = 50000
                            tg_answer_callback(cq_id, "✅ Одобрено!")
                            tg_notify_approved(info["name"], info["car_number"], info["pin"])
                            print(f"✅ [TG APPROVED] {info['name']} | {info['car_number']}")
                        else:
                            tg_answer_callback(cq_id, "Заявка не найдена")

                    elif data.startswith("reject:"):
                        pin_id = data.split(":", 1)[1]
                        if pin_id in pending_codes:
                            info = pending_codes[pin_id]
                            info["status"] = "rejected"
                            tg_answer_callback(cq_id, "❌ Отклонено")
                            tg_notify_rejected(info["name"], info["car_number"])
                            print(f"❌ [TG REJECTED] {info['name']}")
                        else:
                            tg_answer_callback(cq_id, "Заявка не найдена")

                # Обработка текстовых команд
                elif "message" in upd:
                    msg  = upd["message"]
                    text = msg.get("text", "")

                    if text == "/start":
                        tg_send(
                            "🚕 <b>TAXI 1229 Samarkand</b>\n\n"
                            "Доступные команды:\n"
                            "/status — водители онлайн\n"
                            "/pending — заявки на ПИН\n"
                            "/drivers — все водители\n"
                            "/order НОМЕР Откуда;Куда;Цена — создать заказ\n\n"
                            "Пример:\n"
                            "<code>/order 90T785OA Регистон;Аэропорт;28500</code>"
                        )

                    elif text == "/status":
                        online = sum(1 for d in drivers.values() if d.get("status") == "free")
                        busy   = sum(1 for d in drivers.values() if d.get("status") == "busy")
                        total  = len(drivers)
                        pend   = sum(1 for p in pending_codes.values() if p.get("status") == "pending")
                        tg_send(
                            f"📊 <b>Статус системы</b>\n\n"
                            f"🟢 Свободны: {online}\n"
                            f"🔴 На заказе: {busy}\n"
                            f"📍 Всего онлайн: {total}\n"
                            f"⏳ Ждут ПИН: {pend}"
                        )

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
                        # Формат: /order 90T785OA Откуда;Куда;Цена
                        # Пример: /order 90T785OA Регистон;Аэропорт;28500
                        try:
                            parts = text.split(" ", 2)
                            car   = parts[1].strip()
                            info  = parts[2].split(";")
                            from_addr = info[0].strip()
                            to_addr   = info[1].strip()
                            price     = int(info[2].strip())

                            import urllib.request, json as json_lib
                            payload = json_lib.dumps({
                                "car_number": car,
                                "from_address": from_addr,
                                "to_address": to_addr,
                                "price": price,
                                "client": "Telegram",
                                "distance": "—"
                            }).encode()
                            req = urllib.request.Request(
                                "http://localhost:5000/api/orders/create",
                                data=payload,
                                headers={"Content-Type": "application/json"},
                                method="POST"
                            )
                            urllib.request.urlopen(req, timeout=3)
                            tg_send(f"✅ Заказ создан для {car}\n{from_addr} → {to_addr}\n💰 {price:,} сум")
                        except Exception as e:
                            tg_send(f"❌ Ошибка: {e}\nФормат: /order НОМЕР Откуда;Куда;Цена")

        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)

# Запускаем polling в фоне
threading.Thread(target=tg_polling, daemon=True).start()

# ==================== АДМИН-ПАНЕЛЬ HTML ====================
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
        .status-pending { color: #FF9800; font-weight: bold; }
        .status-approved { color: #4CAF50; font-weight: bold; }
        .no-data { text-align: center; padding: 30px; color: #444; }
        .refresh-bar { text-align: center; padding: 10px; color: #333; font-size: 12px; }
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

    <div class="section">
        <h2>🚗 Активные водители</h2>
        {% if drivers_list %}
        <table>
            <tr><th>Авто</th><th>Водитель</th><th>Телефон</th><th>Статус</th><th>Скорость</th><th>Баланс</th><th>Обновлён</th><th></th></tr>
            {% for did, d in drivers_list %}
            <tr>
                <td><b style="color:#FFD600">{{ d.car_number }}</b></td>
                <td>{{ d.driver_name or '—' }}</td>
                <td>{{ d.phone or '—' }}</td>
                <td>
                    {% if d.status == 'free' %}<span style="color:#4CAF50">● Свободен</span>
                    {% elif d.status == 'busy' %}<span style="color:#FF5252">● На заказе</span>
                    {% else %}<span style="color:#555">● {{ d.status }}</span>{% endif %}
                </td>
                <td>{{ d.speed }} км/ч</td>
                <td style="color:#4CAF50">{{ "{:,}".format(d.balance|int) }} сум</td>
                <td style="color:#444">{{ d.time_str }}</td>
                <td><button class="btn btn-danger" onclick="removeDriver('{{ did }}')">Удалить</button></td>
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
            <tr><th>Имя</th><th>Телефон</th><th>Авто</th><th>ПИН</th><th>Баланс</th></tr>
            {% for d in all_drivers_list %}
            <tr>
                <td>{{ d.name }}</td><td>{{ d.phone }}</td>
                <td><b style="color:#FFD600">{{ d.car_number }}</b></td>
                <td><span class="pin-code">{{ d.pin }}</span></td>
                <td style="color:#4CAF50">{{ "{:,}".format(d.balance|int) }} сум</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}

    <div class="refresh-bar">Авто-обновление каждые 5 секунд</div>

    <script>
        setTimeout(() => location.reload(), 5000);

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
            if (!confirm('Удалить водителя?')) return;
            fetch('/remove_driver', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({driver: did})
            }).then(() => location.reload());
        }
    </script>
</body>
</html>
"""

# ==================== ЭНДПОИНТЫ ====================

@app.route('/api/driver/register', methods=['POST'])
def register_driver():
    data       = request.json
    phone      = data.get('phone', '')
    car_number = data.get('car_number', '')
    name       = data.get('name', 'Новый водитель')
    pin        = str(random.randint(1000, 9999))
    pin_id     = f"pin_{int(time.time())}_{random.randint(100,999)}"

    pending_codes[pin_id] = {
        "phone": phone, "car_number": car_number,
        "name": name, "pin": pin,
        "created_at": time.time(), "status": "pending"
    }

    # Уведомляем в Telegram
    tg_notify_new_pin(pin_id, name, car_number, phone, pin)
    print(f"🔑 [NEW PIN] {name} | {car_number} | PIN: {pin}")
    return jsonify({"success": True, "message": "Заявка отправлена администратору"})


@app.route('/api/admin/pending_codes', methods=['GET'])
def get_pending_codes():
    result = []
    for pid, info in pending_codes.items():
        result.append({
            "pin_id": pid, "phone": info["phone"],
            "car_number": info["car_number"], "name": info["name"],
            "pin": info["pin"], "status": info["status"]
        })
    return jsonify(result)


@app.route('/api/admin/pending_by_car', methods=['GET'])
def pending_by_car():
    result = {}
    for info in pending_codes.values():
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
    if pin_id not in pending_codes:
        return jsonify({"success": False, "error": "Заявка не найдена"}), 404

    info            = pending_codes[pin_id]
    info["status"]  = "approved"

    new_driver = {
        "id": info["car_number"], "name": info["name"],
        "phone": info["phone"],   "car_number": info["car_number"],
        "pin": info["pin"],       "balance": 50000, "status": "offline"
    }
    driver_list_full.append(new_driver)
    driver_balances[new_driver['id']] = 50000

    tg_notify_approved(info["name"], info["car_number"], info["pin"])
    print(f"✅ [APPROVED] {info['name']} | {info['car_number']}")
    return jsonify({"success": True, "driver": new_driver})


@app.route('/api/admin/reject_code', methods=['POST'])
def reject_code():
    data   = request.json
    pin_id = data.get('pin_id', '')
    if pin_id in pending_codes:
        info           = pending_codes[pin_id]
        info["status"] = "rejected"
        tg_notify_rejected(info["name"], info["car_number"])
    return jsonify({"success": True})


@app.route('/api/driver/login', methods=['POST'])
def driver_login():
    data = request.json
    pin  = data.get('pin', '')

    # Ищем среди уже одобренных
    for d in driver_list_full:
        if d.get('pin') == pin:
            return jsonify({
                "success":   True,
                "driver_id": d["id"],
                "name":      d["name"],
                "balance":   driver_balances.get(d["id"], 0)
            })

    # Ищем среди pending — ПИН правильный но ещё не одобрен
    for pin_id, info in pending_codes.items():
        if info.get('pin') == pin and info.get('status') == 'pending':
            info["status"] = "approved"
            new_driver = {
                "id":         info["car_number"],
                "name":       info["name"],
                "phone":      info["phone"],
                "car_number": info["car_number"],
                "pin":        info["pin"],
                "balance":    50000,
                "status":     "offline"
            }
            driver_list_full.append(new_driver)
            driver_balances[new_driver["id"]] = 50000
            tg_notify_approved(info["name"], info["car_number"], info["pin"])
            print(f"✅ [AUTO APPROVED] {info['name']} | {info['car_number']}")
            return jsonify({
                "success":   True,
                "driver_id": new_driver["id"],
                "name":      new_driver["name"],
                "balance":   50000
            })

    return jsonify({"success": False, "error": "Неверный ПИН"}), 401


@app.route('/api/driver/<driver_id>/balance', methods=['GET'])
def get_driver_balance(driver_id):
    if driver_id in drivers:
        return jsonify({"balance": drivers[driver_id].get("balance", 0),
                        "name": drivers[driver_id].get("driver_name", "")})
    for d in driver_list_full:
        if d["id"] == driver_id:
            return jsonify({"balance": driver_balances.get(driver_id, 0), "name": d["name"]})
    return jsonify({"error": "Не найден"}), 404


@app.route('/')
def index():
    free          = sum(1 for d in drivers.values() if d.get('status') == 'free')
    busy          = sum(1 for d in drivers.values() if d.get('status') == 'busy')
    pending_count = sum(1 for p in pending_codes.values() if p.get('status') == 'pending')

    pending_list = sorted([
        {"pin_id": pid, "name": i["name"], "phone": i["phone"],
         "car_number": i["car_number"], "pin": i["pin"], "status": i["status"]}
        for pid, i in pending_codes.items()
    ], key=lambda x: 0 if x['status'] == 'pending' else 1)

    all_drivers_list = [{
        "name": d["name"], "phone": d["phone"], "car_number": d["car_number"],
        "pin": d["pin"], "balance": driver_balances.get(d["id"], 0)
    } for d in driver_list_full]

    return render_template_string(
        ADMIN_HTML,
        current_time     = datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        total_drivers    = len(drivers),
        free_drivers     = free,
        busy_drivers     = busy,
        pending_count    = pending_count,
        registered_count = len(driver_list_full),
        drivers_list     = list(drivers.items()),
        pending_list     = pending_list,
        all_drivers_list = all_drivers_list
    )


@app.route('/api/tariffs', methods=['GET'])
def get_tariffs():
    return jsonify(TARIF_INFO)


@app.route('/api/tariffs', methods=['POST'])
def update_tariffs():
    data = request.get_json(force=True)
    for key in ['base_fare', 'city_rate', 'suburb_rate', 'wait_rate']:
        if key in data:
            TARIF_INFO[key] = int(data[key])
    return jsonify({'status': 'ok', 'tariffs': TARIF_INFO})


@app.route('/location', methods=['POST'])
def location():
    data    = request.get_json(force=True)
    did     = data.get('driver', data.get('car_number', 'unknown'))
    balance = driver_balances.get(did, data.get('balance', 50000))
    driver_balances[did] = balance
    drivers[did] = {
        'lat': data.get('lat', 0), 'lng': data.get('lng', 0),
        'speed': data.get('speed', 0), 'status': data.get('status', 'free'),
        'car_number': data.get('car_number', did), 'balance': balance,
        'phone': data.get('phone', ''), 'driver_name': data.get('driver_name', ''),
        'time_str': datetime.now().strftime('%H:%M:%S'), 'timestamp': time.time()
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
    new_b  = driver_balances.get(did, 50000) + amount
    driver_balances[did] = new_b
    if did in drivers:
        drivers[did]['balance'] = new_b

    # Уведомляем в Telegram если изменение баланса от админа
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


@app.route('/api/orders/create', methods=['POST'])
def create_order():
    """Создать заказ и назначить водителю (из админки или Telegram)"""
    global order_counter
    data        = request.json
    car_number  = data.get('car_number', '')
    from_addr   = data.get('from_address', '')
    to_addr     = data.get('to_address', '')
    distance    = data.get('distance', '—')
    price       = int(data.get('price', 0))
    client      = data.get('client', 'Клиент')
    is_suburb   = data.get('is_suburb', False)

    order_counter += 1
    order_id = f"order_{order_counter}"

    order = {
        "order_id":     order_id,
        "order_num":    order_counter,
        "car_number":   car_number,
        "from_address": from_addr,
        "to_address":   to_addr,
        "distance":     distance,
        "price":        price,
        "client":       client,
        "is_suburb":    is_suburb,
        "status":       "pending",
        "created_at":   time.time()
    }
    orders_db[order_id]       = order
    pending_orders[car_number] = order_id

    # Уведомляем в Telegram
    tg_send(
        f"📦 <b>Заказ #{order_counter}</b> назначен водителю <b>{car_number}</b>\n"
        f"📍 {from_addr} → {to_addr}\n"
        f"💰 {price:,} сум"
    )
    print(f"📦 [ORDER #{order_counter}] {car_number} | {from_addr} → {to_addr} | {price} сум")
    return jsonify({"success": True, "order_id": order_id, "order_num": order_counter})


@app.route('/api/orders/pending', methods=['GET'])
def get_pending_order():
    """Водитель проверяет — есть ли для него новый заказ"""
    car = request.args.get('car', '')
    if car not in pending_orders:
        return jsonify({"has_order": False})

    order_id = pending_orders[car]
    if order_id not in orders_db:
        del pending_orders[car]
        return jsonify({"has_order": False})

    order = orders_db[order_id]
    if order["status"] != "pending":
        del pending_orders[car]
        return jsonify({"has_order": False})

    return jsonify({"has_order": True, **order})


@app.route('/api/orders/respond', methods=['POST'])
def respond_to_order():
    """Водитель принимает или отклоняет заказ"""
    data       = request.json
    order_id   = data.get('order_id', '')
    car        = data.get('car_number', '')
    response_val = data.get('response', '')  # 'accepted' or 'rejected'

    if order_id not in orders_db:
        return jsonify({"success": False, "error": "Заказ не найден"}), 404

    order = orders_db[order_id]
    order["status"] = response_val

    if car in pending_orders:
        del pending_orders[car]

    if response_val == "accepted":
        tg_send(f"✅ Водитель <b>{car}</b> принял заказ #{order['order_num']}")
        print(f"✅ [ORDER ACCEPTED] {car} принял #{order['order_num']}")
    else:
        tg_send(f"❌ Водитель <b>{car}</b> отклонил заказ #{order['order_num']}")
        print(f"❌ [ORDER REJECTED] {car} отклонил #{order['order_num']}")

    return jsonify({"success": True})


@app.route('/api/orders/list', methods=['GET'])
def list_orders():
    """Список всех заказов для админки"""
    return jsonify(list(orders_db.values()))


@app.route('/api/chat/send', methods=['POST'])
def chat_send():
    global chat_counter
    data       = request.json
    car        = data.get('car_number', '')
    driver     = data.get('driver', '')
    text       = data.get('text', '')
    chat_counter += 1
    msg = {
        "id":         chat_counter,
        "car_number": car,
        "from":       car,
        "text":       text,
        "time":       datetime.now().strftime('%H:%M')
    }
    chat_messages.append(msg)
    # Уведомляем диспетчера в Telegram
    tg_send(f"💬 <b>{driver}</b> ({car}):\n{text}")
    return jsonify({"success": True, "id": chat_counter})


@app.route('/api/chat/messages', methods=['GET'])
def chat_get():
    car = request.args.get('car', '')
    # Отдаём сообщения для этой машины (от диспетчера)
    result = [m for m in chat_messages if m['car_number'] == car or m['from'] == 'dispatcher']
    return jsonify(result[-50:])  # последние 50


@app.route('/api/chat/dispatch', methods=['POST'])
def chat_dispatch():
    """Диспетчер отправляет сообщение водителю (из веб-панели или Telegram)"""
    global chat_counter
    data = request.json
    car  = data.get('car_number', '')
    text = data.get('text', '')
    chat_counter += 1
    msg = {
        "id":         chat_counter,
        "car_number": car,
        "from":       "dispatcher",
        "text":       text,
        "time":       datetime.now().strftime('%H:%M')
    }
    chat_messages.append(msg)
    return jsonify({"success": True})


@app.route('/ping')
def ping():
    return jsonify({
        'status':           'alive',
        'drivers_online':   len(drivers),
        'drivers_registered': len(driver_list_full),
        'pending_requests': len([p for p in pending_codes.values() if p['status'] == 'pending'])
    })


if __name__ == '__main__':
    print("=" * 50)
    print("🚕 TAXI 1229 SAMARKAND — SERVER STARTED")
    print(f"🤖 Telegram bot: активен")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False)