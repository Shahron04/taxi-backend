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
ratings_db       = {}
bonuses_db       = {}
revenue_db       = {}   # ✅ НОВОЕ: выручка по водителям
shift_start      = {}   # ✅ НОВОЕ: начало смены
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

def get_rating(car):
    r = ratings_db.get(car, {"total": 0, "count": 0, "orders": 0})
    avg = round(r["total"] / r["count"], 1) if r["count"] > 0 else 5.0
    return {"avg": avg, "count": r["count"], "orders": r["orders"]}

def add_rating(car, stars):
    if car not in ratings_db:
        ratings_db[car] = {"total": 0, "count": 0, "orders": 0}
    ratings_db[car]["total"] += stars
    ratings_db[car]["count"] += 1

def get_bonus(car):
    return bonuses_db.get(car, 0)

def add_bonus(car, points):
    bonuses_db[car] = bonuses_db.get(car, 0) + points

def add_revenue(car, amount):
    revenue_db[car] = revenue_db.get(car, 0) + amount

def get_revenue(car):
    return revenue_db.get(car, 0)

def get_stars(avg):
    if avg >= 4.8: return "⭐⭐⭐⭐⭐"
    if avg >= 4.0: return "⭐⭐⭐⭐"
    if avg >= 3.0: return "⭐⭐⭐"
    if avg >= 2.0: return "⭐⭐"
    return "⭐"

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
    if car_number not in ratings_db:
        ratings_db[car_number] = {"total": 0, "count": 0, "orders": 0}
    ratings_db[car_number]["orders"] += 1
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
                    text_msg = msg.get("text", "")
                    if text_msg == "/start":
                        tg_send(
                            "🚕 <b>TAXI 3042 Xazarasp</b>\n\n"
                            "Доступные команды:\n"
                            "/status — статус системы\n"
                            "/drivers — водители онлайн\n"
                            "/rating — рейтинг водителей\n"
                            "/pending — заявки на ПИН\n"
                            "/orders — активные заказы\n"
                            "/revenue — выручка за смену\n"
                            "/order НОМЕР Откуда;Куда;Цена\n\n"
                            "Пример:\n"
                            "<code>/order 90T785OA Bozor;Aeroport;25000</code>"
                        )
                    elif text_msg == "/status":
                        online = sum(1 for d in drivers.values() if d.get("status") == "free")
                        busy   = sum(1 for d in drivers.values() if d.get("status") == "busy")
                        pend   = sum(1 for p in pending_codes.values() if p.get("status") == "pending")
                        active = sum(1 for o in orders_db if o.get("status") == "pending")
                        total_rev = sum(revenue_db.values())
                        tg_send(
                            f"📊 <b>Статус системы</b>\n\n"
                            f"🟢 Свободны: {online}\n"
                            f"🔴 На заказе: {busy}\n"
                            f"📍 Сейчас онлайн: {len(drivers)}\n"
                            f"👥 Всего водителей: {len(driver_list)}\n"
                            f"📦 Активных заказов: {active}\n"
                            f"⏳ Ждут ПИН: {pend}\n"
                            f"💰 Выручка: {total_rev:,} сум"
                        )
                    elif text_msg == "/revenue":
                        if not revenue_db:
                            tg_send("💰 Нет данных о выручке")
                        else:
                            lines = []
                            for car, rev in sorted(revenue_db.items(), key=lambda x: x[1], reverse=True)[:10]:
                                lines.append(f"🚗 <b>{car}</b>: {rev:,} сум")
                            total = sum(revenue_db.values())
                            tg_send("💰 <b>Выручка за смену:</b>\n\n" + "\n".join(lines) + f"\n\n📊 Итого: <b>{total:,} сум</b>")
                    elif text_msg == "/rating":
                        if not ratings_db:
                            tg_send("📊 Нет данных о рейтинге")
                        else:
                            lines = []
                            sorted_drivers = sorted(
                                ratings_db.items(),
                                key=lambda x: x[1]["total"]/x[1]["count"] if x[1]["count"] > 0 else 0,
                                reverse=True
                            )
                            for i, (car, r) in enumerate(sorted_drivers[:10], 1):
                                avg = round(r["total"]/r["count"], 1) if r["count"] > 0 else 5.0
                                bonus = get_bonus(car)
                                lines.append(
                                    f"{i}. <b>{car}</b>\n"
                                    f"   {get_stars(avg)} {avg} ({r['count']} оценок)\n"
                                    f"   📦 {r['orders']} заказов | 🎁 {bonus} бонусов"
                                )
                            tg_send("🏆 <b>Рейтинг водителей:</b>\n\n" + "\n\n".join(lines))
                    elif text_msg == "/drivers":
                        if not drivers:
                            tg_send("Нет водителей онлайн")
                        else:
                            lines = []
                            for d in drivers.values():
                                icon = "🟢" if d.get("status") == "free" else "🔴"
                                r    = get_rating(d['car_number'])
                                lines.append(
                                    f"{icon} <b>{d['car_number']}</b> — {d.get('driver_name','—')}\n"
                                    f"   ⭐ {r['avg']} | 💰 {get_balance(d['car_number']):,} сум"
                                )
                            tg_send("🚗 <b>Водители онлайн:</b>\n\n" + "\n".join(lines))
                    elif text_msg == "/pending":
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
                    elif text_msg == "/orders":
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
                    elif text_msg.startswith("/order "):
                        try:
                            parts     = text_msg.split(" ", 2)
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

# ==================== ГЛАВНАЯ СТРАНИЦА (SPA) ====================
ADMIN_HTML = """<!DOCTYPE html>
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
/* scrollbar */
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}

/* ── ШАПКА ── */
.topbar{
  position:sticky;top:0;z-index:200;
  display:flex;align-items:center;gap:12px;
  background:rgba(10,10,10,.95);border-bottom:1px solid var(--border);
  padding:0 20px;height:54px;backdrop-filter:blur(10px)
}
.logo{color:var(--gold);font-size:18px;font-weight:700;white-space:nowrap}
.logo span{color:var(--text);font-weight:400;font-size:13px;margin-left:6px}
.topbar-spacer{flex:1}
.pill{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}
.pill-live{background:#0a1f0a;color:var(--green);border:1px solid #1a3a1a}
.pill-tg{background:#001f3d;color:#60a5fa;border:1px solid #1a3a5a}
.pill-time{background:var(--bg2);color:var(--gold);border:1px solid var(--border);font-family:monospace;letter-spacing:.05em}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(1.3)}}

/* ── ТАБЫ ── */
.tabs{display:flex;gap:0;border-bottom:1px solid var(--border);background:var(--bg);padding:0 20px;overflow-x:auto}
.tab{padding:12px 18px;font-size:13px;font-weight:500;color:var(--muted);border:none;background:none;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;transition:all .2s}
.tab:hover{color:var(--text)}
.tab.active{color:var(--gold);border-bottom-color:var(--gold)}
.tab-badge{background:var(--red);color:#fff;padding:1px 5px;border-radius:8px;font-size:10px;margin-left:4px}

/* ── КОНТЕНТ ── */
.page{display:none;padding:20px;max-width:1300px;margin:0 auto}
.page.active{display:block}

/* ── СТАТЫ ── */
.stats-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
.stat{
  flex:1;min-width:110px;background:var(--bg2);border:1px solid var(--border);
  border-radius:var(--r2);padding:16px;cursor:default;transition:border-color .2s
}
.stat:hover{border-color:var(--border2)}
.stat-val{font-size:28px;font-weight:700;color:var(--gold);line-height:1}
.stat-val.green{color:var(--green)}
.stat-val.red{color:var(--red)}
.stat-val.blue{color:var(--blue)}
.stat-val.orange{color:var(--orange)}
.stat-lbl{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-top:6px}

/* ── КАРТОЧКИ/ТАБЛИЦЫ ── */
.card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);margin-bottom:16px;overflow:hidden}
.card-head{display:flex;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid var(--border);background:var(--bg3)}
.card-head h3{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--gold);flex:1}
.card-body{padding:16px}

table{width:100%;border-collapse:collapse}
th{background:var(--bg3);padding:9px 12px;text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;font-weight:500;white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.02)}

/* ── КНОПКИ ── */
.btn{display:inline-flex;align-items:center;gap:4px;padding:5px 11px;border:none;border-radius:7px;cursor:pointer;font-size:12px;font-weight:600;transition:all .15s;white-space:nowrap}
.btn:hover{opacity:.85;transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
.btn-primary{background:var(--gold);color:#000}
.btn-success{background:#14532d;color:var(--green);border:1px solid #166534}
.btn-danger{background:#2a0000;color:var(--red);border:1px solid #4a0000}
.btn-info{background:#0c1a3a;color:var(--blue);border:1px solid #1e3a6a}
.btn-orange{background:#1f1000;color:var(--orange);border:1px solid #3d2000}
.btn-ghost{background:var(--bg3);color:var(--muted);border:1px solid var(--border)}

.btn-lg{padding:9px 20px;font-size:14px;border-radius:var(--r)}
.btn-send{padding:9px 24px;background:var(--gold);color:#000;border:none;border-radius:var(--r);font-size:14px;font-weight:700;cursor:pointer;transition:all .2s}
.btn-send:hover{background:var(--gold2)}
.btn-send:disabled{background:var(--border2);color:var(--muted);cursor:not-allowed;transform:none}

/* ── ФОРМА ЗАКАЗА ── */
.order-form{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);padding:20px;margin-bottom:16px}
.form-row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
.form-group{display:flex;flex-direction:column;gap:5px}
.form-group label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.form-control{
  background:var(--bg);color:var(--text);border:1px solid var(--border2);
  padding:8px 12px;border-radius:var(--r);font-size:13px;outline:none;transition:border-color .2s
}
.form-control:focus{border-color:var(--gold)}
.form-control::placeholder{color:var(--muted2)}
select.form-control option{background:var(--bg2)}

.quick-addr{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.addr-chip{padding:4px 11px;background:var(--bg3);color:var(--muted);border:1px solid var(--border2);border-radius:20px;font-size:12px;cursor:pointer;transition:all .18s}
.addr-chip:hover{background:var(--gold);color:#000;border-color:var(--gold)}

.order-toast{display:none;margin-top:12px;padding:10px 16px;border-radius:var(--r);font-size:13px;font-weight:600}
.order-toast.ok{background:#0a2a0a;color:var(--green);border-left:3px solid var(--green)}
.order-toast.err{background:#2a0a0a;color:var(--red);border-left:3px solid var(--red)}

/* ── СТАТУС ПИЛЮЛИ ── */
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600}
.badge-free{background:#0a2a0a;color:var(--green);border:1px solid #166534}
.badge-busy{background:#2a0a0a;color:var(--red);border:1px solid #7f1d1d}
.badge-pending{background:#2a1a00;color:var(--orange);border:1px solid #7c2d12}
.badge-ok{background:#0a2a0a;color:var(--green)}
.badge-rejected{background:#2a0a0a;color:var(--red)}
.car-num{color:var(--gold);font-weight:700;font-family:monospace;font-size:13px}
.pin-code{color:var(--gold);font-weight:700;font-family:monospace;font-size:18px;letter-spacing:.15em}

/* ── ЧАТ ── */
.chat-wrap{display:flex;flex-direction:column;height:400px}
.chat-messages{flex:1;overflow-y:auto;padding:12px;background:var(--bg);border-radius:var(--r);margin-bottom:10px;display:flex;flex-direction:column;gap:6px}
.chat-msg{padding:8px 12px;border-radius:10px;max-width:70%;font-size:13px;line-height:1.4}
.chat-msg.own{background:var(--gold);color:#000;align-self:flex-end;border-bottom-right-radius:3px}
.chat-msg.other{background:var(--bg4);color:var(--text);align-self:flex-start;border-bottom-left-radius:3px}
.chat-msg .msg-meta{font-size:10px;opacity:.6;margin-top:3px}
.chat-input-row{display:flex;gap:8px}
.chat-input-row input{flex:1}
.quick-replies{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.qr-btn{padding:5px 11px;background:var(--bg3);color:var(--muted);border:1px solid var(--border2);border-radius:20px;font-size:11px;cursor:pointer;transition:all .18s}
.qr-btn:hover{background:var(--gold);color:#000;border-color:var(--gold)}

/* ── РЕЙТИНГ ── */
.driver-card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);padding:14px 16px;margin-bottom:10px;display:flex;align-items:center;gap:14px;transition:border-color .2s}
.driver-card:hover{border-color:var(--border2)}
.rank-num{font-size:22px;font-weight:700;color:var(--muted2);min-width:30px;text-align:center}
.rank-num.gold{color:#FFD600}
.rank-num.silver{color:#a8a8a8}
.rank-num.bronze{color:#cd7f32}
.dc-info{flex:1}
.dc-car{color:var(--gold);font-weight:700;font-size:15px;font-family:monospace}
.dc-sub{color:var(--muted);font-size:12px;margin-top:2px}
.dc-rating{text-align:right;min-width:60px}
.dc-avg{font-size:22px;font-weight:700;color:var(--gold)}
.rating-bar{background:var(--bg3);border-radius:3px;height:5px;width:120px;margin-top:5px;overflow:hidden}
.rating-fill{background:var(--gold);height:100%;border-radius:3px;transition:width .4s}
.bonus-pill{display:inline-flex;align-items:center;gap:3px;background:#1a1100;color:var(--gold);border:1px solid #3d2800;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600}

/* ── ТАРИФЫ ── */
.tariff-grid{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.tariff-card{flex:1;min-width:120px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);padding:16px;text-align:center}
.tariff-val{font-size:24px;font-weight:700;color:var(--gold)}
.tariff-lbl{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-top:4px}

/* ── ВЫРУЧКА ── */
.rev-bar-wrap{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.rev-bar-bg{flex:1;background:var(--bg3);border-radius:3px;height:8px;overflow:hidden}
.rev-bar-fill{height:100%;background:linear-gradient(90deg,var(--gold2),var(--gold));border-radius:3px;transition:width .5s}

/* ── МОДАЛ ── */
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:500;align-items:center;justify-content:center;padding:20px}
.modal-bg.open{display:flex}
.modal{background:var(--bg2);border:1px solid var(--border2);border-radius:var(--r3);padding:24px;width:100%;max-width:440px;position:relative}
.modal h3{color:var(--gold);font-size:17px;margin-bottom:16px}
.modal-close{position:absolute;top:14px;right:16px;background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer;line-height:1;padding:2px 6px;border-radius:5px;transition:background .15s}
.modal-close:hover{background:var(--bg4)}
.info-row{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--border);font-size:13px}
.info-row:last-child{border:none}
.info-lbl{color:var(--muted)}
.info-val{font-weight:600}
.modal-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
.modal-actions .btn{flex:1;justify-content:center}

/* ── УВЕДОМЛЕНИЯ ── */
.notifications{position:fixed;top:64px;right:16px;z-index:1000;display:flex;flex-direction:column;gap:8px;pointer-events:none}
.notif{background:var(--bg2);border:1px solid var(--border2);border-radius:var(--r);padding:12px 16px;font-size:13px;max-width:300px;pointer-events:all;animation:slideIn .3s ease;box-shadow:0 4px 24px rgba(0,0,0,.5)}
.notif.notif-success{border-left:3px solid var(--green)}
.notif.notif-warning{border-left:3px solid var(--orange)}
.notif.notif-info{border-left:3px solid var(--blue)}
@keyframes slideIn{from{transform:translateX(120%);opacity:0}to{transform:translateX(0);opacity:1}}
@keyframes slideOut{from{transform:translateX(0);opacity:1}to{transform:translateX(120%);opacity:0}}
.notif.removing{animation:slideOut .3s ease forwards}

/* ── НЕТ ДАННЫХ ── */
.empty{text-align:center;padding:40px 20px;color:var(--muted2)}
.empty-icon{font-size:36px;margin-bottom:10px}

/* ── АДАПТИВ ── */
@media(max-width:600px){
  .topbar{padding:0 12px}
  .page{padding:12px}
  .form-row{flex-direction:column}
  .stats-row .stat{min-width:calc(50% - 6px)}
  .btn-lg{width:100%;justify-content:center}
}
</style>
</head>
<body>

<!-- ШАПКА -->
<div class="topbar">
  <div class="logo">🚕 TAXI 3042 <span>XAZARASP</span></div>
  <div class="topbar-spacer"></div>
  <div class="pill pill-time" id="clock">00:00:00</div>
  <div class="pill pill-live"><span class="dot"></span>Live</div>
  <div class="pill pill-tg">🤖 TG</div>
</div>

<!-- ТАБЫ -->
<div class="tabs">
  <button class="tab active" onclick="switchTab('dash')">📊 Дашборд</button>
  <button class="tab" onclick="switchTab('order')">📦 Заказы <span class="tab-badge" id="badge-orders" style="display:none"></span></button>
  <button class="tab" onclick="switchTab('drivers')">🚗 Водители</button>
  <button class="tab" onclick="switchTab('rating')">🏆 Рейтинг</button>
  <button class="tab" onclick="switchTab('chat')">💬 Чат</button>
  <button class="tab" onclick="switchTab('finance')">💰 Финансы</button>
  <button class="tab" onclick="switchTab('settings')">⚙️ Настройки</button>
</div>

<!-- УВЕДОМЛЕНИЯ -->
<div class="notifications" id="notifBox"></div>

<!-- ═══════════════════════════════ ДАШБОРД ═══════════════════════════════ -->
<div class="page active" id="page-dash">
  <div class="stats-row">
    <div class="stat"><div class="stat-val" id="s-online">0</div><div class="stat-lbl">На линии</div></div>
    <div class="stat"><div class="stat-val green" id="s-free">0</div><div class="stat-lbl">Свободны</div></div>
    <div class="stat"><div class="stat-val red" id="s-busy">0</div><div class="stat-lbl">На заказе</div></div>
    <div class="stat"><div class="stat-val orange" id="s-pending">0</div><div class="stat-lbl">Ждут ПИН</div></div>
    <div class="stat"><div class="stat-val" id="s-total">0</div><div class="stat-lbl">Всего водит.</div></div>
    <div class="stat"><div class="stat-val blue" id="s-orders">0</div><div class="stat-lbl">Активн. заказов</div></div>
    <div class="stat"><div class="stat-val green" id="s-revenue">0</div><div class="stat-lbl">Выручка (сум)</div></div>
  </div>

  <!-- Быстрые действия -->
  <div class="card">
    <div class="card-head"><h3>⚡ Быстрые действия</h3></div>
    <div class="card-body" style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn btn-primary btn-lg" onclick="switchTab('order')">📦 Новый заказ</button>
      <button class="btn btn-info btn-lg" onclick="switchTab('chat')">💬 Открыть чат</button>
      <button class="btn btn-ghost btn-lg" onclick="window.open('/map')">🗺 Карта</button>
      <button class="btn btn-ghost btn-lg" onclick="exportReport()">📥 Экспорт отчёта</button>
      <button class="btn btn-danger btn-lg" onclick="resetRevenue()">🔄 Сбросить смену</button>
    </div>
  </div>

  <!-- Водители онлайн (компактно) -->
  <div class="card">
    <div class="card-head">
      <h3>🚗 Водители онлайн</h3>
      <span id="last-upd" style="color:var(--muted2);font-size:11px"></span>
    </div>
    <div style="overflow-x:auto">
      <table id="tbl-drivers">
        <thead>
          <tr>
            <th>Авто</th><th>Водитель</th><th>Статус</th>
            <th>Скорость</th><th>Баланс</th><th>Рейтинг</th><th>Действия</th>
          </tr>
        </thead>
        <tbody id="tbody-drivers">
          <tr><td colspan="7" class="empty"><div class="empty-icon">🚗</div>Водители выйдут на линию</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Активные заказы -->
  <div class="card" id="card-active-orders" style="display:none">
    <div class="card-head"><h3>📦 Активные заказы</h3></div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>#</th><th>Водитель</th><th>Откуда</th><th>Куда</th><th>Цена</th><th>Клиент</th><th>Статус</th><th>Время</th></tr></thead>
        <tbody id="tbody-active-orders"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- ═══════════════════════════════ ЗАКАЗЫ ═══════════════════════════════ -->
<div class="page" id="page-order">
  <div class="order-form">
    <div class="form-row">
      <div class="form-group">
        <label>Водитель</label>
        <select class="form-control" id="o-car" style="width:220px">
          <option value="">— Выбрать —</option>
          <option value="ALL">📢 Всем свободным</option>
        </select>
      </div>
      <div class="form-group">
        <label>Откуда</label>
        <input class="form-control" id="o-from" placeholder="Адрес подачи" style="width:180px">
      </div>
      <div class="form-group">
        <label>Куда</label>
        <input class="form-control" id="o-to" placeholder="Адрес назначения" style="width:180px">
      </div>
      <div class="form-group">
        <label>Цена (сум)</label>
        <input class="form-control" id="o-price" type="number" placeholder="0" style="width:130px" min="0" step="500">
      </div>
      <div class="form-group">
        <label>Клиент</label>
        <input class="form-control" id="o-client" placeholder="Имя/телефон" style="width:150px">
      </div>
      <div class="form-group">
        <label>&nbsp;</label>
        <button class="btn-send" id="btn-send-order" onclick="createOrder()">🚀 Отправить</button>
      </div>
    </div>
    <div class="quick-addr">
      <span style="color:var(--muted2);font-size:11px;text-transform:uppercase;letter-spacing:.06em">Быстро:</span>
      <button class="addr-chip" onclick="setAddr('Bozor')">📍 Bozor</button>
      <button class="addr-chip" onclick="setAddr('Aeroport')">✈️ Aeroport</button>
      <button class="addr-chip" onclick="setAddr('Kasalxona')">🏥 Kasalxona</button>
      <button class="addr-chip" onclick="setAddr('Vokzal')">🚉 Vokzal</button>
      <button class="addr-chip" onclick="setAddr('Maktab')">🏫 Maktab</button>
      <button class="addr-chip" onclick="setAddr('Markaziy bozor')">🛒 Markaziy</button>
      <button class="addr-chip" onclick="setAddr('Poliklinika')">💊 Poliklinika</button>
      <button class="addr-chip" onclick="setAddr('Do\'kon')">🏪 Do\'kon</button>
    </div>
    <div class="order-toast" id="o-toast"></div>
  </div>

  <!-- История заказов -->
  <div class="card">
    <div class="card-head"><h3>📋 История заказов</h3><button class="btn btn-ghost" onclick="loadOrders()">↻ Обновить</button></div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>#</th><th>Водитель</th><th>Откуда → Куда</th><th>Цена</th><th>Клиент</th><th>Статус</th><th>Время</th></tr></thead>
        <tbody id="tbody-orders"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- ═══════════════════════════════ ВОДИТЕЛИ ═══════════════════════════════ -->
<div class="page" id="page-drivers">
  <!-- Заявки на ПИН -->
  <div class="card" id="card-pins">
    <div class="card-head">
      <h3>🔑 Заявки на регистрацию</h3>
      <span id="pin-count" style="background:var(--orange);color:#000;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700"></span>
    </div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Имя</th><th>Телефон</th><th>Авто</th><th>ПИН</th><th>Статус</th><th>Действия</th></tr></thead>
        <tbody id="tbody-pins"></tbody>
      </table>
    </div>
  </div>

  <!-- Заявки на баланс -->
  <div class="card" id="card-bal-reqs">
    <div class="card-head"><h3>💳 Заявки на пополнение</h3></div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Авто</th><th>Сумма</th><th>Статус</th><th>Действия</th></tr></thead>
        <tbody id="tbody-bal-reqs"></tbody>
      </table>
    </div>
  </div>

  <!-- Все зарегистрированные -->
  <div class="card">
    <div class="card-head"><h3>📋 Все водители</h3></div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Имя</th><th>Телефон</th><th>Авто</th><th>ПИН</th><th>Баланс</th><th>Рейтинг</th><th>Выручка</th><th>Действия</th></tr></thead>
        <tbody id="tbody-all-drivers"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- ═══════════════════════════════ РЕЙТИНГ ═══════════════════════════════ -->
<div class="page" id="page-rating">
  <div id="rating-list"></div>
</div>

<!-- ═══════════════════════════════ ЧАТ ═══════════════════════════════ -->
<div class="page" id="page-chat">
  <div style="display:flex;gap:16px;flex-wrap:wrap">
    <div style="flex:0 0 220px">
      <div class="card">
        <div class="card-head"><h3>Водители</h3></div>
        <div id="chat-driver-list" style="padding:8px"></div>
      </div>
    </div>
    <div style="flex:1;min-width:280px">
      <div class="card" id="chat-area" style="display:none">
        <div class="card-head">
          <h3 id="chat-title">Чат</h3>
          <button class="btn btn-ghost" onclick="closeChat()">✕ Закрыть</button>
        </div>
        <div class="card-body" style="padding:12px">
          <div class="chat-wrap">
            <div class="chat-messages" id="chat-msgs"></div>
            <div class="chat-input-row">
              <input class="form-control" id="chat-text" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sendChat()">
              <button class="btn btn-primary" onclick="sendChat()">📤</button>
            </div>
            <div class="quick-replies">
              <button class="qr-btn" onclick="sendQuick('✅ Принято')">Принято</button>
              <button class="qr-btn" onclick="sendQuick('⏳ Подождите')">Подождите</button>
              <button class="qr-btn" onclick="sendQuick('📦 Есть заказ!')">Есть заказ</button>
              <button class="qr-btn" onclick="sendQuick('🟢 Вы свободны')">Свободны</button>
              <button class="qr-btn" onclick="sendQuick('🚦 Выезжайте на линию!')">На линию</button>
              <button class="qr-btn" onclick="sendQuick('⚠️ Клиент ждёт!')">Клиент ждёт</button>
              <button class="qr-btn" onclick="sendQuick('👍 Хорошей смены!')">Хорошей смены</button>
            </div>
          </div>
        </div>
      </div>
      <div class="card" id="chat-empty-state">
        <div class="card-body empty"><div class="empty-icon">💬</div>Выберите водителя слева</div>
      </div>
    </div>
  </div>
</div>

<!-- ═══════════════════════════════ ФИНАНСЫ ═══════════════════════════════ -->
<div class="page" id="page-finance">
  <div class="stats-row">
    <div class="stat"><div class="stat-val green" id="f-total">0</div><div class="stat-lbl">Итого выручка</div></div>
    <div class="stat"><div class="stat-val" id="f-orders">0</div><div class="stat-lbl">Завершено заказов</div></div>
    <div class="stat"><div class="stat-val blue" id="f-avg">0</div><div class="stat-lbl">Средний чек</div></div>
  </div>
  <div class="card">
    <div class="card-head"><h3>💰 Выручка по водителям</h3><button class="btn btn-ghost" onclick="loadFinance()">↻</button></div>
    <div class="card-body" id="rev-list"></div>
  </div>
</div>

<!-- ═══════════════════════════════ НАСТРОЙКИ ═══════════════════════════════ -->
<div class="page" id="page-settings">
  <div class="card">
    <div class="card-head"><h3>💰 Тарифы</h3></div>
    <div class="card-body">
      <div class="tariff-grid" id="tariff-display"></div>
      <div class="form-row">
        <div class="form-group">
          <label>Посадка (сум)</label>
          <input class="form-control" id="t-base" type="number" style="width:130px">
        </div>
        <div class="form-group">
          <label>Город (сум/км)</label>
          <input class="form-control" id="t-city" type="number" style="width:130px">
        </div>
        <div class="form-group">
          <label>Загород (сум/км)</label>
          <input class="form-control" id="t-suburb" type="number" style="width:130px">
        </div>
        <div class="form-group">
          <label>Ожидание (сум/мин)</label>
          <input class="form-control" id="t-wait" type="number" style="width:130px">
        </div>
        <div class="form-group">
          <label>&nbsp;</label>
          <button class="btn-send" onclick="saveTariffs()">💾 Сохранить</button>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><h3>🔔 Уведомления</h3></div>
    <div class="card-body" style="display:flex;flex-direction:column;gap:12px">
      <label style="display:flex;align-items:center;gap:10px;cursor:pointer;font-size:14px">
        <input type="checkbox" id="notif-sound" checked>
        Звуковые уведомления о новых заказах
      </label>
      <label style="display:flex;align-items:center;gap:10px;cursor:pointer;font-size:14px">
        <input type="checkbox" id="notif-new-driver" checked>
        Уведомления о новых водителях онлайн
      </label>
    </div>
  </div>
</div>

<!-- МОДАЛ ИНФОРМАЦИЯ О ВОДИТЕЛЕ -->
<div class="modal-bg" id="driver-modal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal()">✕</button>
    <h3>🚗 <span id="m-car"></span></h3>
    <div class="info-row"><span class="info-lbl">Водитель</span><span class="info-val" id="m-name"></span></div>
    <div class="info-row"><span class="info-lbl">Телефон</span><span class="info-val" id="m-phone"></span></div>
    <div class="info-row"><span class="info-lbl">Статус</span><span class="info-val" id="m-status"></span></div>
    <div class="info-row"><span class="info-lbl">Баланс</span><span class="info-val" id="m-balance"></span></div>
    <div class="info-row"><span class="info-lbl">Скорость</span><span class="info-val" id="m-speed"></span></div>
    <div class="info-row"><span class="info-lbl">Рейтинг</span><span class="info-val" id="m-rating"></span></div>
    <div class="info-row"><span class="info-lbl">Бонусы</span><span class="info-val" id="m-bonus"></span></div>
    <div class="info-row"><span class="info-lbl">Выручка сегодня</span><span class="info-val" id="m-revenue"></span></div>
    <div class="modal-actions">
      <button class="btn btn-info" onclick="closeModal();openChat(modalCar)">💬 Чат</button>
      <button class="btn btn-success" onclick="closeModal();doAddBalance(modalCar)">💰 Баланс</button>
      <button class="btn btn-orange" onclick="closeModal();doAddBonus(modalCar)">🎁 Бонус</button>
      <button class="btn btn-primary" onclick="closeModal();quickOrder(modalCar)">📦 Заказ</button>
      <button class="btn btn-danger" onclick="closeModal();doRemove(modalCar)">🗑 Убрать</button>
    </div>
  </div>
</div>

<script>
// ═══════════════ СОСТОЯНИЕ ═══════════════
let state = {
  drivers: {},
  ratings: {},
  bonuses: {},
  revenue: {},
  orders: [],
  pins: [],
  balReqs: [],
  allDrivers: [],
  tariffs: {},
  pendingCount: 0,
  activeOrders: 0,
};
let currentCar = '';
let modalCar = '';
let chatInterval = null;
let chatSeenIds = new Set();
let prevDriverSet = new Set();
let prevActiveOrders = 0;

// ═══════════════ ЧАСЫ ═══════════════
(function clock(){
  const el = document.getElementById('clock');
  function tick(){
    const d=new Date();
    el.textContent=[d.getHours(),d.getMinutes(),d.getSeconds()].map(n=>String(n).padStart(2,'0')).join(':');
  }
  tick(); setInterval(tick,1000);
})();

// ═══════════════ УВЕДОМЛЕНИЯ ═══════════════
function notify(msg, type='info'){
  const box = document.getElementById('notifBox');
  const el = document.createElement('div');
  el.className = `notif notif-${type}`;
  el.innerHTML = msg;
  el.onclick = () => el.remove();
  box.appendChild(el);
  setTimeout(()=>{ el.classList.add('removing'); setTimeout(()=>el.remove(),300); },4000);
}

function beep(){
  if(!document.getElementById('notif-sound')?.checked) return;
  try{
    const ctx=new(window.AudioContext||window.webkitAudioContext)();
    const o=ctx.createOscillator();
    const g=ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.frequency.value=880; o.type='sine';
    g.gain.setValueAtTime(.3,ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(.001,ctx.currentTime+.4);
    o.start(ctx.currentTime); o.stop(ctx.currentTime+.4);
  }catch(e){}
}

// ═══════════════ ТАБЫ ═══════════════
function switchTab(id){
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  const pages=['dash','order','drivers','rating','chat','finance','settings'];
  const idx=pages.indexOf(id);
  if(idx>=0) document.querySelectorAll('.tab')[idx].classList.add('active');
  document.getElementById('page-'+id)?.classList.add('active');
  if(id==='order') loadOrderHistory();
  if(id==='drivers') loadDriversPage();
  if(id==='rating') loadRating();
  if(id==='chat') loadChatDriverList();
  if(id==='finance') loadFinance();
  if(id==='settings') loadTariffs();
}

// ═══════════════ LIVE ОБНОВЛЕНИЕ ═══════════════
async function liveUpdate(){
  try{
    const [drvRes, orderRes] = await Promise.all([
      fetch('/api/drivers').then(r=>r.json()),
      fetch('/api/orders/list').then(r=>r.json()),
    ]);
    state.drivers = drvRes;
    state.orders = orderRes;

    const keys = Object.keys(drvRes);
    const free = keys.filter(k=>drvRes[k].status==='free').length;
    const busy = keys.filter(k=>drvRes[k].status==='busy').length;
    const activeOrds = orderRes.filter(o=>o.status==='pending'||o.status==='accepted').length;

    // Уведомления о новых водителях
    const newSet = new Set(keys);
    if(document.getElementById('notif-new-driver')?.checked){
      for(const k of newSet){
        if(!prevDriverSet.has(k)){
          notify(`🟢 <b>${drvRes[k].car_number||k}</b> вышел на линию`, 'info');
        }
      }
    }
    prevDriverSet = newSet;

    // Уведомления о новых заказах
    if(activeOrds > prevActiveOrders && prevActiveOrders > 0){
      beep();
      notify(`📦 Новый заказ! Активных: ${activeOrds}`, 'warning');
    }
    prevActiveOrders = activeOrds;

    // Обновляем статы
    document.getElementById('s-online').textContent = keys.length;
    document.getElementById('s-free').textContent = free;
    document.getElementById('s-busy').textContent = busy;
    document.getElementById('s-orders').textContent = activeOrds;

    // Заказы статбейдж
    const badge = document.getElementById('badge-orders');
    badge.style.display = activeOrds ? '' : 'none';
    badge.textContent = activeOrds;

    document.getElementById('last-upd').textContent = 'Обновлено: ' + new Date().toLocaleTimeString();

    renderDriversTable();
    renderActiveOrders();
    updateDriverSelect();
  }catch(e){}

  // Отдельно — считаем ожидающие ПИН и баланс
  try{
    const pins = await fetch('/api/admin/pending_codes').then(r=>r.json());
    state.pins = pins;
    const pCount = pins.filter(p=>p.status==='pending').length;
    state.pendingCount = pCount;
    document.getElementById('s-pending').textContent = pCount;
  }catch(e){}

  // Выручка
  try{
    const rev = await fetch('/api/stats/revenue').then(r=>r.json());
    state.revenue = rev;
    const total = Object.values(rev).reduce((a,b)=>a+b,0);
    document.getElementById('s-revenue').textContent = fmt(total);
  }catch(e){}

  // Рейтинги
  try{
    state.ratings = await fetch('/api/stats/ratings').then(r=>r.json());
    state.bonuses = await fetch('/api/stats/bonuses').then(r=>r.json());
    state.allDrivers = await fetch('/api/stats/all_drivers').then(r=>r.json());
    document.getElementById('s-total').textContent = state.allDrivers.length;
  }catch(e){}
}
setInterval(liveUpdate, 3000);
liveUpdate();

// ═══════════════ ФОРМАТИРОВАНИЕ ═══════════════
function fmt(n){ return Number(n||0).toLocaleString('ru') }
function ts(t){ return t ? new Date(t*1000).toLocaleTimeString('ru',{hour:'2-digit',minute:'2-digit'}) : '—' }

// ═══════════════ ТАБЛИЦА ВОДИТЕЛЕЙ (ДАШБОРД) ═══════════════
function renderDriversTable(){
  const tbody = document.getElementById('tbody-drivers');
  const keys = Object.keys(state.drivers);
  if(!keys.length){
    tbody.innerHTML = `<tr><td colspan="7" class="empty"><div class="empty-icon">🚗</div>Водители выйдут на линию</td></tr>`;
    return;
  }
  tbody.innerHTML = keys.map(k=>{
    const d = state.drivers[k];
    const r = state.ratings[d.car_number] || {avg:5.0, count:0};
    const statusBadge = d.status==='free'
      ? `<span class="badge badge-free">🟢 Свободен</span>`
      : `<span class="badge badge-busy">🔴 На заказе</span>`;
    return `<tr>
      <td><span class="car-num">${d.car_number||k}</span></td>
      <td>${d.driver_name||'—'}<br><small style="color:var(--muted)">${d.phone||''}</small></td>
      <td>${statusBadge}</td>
      <td>${d.speed||0} км/ч</td>
      <td style="color:var(--green);font-weight:600">${fmt(d.balance)} сум</td>
      <td>⭐ ${r.avg} <small style="color:var(--muted)">(${r.count})</small></td>
      <td style="white-space:nowrap">
        <button class="btn btn-primary" onclick="quickOrder('${escQ(d.car_number||k)}')">📦</button>
        <button class="btn btn-info" onclick="openChat('${escQ(d.car_number||k)}')">💬</button>
        <button class="btn btn-success" onclick="doAddBalance('${escQ(d.car_number||k)}')">💰</button>
        <button class="btn btn-orange" onclick="doAddBonus('${escQ(d.car_number||k)}')">🎁</button>
        <button class="btn btn-ghost" onclick="showModal('${escQ(d.car_number||k)}','${escQ(d.driver_name||'')}','${escQ(d.phone||'')}','${d.status||''}',${d.balance||0},${d.speed||0},${r.avg||5},${state.bonuses[d.car_number]||0},${state.revenue[d.car_number]||0})">ℹ️</button>
        <button class="btn btn-danger" onclick="doRemove('${escQ(d.car_number||k)}')">🗑</button>
      </td>
    </tr>`;
  }).join('');
}

function renderActiveOrders(){
  const active = state.orders.filter(o=>o.status==='pending'||o.status==='accepted');
  const card = document.getElementById('card-active-orders');
  card.style.display = active.length ? '' : 'none';
  const tbody = document.getElementById('tbody-active-orders');
  tbody.innerHTML = active.map(o=>{
    const st = o.status==='pending'
      ? `<span class="badge badge-pending">⏳ Ожидает</span>`
      : `<span class="badge badge-ok">✅ Принят</span>`;
    const age = Math.floor((Date.now()/1000 - o.created_at)/60);
    return `<tr>
      <td style="color:var(--muted2)">${o.order_num}</td>
      <td><span class="car-num">${o.car_number}</span></td>
      <td>${esc(o.from_address)}</td>
      <td>${esc(o.to_address)}</td>
      <td style="color:var(--green);font-weight:600">${fmt(o.price)} сум</td>
      <td>${esc(o.client)}</td>
      <td>${st}</td>
      <td style="color:var(--muted)">${age}мин назад</td>
    </tr>`;
  }).join('');
}

// ═══════════════ ВЫБОР ВОДИТЕЛЯ В ФОРМЕ ═══════════════
function updateDriverSelect(){
  const sel = document.getElementById('o-car');
  const cur = sel.value;
  const opts = ['<option value="">— Выбрать —</option><option value="ALL">📢 Всем свободным</option>'];
  Object.values(state.drivers).forEach(d=>{
    const icon = d.status==='free' ? '🟢' : '🔴';
    opts.push(`<option value="${escAttr(d.car_number)}">${icon} ${esc(d.car_number)} — ${esc(d.driver_name||'—')}</option>`);
  });
  sel.innerHTML = opts.join('');
  sel.value = cur;
}

// ═══════════════ СОЗДАНИЕ ЗАКАЗА ═══════════════
function setAddr(addr){
  const f = document.getElementById('o-from');
  const t = document.getElementById('o-to');
  if(!f.value) f.value = addr;
  else if(!t.value) t.value = addr;
  else { f.value = addr; t.value = ''; }
}

function quickOrder(car){
  switchTab('order');
  setTimeout(()=>{
    document.getElementById('o-car').value = car;
    document.getElementById('o-from').focus();
  },100);
}

function showOrderToast(msg, ok){
  const el = document.getElementById('o-toast');
  el.textContent = msg;
  el.className = 'order-toast ' + (ok ? 'ok' : 'err');
  el.style.display = 'block';
  setTimeout(()=>el.style.display='none', 5000);
}

async function createOrder(){
  const car    = document.getElementById('o-car').value;
  const from   = document.getElementById('o-from').value.trim();
  const to     = document.getElementById('o-to').value.trim();
  const price  = parseInt(document.getElementById('o-price').value)||0;
  const client = document.getElementById('o-client').value.trim()||'Клиент';

  if(!car)    return showOrderToast('❌ Выберите водителя!', false);
  if(!from)   return showOrderToast('❌ Введите откуда!', false);
  if(!to)     return showOrderToast('❌ Введите куда!', false);
  if(price<=0)return showOrderToast('❌ Укажите корректную цену!', false);

  const btn = document.getElementById('btn-send-order');
  btn.disabled = true; btn.textContent = '⏳...';

  try{
    const url = car==='ALL' ? '/api/orders/broadcast' : '/api/orders/create';
    const res = await fetch(url,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({car_number:car,from_address:from,to_address:to,price,client})
    }).then(r=>r.json());

    if(res.success){
      showOrderToast('✅ Заказ отправлен!' + (res.sent_to ? ` (${res.sent_to} водителям)` : ''), true);
      document.getElementById('o-from').value='';
      document.getElementById('o-to').value='';
      document.getElementById('o-price').value='';
      document.getElementById('o-client').value='';
      loadOrderHistory();
    } else {
      showOrderToast('❌ ' + (res.error||'Ошибка'), false);
    }
  }catch(e){ showOrderToast('❌ Нет соединения', false); }
  finally{ btn.disabled=false; btn.textContent='🚀 Отправить'; }
}

// ═══════════════ ИСТОРИЯ ЗАКАЗОВ ═══════════════
async function loadOrderHistory(){
  try{
    const orders = await fetch('/api/orders/list').then(r=>r.json());
    const tbody = document.getElementById('tbody-orders');
    if(!orders.length){
      tbody.innerHTML = `<tr><td colspan="7" class="empty">Нет заказов</td></tr>`; return;
    }
    tbody.innerHTML = orders.slice(0,50).map(o=>{
      const st = {pending:'<span class="badge badge-pending">⏳</span>',
                  accepted:'<span class="badge badge-ok">✅</span>',
                  cancelled:'<span class="badge badge-rejected">❌</span>',
                  rejected:'<span class="badge badge-rejected">🚫</span>'}[o.status]||o.status;
      return `<tr>
        <td style="color:var(--muted2)">${o.order_num}</td>
        <td><span class="car-num">${o.car_number}</span></td>
        <td>${esc(o.from_address)} → ${esc(o.to_address)}</td>
        <td style="color:var(--green);font-weight:600">${fmt(o.price)} сум</td>
        <td>${esc(o.client)}</td>
        <td>${st}</td>
        <td style="color:var(--muted)">${ts(o.created_at)}</td>
      </tr>`;
    }).join('');
  }catch(e){}
}
function loadOrders(){ loadOrderHistory(); }

// ═══════════════ СТРАНИЦА ВОДИТЕЛЕЙ ═══════════════
async function loadDriversPage(){
  // ПИН заявки
  const pins = state.pins;
  const pendPins = pins.filter(p=>p.status==='pending');
  document.getElementById('pin-count').textContent = pendPins.length ? `${pendPins.length} новых` : '';
  document.getElementById('card-pins').style.display = pins.length ? '' : 'none';

  document.getElementById('tbody-pins').innerHTML = pins.map(p=>{
    const st = {pending:`<span class="badge badge-pending">⏳ Ожидает</span>`,
                approved:`<span class="badge badge-ok">✅ Одобрен</span>`,
                rejected:`<span class="badge badge-rejected">❌</span>`}[p.status]||p.status;
    const actions = p.status==='pending' ? `
      <button class="btn btn-success" onclick="approvePin('${escQ(p.pin_id)}')">✅ Одобрить</button>
      <button class="btn btn-danger" onclick="rejectPin('${escQ(p.pin_id)}')">❌ Отказ</button>
    ` : '—';
    return `<tr>
      <td>${esc(p.name)}</td>
      <td>${esc(p.phone)}</td>
      <td><span class="car-num">${esc(p.car_number)}</span></td>
      <td><span class="pin-code">${p.pin}</span></td>
      <td>${st}</td>
      <td>${actions}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="6" class="empty">Нет заявок</td></tr>`;

  // Заявки на баланс
  try{
    const bReqs = await fetch('/api/admin/balance_requests').then(r=>r.json());
    state.balReqs = bReqs;
    document.getElementById('card-bal-reqs').style.display = bReqs.length ? '' : 'none';
    document.getElementById('tbody-bal-reqs').innerHTML = bReqs.map(r=>{
      const st = {pending:`<span class="badge badge-pending">⏳</span>`,
                  approved:`<span class="badge badge-ok">✅</span>`,
                  rejected:`<span class="badge badge-rejected">❌</span>`}[r.status]||r.status;
      const acts = r.status==='pending' ? `
        <button class="btn btn-success" onclick="approveBalance(${r.id})">✅</button>
        <button class="btn btn-danger" onclick="rejectBalance(${r.id})">❌</button>
      ` : '—';
      return `<tr>
        <td><span class="car-num">${esc(r.car_number)}</span></td>
        <td style="color:var(--green);font-weight:600">${fmt(r.amount)} сум</td>
        <td>${st}</td>
        <td>${acts}</td>
      </tr>`;
    }).join('') || `<tr><td colspan="4" class="empty">Нет заявок</td></tr>`;
  }catch(e){}

  // Все водители
  const all = state.allDrivers;
  document.getElementById('tbody-all-drivers').innerHTML = all.map(d=>{
    const r = state.ratings[d.car_number]||{avg:5.0,count:0};
    const rev = state.revenue[d.car_number]||0;
    return `<tr>
      <td>${esc(d.name)}</td>
      <td>${esc(d.phone)}</td>
      <td><span class="car-num">${esc(d.car_number)}</span></td>
      <td><span class="pin-code" style="font-size:14px">${d.pin}</span></td>
      <td style="color:var(--green);font-weight:600">${fmt(d.balance)} сум</td>
      <td>⭐ ${r.avg} <small style="color:var(--muted)">(${r.count})</small></td>
      <td style="color:var(--orange);font-weight:600">${fmt(rev)} сум</td>
      <td>
        <button class="btn btn-success" onclick="doAddBalance('${escQ(d.car_number)}')">💰</button>
        <button class="btn btn-orange" onclick="doAddBonus('${escQ(d.car_number)}')">🎁</button>
        <button class="btn btn-danger" onclick="deleteDriver('${escQ(d.car_number)}')">🗑</button>
      </td>
    </tr>`;
  }).join('') || `<tr><td colspan="8" class="empty">Нет водителей</td></tr>`;
}

// ═══════════════ РЕЙТИНГ ═══════════════
function loadRating(){
  const container = document.getElementById('rating-list');
  const ratings = state.ratings;
  const sorted = Object.entries(ratings).sort((a,b)=>{
    const ra = a[1].count > 0 ? a[1].total/a[1].count : 0;
    const rb = b[1].count > 0 ? b[1].total/b[1].count : 0;
    return rb-ra;
  });
  if(!sorted.length){
    container.innerHTML = `<div class="empty"><div class="empty-icon">🏆</div>Нет данных о рейтинге</div>`;
    return;
  }
  container.innerHTML = sorted.map(([car,r],i)=>{
    const avg = r.count > 0 ? (r.total/r.count).toFixed(1) : '5.0';
    const stars = avg>=4.8?'⭐⭐⭐⭐⭐':avg>=4?'⭐⭐⭐⭐':avg>=3?'⭐⭐⭐':'⭐⭐';
    const bonus = state.bonuses[car]||0;
    const rev = state.revenue[car]||0;
    const rank = i===0?'gold':i===1?'silver':i===2?'bronze':'';
    return `<div class="driver-card">
      <div class="rank-num ${rank}">${i+1}</div>
      <div class="dc-info">
        <div class="dc-car">🚗 ${esc(car)}</div>
        <div class="dc-sub">📦 ${r.orders||0} заказов &nbsp;|&nbsp; <span class="bonus-pill">🎁 ${bonus} бонусов</span> &nbsp;|&nbsp; 💰 ${fmt(rev)} сум</div>
        <div class="rating-bar"><div class="rating-fill" style="width:${Math.round(avg/5*100)}%"></div></div>
      </div>
      <div class="dc-rating">
        <div class="dc-avg">${avg}</div>
        <div>${stars}</div>
        <div style="color:var(--muted);font-size:11px">${r.count} оценок</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:5px">
        <button class="btn btn-orange" onclick="doAddBonus('${escQ(car)}')">🎁 Бонус</button>
        <button class="btn btn-info" onclick="openChat('${escQ(car)}')">💬 Чат</button>
      </div>
    </div>`;
  }).join('');
}

// ═══════════════ ЧАТ ═══════════════
function loadChatDriverList(){
  const container = document.getElementById('chat-driver-list');
  const keys = Object.keys(state.drivers);
  if(!keys.length){
    container.innerHTML = `<div class="empty" style="padding:20px">Нет водителей</div>`;
    return;
  }
  container.innerHTML = keys.map(k=>{
    const d = state.drivers[k];
    const icon = d.status==='free' ? '🟢' : '🔴';
    const active = d.car_number === currentCar ? 'background:var(--bg4);' : '';
    return `<div onclick="openChat('${escQ(d.car_number||k)}')" style="padding:9px 10px;border-radius:var(--r);cursor:pointer;${active}transition:background .15s;display:flex;align-items:center;gap:8px" onmouseover="this.style.background='var(--bg4)'" onmouseout="this.style.background='${active?'var(--bg4)':'transparent'}'">
      <span>${icon}</span>
      <div>
        <div style="font-size:13px;font-weight:600;color:var(--gold)">${esc(d.car_number||k)}</div>
        <div style="font-size:11px;color:var(--muted)">${esc(d.driver_name||'—')}</div>
      </div>
    </div>`;
  }).join('');
}

function openChat(car){
  currentCar = car;
  switchTab('chat');
  document.getElementById('chat-area').style.display = '';
  document.getElementById('chat-empty-state').style.display = 'none';
  document.getElementById('chat-title').textContent = '💬 ' + car;
  document.getElementById('chat-msgs').innerHTML = '';
  chatSeenIds.clear();
  loadChat();
  if(chatInterval) clearInterval(chatInterval);
  chatInterval = setInterval(loadChat, 2000);
  loadChatDriverList();
}

function closeChat(){
  currentCar = '';
  document.getElementById('chat-area').style.display = 'none';
  document.getElementById('chat-empty-state').style.display = '';
  if(chatInterval){ clearInterval(chatInterval); chatInterval=null; }
}

async function loadChat(){
  if(!currentCar) return;
  try{
    const msgs = await fetch('/api/chat/messages?car='+encodeURIComponent(currentCar)).then(r=>r.json());
    const box = document.getElementById('chat-msgs');
    let added = false;
    msgs.forEach(m=>{
      if(chatSeenIds.has(m.id)) return;
      chatSeenIds.add(m.id);
      const div = document.createElement('div');
      div.className = 'chat-msg ' + (m.from==='dispatcher'?'own':'other');
      const who = m.from==='dispatcher' ? '👨‍💼 Диспетчер' : ('🚗 ' + (m.car_number||''));
      div.innerHTML = `<b>${esc(who)}</b>: ${esc(m.text)}<div class="msg-meta">${m.time||''}</div>`;
      box.appendChild(div);
      added = true;
    });
    if(added) box.scrollTop = box.scrollHeight;
  }catch(e){}
}

async function sendChat(){
  const text = document.getElementById('chat-text').value.trim();
  if(!text || !currentCar) return;
  document.getElementById('chat-text').value = '';
  try{
    await fetch('/api/chat/dispatch',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({car_number:currentCar, text})
    });
    loadChat();
  }catch(e){}
}

function sendQuick(text){
  if(!currentCar){ notify('❌ Сначала выберите водителя в чате!','warning'); return; }
  fetch('/api/chat/dispatch',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({car_number:currentCar, text})
  }).then(()=>loadChat());
}

// ═══════════════ ФИНАНСЫ ═══════════════
async function loadFinance(){
  const rev = state.revenue;
  const orders = state.orders;
  const done = orders.filter(o=>o.status==='accepted');
  const total = Object.values(rev).reduce((a,b)=>a+b,0);
  const avg = done.length ? Math.round(done.reduce((a,o)=>a+o.price,0)/done.length) : 0;

  document.getElementById('f-total').textContent = fmt(total);
  document.getElementById('f-orders').textContent = done.length;
  document.getElementById('f-avg').textContent = fmt(avg);

  const maxRev = Math.max(1, ...Object.values(rev));
  const sorted = Object.entries(rev).sort((a,b)=>b[1]-a[1]);
  document.getElementById('rev-list').innerHTML = sorted.map(([car, amount])=>`
    <div class="rev-bar-wrap">
      <span class="car-num" style="min-width:110px">${esc(car)}</span>
      <div class="rev-bar-bg">
        <div class="rev-bar-fill" style="width:${Math.round(amount/maxRev*100)}%"></div>
      </div>
      <span style="min-width:100px;text-align:right;color:var(--green);font-weight:600">${fmt(amount)} сум</span>
    </div>
  `).join('') || `<div class="empty">Нет данных о выручке</div>`;
}

async function resetRevenue(){
  if(!confirm('Сбросить выручку и начать новую смену?')) return;
  try{
    await fetch('/api/stats/reset_revenue',{method:'POST'});
    state.revenue = {};
    notify('✅ Смена сброшена!','info');
    loadFinance();
  }catch(e){}
}

function exportReport(){
  const rev = state.revenue;
  const ratings = state.ratings;
  const total = Object.values(rev).reduce((a,b)=>a+b,0);
  const date = new Date().toLocaleDateString('ru');
  let csv = `Отчёт TAXI 3042 XAZARASP - ${date}\n\n`;
  csv += `Водитель,Выручка (сум),Рейтинг,Заказов,Бонусы\n`;
  const cars = new Set([...Object.keys(rev), ...Object.keys(ratings)]);
  for(const car of cars){
    const r = ratings[car]||{avg:5,count:0,orders:0};
    csv += `${car},${rev[car]||0},${r.avg},${r.orders||0},${state.bonuses[car]||0}\n`;
  }
  csv += `\nИтого,${total},,, \n`;
  const blob = new Blob(['\uFEFF'+csv],{type:'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `taxi3042_${date.replace(/\./g,'-')}.csv`;
  a.click();
  notify('📥 Отчёт скачан!', 'info');
}

// ═══════════════ ТАРИФЫ ═══════════════
async function loadTariffs(){
  try{
    const t = await fetch('/api/tariffs').then(r=>r.json());
    state.tariffs = t;
    document.getElementById('tariff-display').innerHTML = `
      <div class="tariff-card"><div class="tariff-val">${fmt(t.base_fare)}</div><div class="tariff-lbl">Посадка (сум)</div></div>
      <div class="tariff-card"><div class="tariff-val">${fmt(t.city_rate)}</div><div class="tariff-lbl">Город (сум/км)</div></div>
      <div class="tariff-card"><div class="tariff-val">${fmt(t.suburb_rate)}</div><div class="tariff-lbl">Загород (сум/км)</div></div>
      <div class="tariff-card"><div class="tariff-val">${fmt(t.wait_rate)}</div><div class="tariff-lbl">Ожидание (сум/мин)</div></div>
    `;
    document.getElementById('t-base').value = t.base_fare;
    document.getElementById('t-city').value = t.city_rate;
    document.getElementById('t-suburb').value = t.suburb_rate;
    document.getElementById('t-wait').value = t.wait_rate;
  }catch(e){}
}

async function saveTariffs(){
  const body = {
    base_fare:   parseInt(document.getElementById('t-base').value)||5000,
    city_rate:   parseInt(document.getElementById('t-city').value)||2800,
    suburb_rate: parseInt(document.getElementById('t-suburb').value)||3000,
    wait_rate:   parseInt(document.getElementById('t-wait').value)||500,
  };
  try{
    await fetch('/api/tariffs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    notify('✅ Тарифы сохранены!','info');
    loadTariffs();
  }catch(e){ notify('❌ Ошибка сохранения','warning'); }
}

// ═══════════════ МОДАЛЬНОЕ ОКНО ═══════════════
function showModal(car,name,phone,status,balance,speed,rating,bonus,revenue){
  modalCar = car;
  document.getElementById('m-car').textContent = car;
  document.getElementById('m-name').textContent = name||'—';
  document.getElementById('m-phone').textContent = phone||'—';
  document.getElementById('m-status').textContent = status==='free'?'🟢 Свободен':status==='busy'?'🔴 На заказе':'⚫ '+status;
  document.getElementById('m-balance').textContent = fmt(balance)+' сум';
  document.getElementById('m-speed').textContent = speed+' км/ч';
  document.getElementById('m-rating').textContent = '⭐ '+rating+'/5.0';
  document.getElementById('m-bonus').textContent = '🎁 '+bonus+' бонусов';
  document.getElementById('m-revenue').textContent = fmt(revenue)+' сум';
  document.getElementById('driver-modal').classList.add('open');
}
function closeModal(){ document.getElementById('driver-modal').classList.remove('open'); }
document.getElementById('driver-modal').addEventListener('click',function(e){if(e.target===this)closeModal();});

// ═══════════════ ПИН ДЕЙСТВИЯ ═══════════════
async function approvePin(pinId){
  if(!confirm('Одобрить заявку?')) return;
  try{
    const r = await fetch('/api/admin/approve_code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin_id:pinId})}).then(r=>r.json());
    notify(r.success ? '✅ Водитель одобрен!' : '❌ Ошибка', r.success?'info':'warning');
    await liveUpdate();
    loadDriversPage();
  }catch(e){}
}

async function rejectPin(pinId){
  if(!confirm('Отклонить заявку?')) return;
  try{
    await fetch('/api/admin/reject_code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin_id:pinId})});
    notify('❌ Заявка отклонена','info');
    await liveUpdate();
    loadDriversPage();
  }catch(e){}
}

// ═══════════════ БАЛАНС ═══════════════
async function doAddBalance(car){
  const amount = prompt(`💰 Пополнение баланса для ${car}\n(введите сумму в сум):`);
  if(!amount || isNaN(amount) || parseInt(amount)<=0) return;
  try{
    const r = await fetch('/api/admin/add_balance',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({car_number:car, amount:parseInt(amount)})
    }).then(r=>r.json());
    if(r.success){
      notify(`✅ Баланс ${car} пополнен! Новый: ${fmt(r.new_balance)} сум`, 'info');
      liveUpdate();
    }
  }catch(e){}
}

async function approveBalance(id){
  if(!confirm('Одобрить пополнение баланса?')) return;
  try{
    await fetch('/api/admin/approve_balance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
    notify('✅ Баланс пополнен!','info');
    liveUpdate(); loadDriversPage();
  }catch(e){}
}

async function rejectBalance(id){
  if(!confirm('Отклонить заявку?')) return;
  try{
    await fetch('/api/admin/reject_balance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
    notify('❌ Заявка отклонена','info');
    loadDriversPage();
  }catch(e){}
}

// ═══════════════ БОНУСЫ ═══════════════
async function doAddBonus(car){
  const opts = ['10 — За пунктуальность','20 — За хорошие отзывы','50 — За лучший месяц','100 — За год работы'];
  const pts  = [10,20,50,100];
  const choice = prompt(`🎁 Бонусы для ${car}:\n\n${opts.map((o,i)=>`${i+1}. ${o}`).join('\n')}\n\nВведите номер (1-4) или своё количество:`);
  if(!choice) return;
  const idx = parseInt(choice)-1;
  const amount = (idx>=0&&idx<4) ? pts[idx] : parseInt(choice);
  if(!amount||amount<=0||isNaN(amount)) return;
  try{
    const r = await fetch('/api/admin/add_bonus',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({car_number:car, points:amount})
    }).then(r=>r.json());
    if(r.success){
      notify(`🎁 ${car}: +${amount} бонусов (всего ${r.total_bonus})`, 'info');
      liveUpdate();
    }
  }catch(e){}
}

// ═══════════════ УДАЛЕНИЕ ═══════════════
async function doRemove(car){
  if(!confirm(`Убрать ${car} с линии?`)) return;
  try{
    await fetch('/remove_driver',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({driver:car,car_number:car})});
    notify(`🗑 ${car} убран с линии`, 'info');
    liveUpdate();
  }catch(e){}
}

async function deleteDriver(car){
  if(!confirm(`Полностью удалить ${car}? Все данные будут удалены!`)) return;
  try{
    await fetch('/api/admin/delete_driver',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({car_number:car})});
    notify(`🗑 ${car} удалён`, 'info');
    liveUpdate(); loadDriversPage();
  }catch(e){}
}

// ═══════════════ УТИЛИТЫ ═══════════════
function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') }
function escAttr(s){ return String(s||'').replace(/"/g,'&quot;') }
function escQ(s){ return String(s||'').replace(/'/g,"\\'") }
</script>
</body>
</html>"""

# ==================== РОУТЫ ====================

@app.route('/')
def index():
    return ADMIN_HTML

@app.route('/api/stats/revenue', methods=['GET'])
def get_revenue_api():
    return jsonify(revenue_db)

@app.route('/api/stats/ratings', methods=['GET'])
def get_ratings_api():
    result = {}
    for car, r in ratings_db.items():
        avg = round(r["total"]/r["count"], 1) if r["count"] > 0 else 5.0
        result[car] = {"avg": avg, "count": r["count"], "orders": r["orders"],
                       "total": r["total"], "stars": get_stars(avg)}
    return jsonify(result)

@app.route('/api/stats/bonuses', methods=['GET'])
def get_bonuses_api():
    return jsonify(bonuses_db)

@app.route('/api/stats/all_drivers', methods=['GET'])
def get_all_drivers_api():
    result = []
    for car, d in driver_list.items():
        result.append({
            "name": d.get("name",""),
            "phone": d.get("phone",""),
            "car_number": d.get("car_number", car),
            "pin": d.get("pin",""),
            "balance": get_balance(car)
        })
    return jsonify(result)

@app.route('/api/stats/reset_revenue', methods=['POST'])
def reset_revenue():
    revenue_db.clear()
    tg_send("🔄 Смена сброшена — выручка обнулена")
    return jsonify({"success": True})

@app.route('/api/admin/balance_requests', methods=['GET'])
def get_balance_requests():
    return jsonify(sorted(balance_requests, key=lambda x: x['id'], reverse=True)[:30])

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
        #info{{position:absolute;top:10px;right:10px;background:rgba(0,0,0,.85);
               color:#FFD600;padding:10px 16px;border-radius:10px;font-size:13px;
               z-index:100;border:1px solid #333;}}
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
        update();setInterval(update,3000);
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
                let icon={{url:d.status==='free'
                    ?'https://maps.google.com/mapfiles/ms/icons/green-dot.png'
                    :'https://maps.google.com/mapfiles/ms/icons/red-dot.png',
                    scaledSize:new google.maps.Size(40,40)}};
                if(markers[k]){{markers[k].setPosition(pos);markers[k].setIcon(icon);}}
                else{{
                    markers[k]=new google.maps.Marker({{position:pos,map,title:d.car_number,icon,
                        label:{{text:d.car_number,color:'#FFD600',fontSize:'11px',fontWeight:'bold'}}}});
                    markers[k].addListener('click',()=>{{
                        Object.values(iws).forEach(w=>w.close());
                        iws[k]=new google.maps.InfoWindow({{content:`
                            <div style="background:#1a1a1a;color:#fff;padding:14px;border-radius:10px;min-width:200px;">
                                <b style="color:#FFD600;font-size:15px;">🚗 ${{d.car_number}}</b><br><br>
                                👤 ${{d.driver_name||'—'}}<br>📱 ${{d.phone||'—'}}<br>
                                📍 <b style="color:${{d.status==='free'?'#4CAF50':'#FF5252'}}">
                                    ${{d.status==='free'?'🟢 Свободен':'🔴 На заказе'}}</b><br>
                                ⚡ ${{d.speed}} км/ч<br>
                                💰 ${{parseInt(d.balance||0).toLocaleString()}} сум<br>
                                <small style="color:#555;">🕐 ${{d.time_str}}</small>
                            </div>`}});
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
    return jsonify({"success": True})

@app.route('/api/driver/login', methods=['POST'])
def driver_login():
    data = request.json
    pin  = data.get('pin', '')
    for car, d in driver_list.items():
        if d.get('pin') == pin:
            return jsonify({"success": True, "driver_id": car,
                            "name": d.get('name',''), "balance": get_balance(car)})
    for pid, info in pending_codes.items():
        if info.get('pin') == pin and info.get('status') == 'pending':
            info['status'] = 'approved'
            car = info['car_number']
            driver_list[car] = info
            set_balance(car, 50000)
            tg_notify_approved(info['name'], car, pin)
            return jsonify({"success": True, "driver_id": car,
                            "name": info.get('name',''), "balance": 50000})
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
    pin_id = request.json.get('pin_id','')
    info   = pending_codes.get(pin_id)
    if not info:
        return jsonify({"success": False, "error": "Not found"})
    info['status'] = 'approved'
    car = info['car_number']
    driver_list[car] = info
    set_balance(car, 50000)
    tg_notify_approved(info['name'], car, info['pin'])
    return jsonify({"success": True})

@app.route('/api/admin/reject_code', methods=['POST'])
def reject_code():
    pin_id = request.json.get('pin_id','')
    info   = pending_codes.get(pin_id)
    if info:
        info['status'] = 'rejected'
        tg_notify_rejected(info['name'], info['car_number'])
    return jsonify({"success": True})

@app.route('/api/admin/delete_driver', methods=['POST'])
def delete_driver():
    car = request.json.get('car_number','')
    driver_list.pop(car, None)
    drivers.pop(car, None)
    ratings_db.pop(car, None)
    bonuses_db.pop(car, None)
    revenue_db.pop(car, None)
    tg_send(f"🗑 Водитель {car} удалён")
    return jsonify({"success": True})

@app.route('/api/admin/add_balance', methods=['POST'])
def admin_add_balance():
    car    = request.json.get('car_number','')
    amount = int(request.json.get('amount', 0))
    if amount <= 0:
        return jsonify({"success": False})
    new_b = get_balance(car) + amount
    set_balance(car, new_b)
    if car in drivers:
        drivers[car]['balance'] = new_b
    tg_send(f"💰 {car}: +{amount:,} → {new_b:,} сум")
    return jsonify({"success": True, "new_balance": new_b})

@app.route('/api/admin/add_bonus', methods=['POST'])
def admin_add_bonus():
    car    = request.json.get('car_number','')
    points = int(request.json.get('points', 0))
    if points <= 0:
        return jsonify({"success": False})
    add_bonus(car, points)
    total = get_bonus(car)
    tg_send(f"🎁 {car}: +{points} бонусов → всего {total}")
    return jsonify({"success": True, "total_bonus": total})

@app.route('/api/driver/rate', methods=['POST'])
def rate_driver():
    car   = request.json.get('car_number','')
    stars = int(request.json.get('stars', 5))
    stars = max(1, min(5, stars))
    add_rating(car, stars)
    r = get_rating(car)
    if stars == 5:
        add_bonus(car, 5)
    return jsonify({"success": True, "new_rating": r["avg"]})

@app.route('/api/driver/<driver_id>/rating', methods=['GET'])
def get_driver_rating(driver_id):
    r = get_rating(driver_id)
    return jsonify({
        "rating":  r["avg"],
        "count":   r["count"],
        "orders":  r["orders"],
        "bonus":   get_bonus(driver_id),
        "stars":   get_stars(r["avg"])
    })

@app.route('/api/balance/request', methods=['POST'])
def request_balance():
    car    = request.json.get('car_number','')
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
            bonus_pts = req['amount'] // 10000
            if bonus_pts > 0:
                add_bonus(car, bonus_pts)
            tg_send(f"✅ {car}: +{req['amount']:,} → {new_b:,} сум\n🎁 +{bonus_pts} бонусов")
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
    return jsonify({
        "balance": get_balance(driver_id),
        "bonus":   get_bonus(driver_id),
        "rating":  get_rating(driver_id)["avg"]
    })

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
    if did not in shift_start:
        shift_start[did] = time.time()
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
    return jsonify({
        'status':  'ok',
        'balance': bal,
        'bonus':   get_bonus(did),
        'rating':  get_rating(did)["avg"]
    })

@app.route('/api/drivers', methods=['GET'])
def get_drivers():
    return jsonify(drivers)

@app.route('/remove_driver', methods=['POST'])
def remove_driver():
    data = request.get_json(force=True)
    did  = data.get('driver', data.get('car_number',''))
    drivers.pop(did, None)
    return jsonify({'status': 'ok'})

@app.route('/api/balance', methods=['POST'])
def update_balance():
    data   = request.get_json(force=True)
    did    = data.get('driver','')
    amount = int(data.get('amount', 0))
    new_b  = get_balance(did) + amount
    set_balance(did, new_b)
    if did in drivers:
        drivers[did]['balance'] = new_b
    return jsonify({'status': 'ok', 'new_balance': new_b})

@app.route('/admin/block_driver', methods=['POST'])
def block_driver():
    car = request.get_json(force=True).get('car_number','')
    if car in drivers:
        drivers[car]['status'] = 'blocked'
    tg_send(f"🚫 {car} заблокирован")
    return jsonify({'status': 'ok'})

@app.route('/api/orders/create', methods=['POST'])
def create_order():
    data     = request.json
    car      = data.get('car_number','')
    from_a   = data.get('from_address','')
    to_a     = data.get('to_address','')
    price    = int(data.get('price', 0))
    client   = data.get('client','Клиент')
    distance = data.get('distance','—')
    order_id = create_order_internal(car, from_a, to_a, price, client, distance)
    tg_send(f"📦 Заказ → <b>{car}</b>\n📍 {from_a} → {to_a}\n💰 {price:,} сум\n👤 {client}")
    return jsonify({"success": True, "order_id": order_id})

@app.route('/api/orders/broadcast', methods=['POST'])
def broadcast_order():
    data   = request.json
    from_a = data.get('from_address','')
    to_a   = data.get('to_address','')
    price  = int(data.get('price', 0))
    client = data.get('client','Клиент')
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
    car = request.args.get('car','')
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
    car      = data.get('car_number','')
    action   = data.get('response','')
    for order in orders_db:
        if order['id'] == order_id:
            order['status'] = action
            if action == 'accepted':
                add_bonus(car, 2)
                # ✅ Добавляем выручку
                add_revenue(car, order.get('price', 0))
            break
    if action == 'accepted':
        tg_send(f"✅ <b>{car}</b> принял заказ")
    else:
        tg_send(f"❌ <b>{car}</b> отклонил заказ")
    return jsonify({"success": True})

@app.route('/api/orders/list', methods=['GET'])
def list_orders():
    return jsonify(list(reversed(orders_db))[:100])

@app.route('/api/chat/send', methods=['POST'])
def chat_send():
    data = request.json
    car  = data.get('car_number','')
    text = data.get('text','')
    drv  = data.get('driver','')
    t    = datetime.now().strftime('%H:%M')
    if car not in chat_messages:
        chat_messages[car] = []
    chat_messages[car].append({
        "id":         int(time.time() * 1000) + random.randint(0,999),
        "car_number": car,
        "from":       "driver",
        "text":       text,
        "time":       t
    })
    tg_send(f"💬 <b>{drv}</b> ({car}):\n{text}")
    return jsonify({"success": True})

@app.route('/api/chat/messages', methods=['GET'])
def chat_get():
    car = request.args.get('car','')
    return jsonify(chat_messages.get(car, [])[-50:])

@app.route('/api/chat/dispatch', methods=['POST'])
def chat_dispatch():
    data = request.json
    car  = data.get('car_number','')
    text = data.get('text','')
    t    = datetime.now().strftime('%H:%M')
    if car not in chat_messages:
        chat_messages[car] = []
    chat_messages[car].append({
        "id":         int(time.time() * 1000) + random.randint(0,999),
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
        'orders_active':  len([o for o in orders_db if o['status'] == 'pending']),
        'revenue_total':  sum(revenue_db.values())
    })

if __name__ == '__main__':
    print("🚕 TAXI 3042 XAZARASP — STARTED")
    app.run(host='0.0.0.0', port=5000, debug=False)
