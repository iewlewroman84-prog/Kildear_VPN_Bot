import os
import asyncio
import json
import logging
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional

# ===== УСТАНАВЛИВАЕМ АЛЬТЕРНАТИВНЫЙ АДРЕС ДЛЯ TELEGRAM API =====
os.environ["TELEGRAM_BOT_API_URL"] = "https://telegram.dog/bot"

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties

import aiohttp
from flask import Flask, request

# ======================= НАСТРОЙКИ (из переменных окружения) =======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8656434661:AAHv3yKPvStdiSDcSiBJPxKaYSgmJLtBlpo")

PANEL_URL = "https://my.xorek.cloud:2053"
PANEL_USERNAME = os.environ.get("PANEL_USERNAME", "ВАШ_ЛОГИН")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "ВАШ_ПАРОЛЬ")

FREEKASSA_MERCHANT_ID = os.environ.get("FREEKASSA_MERCHANT_ID", "75393")
FREEKASSA_API_KEY = os.environ.get("FREEKASSA_API_KEY", "a482e9901b68ea8fa3a30e16b044f80c")
FREEKASSA_SECRET_WORD_1 = os.environ.get("FREEKASSA_SECRET_WORD_1", "Зимушка")
FREEKASSA_SECRET_WORD_2 = os.environ.get("FREEKASSA_SECRET_WORD_2", "Декабрь")
FREEKASSA_API_URL = "https://api.freekassa.ru/v1/orders/create"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://kildear-vpn-bot.onrender.com/webhook/freekassa")

# ======================= ТАРИФЫ =======================
PLANS = {
    "2d": {"days": 2, "price": 0, "devices": 1, "label": "🎁 2 дня бесплатно", "emoji": "🎁"},
    "1m": {"days": 30, "price": 139, "devices": 2, "label": "1 месяц", "emoji": "🔥"},
    "3m": {"days": 90, "price": 469, "devices": 3, "label": "3 месяца", "emoji": "⭐"},
    "1y": {"days": 365, "price": 899, "devices": 5, "label": "1 год", "emoji": "💎"},
}

# ======================= ИНИЦИАЛИЗАЦИЯ =======================
logging.basicConfig(level=logging.INFO)

session = AiohttpSession()
bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

user_orders = {}

# ======================= СОСТОЯНИЯ FSM =======================
class OrderState(StatesGroup):
    waiting_for_payment = State()

# ======================= КЛАВИАТУРА С ТАРИФАМИ =======================
def get_plans_keyboard():
    """Создаёт клавиатуру с кнопками тарифов"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"{PLANS['2d']['emoji']} {PLANS['2d']['label']}",
                callback_data="plan_2d"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{PLANS['1m']['emoji']} {PLANS['1m']['label']} — {PLANS['1m']['price']} ₽",
                callback_data="plan_1m"
            ),
            InlineKeyboardButton(
                text=f"{PLANS['3m']['emoji']} {PLANS['3m']['label']} — {PLANS['3m']['price']} ₽",
                callback_data="plan_3m"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{PLANS['1y']['emoji']} {PLANS['1y']['label']} — {PLANS['1y']['price']} ₽",
                callback_data="plan_1y"
            )
        ]
    ])
    return keyboard

# ======================= ФУНКЦИИ ДЛЯ РАБОТЫ С ПАНЕЛЬЮ =======================
async def get_panel_session():
    async with aiohttp.ClientSession() as session:
        login_url = f"{PANEL_URL}/login"
        payload = {"username": PANEL_USERNAME, "password": PANEL_PASSWORD}
        try:
            async with session.post(login_url, json=payload, ssl=False) as resp:
                if resp.status == 200:
                    return session
                else:
                    logging.error(f"Ошибка авторизации: {resp.status}")
                    return None
        except Exception as e:
            logging.error(f"Ошибка соединения: {e}")
            return None

async def create_vpn_user(telegram_id: int, days: int) -> Optional[str]:
    session = await get_panel_session()
    if not session:
        return None
    
    username = f"user_{telegram_id}_{int(datetime.now().timestamp())}"
    expiry_date = datetime.now() + timedelta(days=days)
    expiry_timestamp = int(expiry_date.timestamp())
    
    client_data = {
        "id": telegram_id,
        "flow": "",
        "email": f"{username}@example.com",
        "limitIp": 5,
        "totalGB": 0,
        "expiryTime": expiry_timestamp * 1000,
        "enable": True,
        "subId": "",
        "settings": json.dumps({
            "clients": [{"id": username, "flow": "xtls-rprx-vision"}]
        })
    }
    
    add_client_url = f"{PANEL_URL}/panel/api/inbounds/addClient"
    
    try:
        async with session.post(add_client_url, json=client_data, ssl=False) as resp:
            if resp.status == 200:
                sub_url = f"{PANEL_URL}/sub/{username}"
                return sub_url
            else:
                logging.error(f"Ошибка создания клиента: {resp.status}")
                return None
    except Exception as e:
        logging.error(f"Ошибка запроса к API: {e}")
        return None

# ======================= ФУНКЦИИ ДЛЯ РАБОТЫ С FREEKASSA =======================
async def create_freekassa_invoice(amount: float, description: str, order_id: str) -> tuple:
    payload = {
        "merchant_id": FREEKASSA_MERCHANT_ID,
        "amount": str(amount),
        "description": description,
        "order_id": order_id,
        "currency": "RUB",
        "payment_method": "all",
        "success_url": "https://t.me/kildear_vpn_bot",
        "fail_url": "https://t.me/kildear_vpn_bot",
        "webhook_url": WEBHOOK_URL
    }
    
    headers = {
        "Authorization": f"Bearer {FREEKASSA_API_KEY}",
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(FREEKASSA_API_URL, json=payload, headers=headers) as resp:
                data = await resp.json()
                logging.info(f"Ответ FREEKASSA: {data}")
                if data.get("status") == "success":
                    return data["data"]["payment_url"], data["data"]["order_id"]
                else:
                    logging.error(f"Ошибка FREEKASSA: {data}")
                    return None, None
        except Exception as e:
            logging.error(f"Ошибка соединения с FREEKASSA: {e}")
            return None, None

def verify_freekassa_signature(data, signature):
    if not signature:
        return False
    check_string = f"{data.get('order_id')}{data.get('status')}{FREEKASSA_SECRET_WORD_2}"
    expected = hashlib.sha256(check_string.encode()).hexdigest()
    return hmac.compare_digest(expected, signature)

# ======================= ОБРАБОТЧИКИ КОМАНД =======================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🛡️ <b>Добро пожаловать в Kildear VPN!</b>\n\n"
        "Я помогу вам приобрести надёжный VPN-доступ.\n\n"
        "👇 <b>Выберите тариф:</b>",
        reply_markup=get_plans_keyboard(),
        parse_mode="HTML"
    )

# ======================= ОБРАБОТЧИКИ КНОПОК (Callback) =======================
@dp.callback_query(F.data.startswith("plan_"))
async def process_plan_selection(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    plan_key = callback.data.replace("plan_", "")
    if plan_key not in PLANS:
        await callback.message.answer("❌ Такого тарифа не существует.")
        return
    
    plan = PLANS[plan_key]
    user_id = callback.from_user.id
    order_id = f"{user_id}_{int(datetime.now().timestamp())}"
    
    # БЕСПЛАТНЫЙ ТАРИФ
    if plan["price"] == 0:
        await callback.message.answer(
            f"🎁 <b>Тестовый доступ на 2 дня!</b>\n\n"
            f"Создаю ваш бесплатный VPN-ключ...",
            parse_mode="HTML"
        )
        
        vpn_link = await create_vpn_user(user_id, plan["days"])
        
        if vpn_link:
            await callback.message.answer(
                f"✅ <b>VPN-ключ готов!</b>\n\n"
                f"🔗 <b>Ссылка для подключения:</b>\n"
                f"<code>{vpn_link}</code>\n\n"
                f"📱 <b>Инструкция:</b>\n"
                f"1. Скачайте приложение V2RayNG или Hiddify\n"
                f"2. Скопируйте ссылку и вставьте в приложение\n"
                f"3. Наслаждайтесь безопасным интернетом!\n\n"
                f"📅 Подписка активна до: {(datetime.now() + timedelta(days=plan['days'])).strftime('%d.%m.%Y')}\n\n"
                f"💡 После теста выберите платный тариф 👇",
                reply_markup=get_plans_keyboard(),
                parse_mode="HTML"
            )
        else:
            await callback.message.answer(
                "❌ Не удалось создать VPN-ключ. Попробуйте позже или обратитесь к администратору."
            )
        return
    
    # ПЛАТНЫЙ ТАРИФ
    await callback.message.answer(
        f"⏳ Создаю счёт для оплаты...\n"
        f"Тариф: {plan['label']}\n"
        f"Стоимость: {plan['price']} ₽\n"
        f"Устройств: {plan['devices']}",
        parse_mode="HTML"
    )
    
    payment_url, invoice_id = await create_freekassa_invoice(
        amount=plan["price"],
        description=f"Kildear VPN — {plan['label']}",
        order_id=order_id
    )
    
    if not payment_url:
        await callback.message.answer("❌ Ошибка при создании счёта. Попробуйте позже.")
        return
    
    user_orders[user_id] = {
        "invoice_id": invoice_id,
        "order_id": order_id,
        "plan": plan_key,
        "days": plan["days"],
        "price": plan["price"],
        "devices": plan["devices"],
        "status": "pending"
    }
    
    await state.set_state(OrderState.waiting_for_payment)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить через FREEKASSA", url=payment_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data="check_payment")],
        [InlineKeyboardButton(text="❌ Отменить заказ", callback_data="cancel_order")]
    ])
    
    await callback.message.answer(
        f"💳 <b>Оформление заказа</b>\n\n"
        f"Тариф: {plan['label']}\n"
        f"Стоимость: {plan['price']} ₽\n"
        f"Устройств: {plan['devices']}\n\n"
        f"Нажмите кнопку ниже, чтобы оплатить.\n"
        f"После оплаты нажмите «Проверить оплату».",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

# ======================= ОБРАБОТЧИКИ КНОПОК (Check/Cancel) =======================
@dp.callback_query(F.data == "check_payment")
async def check_payment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_orders:
        await callback.message.answer("❌ Сначала выберите тариф через /start")
        return
    
    order = user_orders[user_id]
    if order["status"] == "paid":
        await callback.message.answer("✅ Этот заказ уже оплачен!")
        return
    
    # Здесь проверка статуса платежа через API FREEKASSA
    await callback.message.answer(
        "⏳ Проверка платежа...\n"
        "Если вы уже оплатили, подождите 1-2 минуты и нажмите «Проверить оплату» снова."
    )

@dp.callback_query(F.data == "cancel_order")
async def cancel_order(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id in user_orders:
        del user_orders[user_id]
    await state.clear()
    await callback.answer("Заказ отменён")
    await callback.message.edit_text("❌ Заказ отменён. Если передумаете, выберите тариф заново.", reply_markup=get_plans_keyboard())

# ======================= ВЕБХУК ДЛЯ FREEKASSA (FLASK) =======================
app = Flask(__name__)

@app.route('/webhook/freekassa', methods=['POST'])
def freekassa_webhook():
    data = request.json
    signature = request.headers.get('X-Freekassa-Signature')
    
    if not verify_freekassa_signature(data, signature):
        logging.warning("Неверная подпись вебхука")
        return "Invalid signature", 400
    
    if data.get("status") == "paid":
        order_id = data.get("order_id")
        if not order_id:
            return "No order_id", 400
        
        try:
            user_id = int(order_id.split('_')[0])
        except (ValueError, IndexError):
            return "Invalid order_id", 400
        
        if user_id not in user_orders:
            logging.warning(f"Заказ для user_id {user_id} не найден")
            return "Order not found", 404
        
        order = user_orders[user_id]
        if order["status"] == "paid":
            return "Already paid", 200
        
        order["status"] = "paid"
        
        vpn_link = asyncio.run(create_vpn_user(user_id, order["days"]))
        
        if vpn_link:
            asyncio.run(bot.send_message(
                chat_id=user_id,
                text=f"✅ <b>Оплата подтверждена!</b>\n\n"
                     f"🔗 <b>Ваш VPN-ключ:</b>\n"
                     f"<code>{vpn_link}</code>\n\n"
                     f"📅 Подписка активна до: {(datetime.now() + timedelta(days=order['days'])).strftime('%d.%m.%Y')}",
                parse_mode="HTML"
            ))
        else:
            asyncio.run(bot.send_message(
                chat_id=user_id,
                text="❌ Оплата прошла, но не удалось создать VPN-ключ. Обратитесь к администратору."
            ))
        
        del user_orders[user_id]
        return "OK", 200
    
    return "OK", 200

@app.route('/', methods=['GET'])
def index():
    return "Бот Kildear VPN работает!"

# ======================= ЗАПУСК =======================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import threading
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    )
    flask_thread.daemon = True
    flask_thread.start()
    
    asyncio.run(main())
