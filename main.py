import os
import asyncio
import json
import logging
import uuid
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

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8656434661:AAHv3yKPvStdiSDcSiBJPxKaYSgmJLtBlpo")
PANEL_URL = "https://2.26.70.65:55347"
DB_URL = "http://2.26.70.65:8081"  # SQLite Web API

YKASSA_SHOP_ID = os.environ.get("YKASSA_SHOP_ID", "1434221")
YKASSA_SECRET_KEY = os.environ.get("YKASSA_SECRET_KEY", "live_fH2K3m3SygBdP8P6bjaOwkRj4UKl5FwsatLZC-PJKt8")
YKASSA_API_URL = "https://api.yookassa.ru/v3/payments"
YKASSA_WEBHOOK_URL = os.environ.get("YKASSA_WEBHOOK_URL", "https://kildear-vpn-bot.onrender.com/webhook/yookassa")

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
user_referrals = {}
user_free_days = {}
pending_referral = {}

# ======================= СОСТОЯНИЯ FSM =======================
class OrderState(StatesGroup):
    waiting_for_payment = State()

# ======================= КЛАВИАТУРА =======================
def get_plans_keyboard():
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
        ],
        [
            InlineKeyboardButton(
                text="👥 Реферальная программа",
                callback_data="referral_info"
            )
        ]
    ])
    return keyboard

def get_payment_keyboard(payment_url_card: str, payment_url_sbp: str = None):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить картой", url=payment_url_card)]
    ])
    if payment_url_sbp:
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text="📱 Оплатить через СБП", url=payment_url_sbp)]
        )
    keyboard.inline_keyboard.append(
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data="check_payment")]
    )
    keyboard.inline_keyboard.append(
        [InlineKeyboardButton(text="❌ Отменить заказ", callback_data="cancel_order")]
    )
    return keyboard

def get_referral_keyboard(ref_code: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔗 Скопировать ссылку",
                callback_data=f"copy_ref_{ref_code}"
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Мои приглашения",
                callback_data="my_refs"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="back_to_menu"
            )
        ]
    ])
    return keyboard

# ======================= СОЗДАНИЕ КЛИЕНТА ЧЕРЕЗ SQLITE WEB API =======================
async def create_vpn_user(telegram_id: int, days: int) -> Optional[str]:
    """Создаёт VPN-ключ через HTTP-API SQLite Web"""
    username = f"user_{telegram_id}_{int(datetime.now().timestamp())}"
    expiry_date = datetime.now() + timedelta(days=days)
    expiry_timestamp = int(expiry_date.timestamp() * 1000)

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Получаем первый активный Inbound
            async with session.get(f"{DB_URL}/api/query/inbounds") as resp:
                if resp.status != 200:
                    logging.error(f"Ошибка получения Inbound: {resp.status}")
                    return None
                data = await resp.json()
                
                if not data:
                    logging.error("Нет активных Inbound в базе")
                    return None
                
                inbound = data[0]
                inbound_id = inbound["id"]
                settings = json.loads(inbound["settings"])
                
                if "clients" not in settings:
                    settings["clients"] = []
                
                # Проверяем, не существует ли уже клиент
                for client in settings["clients"]:
                    if client.get("email") == username:
                        return f"{PANEL_URL}/sub/{username}"
                
                # Добавляем нового клиента
                new_client = {
                    "id": username,
                    "flow": "xtls-rprx-vision",
                    "email": username,
                    "limitIp": 5,
                    "totalGB": 0
                }
                
                settings["clients"].append(new_client)
                
                # 2. Обновляем Inbound через API
                update_data = {
                    "settings": json.dumps(settings)
                }
                
                async with session.post(
                    f"{DB_URL}/api/query/inbounds/{inbound_id}",
                    json=update_data
                ) as resp:
                    if resp.status == 200:
                        sub_url = f"{PANEL_URL}/sub/{username}"
                        return sub_url
                    else:
                        logging.error(f"Ошибка обновления Inbound: {resp.status}")
                        return None
    except Exception as e:
        logging.error(f"Ошибка при работе с БД: {e}")
        return None

# ======================= РАБОТА С ЮKASSA =======================
async def create_yookassa_invoice(amount: float, description: str, order_id: str, payment_method: str = "bank_card") -> tuple:
    idempotence_key = str(uuid.uuid4())

    if payment_method == "sbp":
        payment_method_data = {"type": "sbp"}
    else:
        payment_method_data = {"type": "bank_card"}

    payload = {
        "amount": {"value": str(amount), "currency": "RUB"},
        "payment_method_data": payment_method_data,
        "confirmation": {"type": "redirect", "return_url": "https://t.me/kildear_vpn_bot"},
        "description": description,
        "metadata": {"order_id": order_id, "telegram_id": order_id.split('_')[0]},
        "capture": True
    }

    auth = aiohttp.BasicAuth(YKASSA_SHOP_ID, YKASSA_SECRET_KEY)
    headers = {"Content-Type": "application/json", "Idempotence-Key": idempotence_key}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(YKASSA_API_URL, json=payload, headers=headers, auth=auth) as resp:
                data = await resp.json()
                if data.get("status") in ["pending", "waiting_for_capture"]:
                    return data["confirmation"]["confirmation_url"], data["id"]
                else:
                    return None, None
        except Exception as e:
            logging.error(f"Ошибка ЮKassa: {e}")
            return None, None

async def check_yookassa_payment(payment_id: str) -> str:
    auth = aiohttp.BasicAuth(YKASSA_SHOP_ID, YKASSA_SECRET_KEY)
    check_url = f"{YKASSA_API_URL}/{payment_id}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(check_url, auth=auth) as resp:
                data = await resp.json()
                return data.get("status")
        except Exception as e:
            return None

# ======================= РЕФЕРАЛЬНАЯ СИСТЕМА =======================
def generate_ref_code(user_id: int) -> str:
    import hashlib
    return hashlib.md5(f"{user_id}_{datetime.now().timestamp()}".encode()).hexdigest()[:8]

def get_or_create_ref_data(user_id: int):
    if user_id not in user_referrals:
        user_referrals[user_id] = {"ref_code": generate_ref_code(user_id), "refs": []}
        user_free_days[user_id] = 0
    return user_referrals[user_id]

async def add_free_days(user_id: int, days: int):
    if user_id not in user_free_days:
        user_free_days[user_id] = 0
    user_free_days[user_id] += days

async def process_referral(new_user_id: int, ref_code: str):
    inviter_id = None
    for uid, data in user_referrals.items():
        if data["ref_code"] == ref_code:
            inviter_id = uid
            break
    if not inviter_id or inviter_id == new_user_id:
        return
    if new_user_id in user_referrals[inviter_id]["refs"]:
        return
    pending_referral[new_user_id] = inviter_id

async def activate_referral(user_id: int):
    if user_id not in pending_referral:
        return
    inviter_id = pending_referral[user_id]
    if inviter_id not in user_referrals:
        return
    if user_id not in user_referrals[inviter_id]["refs"]:
        user_referrals[inviter_id]["refs"].append(user_id)
        await add_free_days(inviter_id, 7)
        await bot.send_message(
            chat_id=inviter_id,
            text=f"🎉 <b>Ваш друг купил подписку!</b>\n\n"
                 f"Вы получили <b>7 дней</b> бесплатной подписки.\n"
                 f"Всего бесплатных дней: <b>{user_free_days.get(inviter_id, 0)}</b>",
            parse_mode="HTML"
        )
    del pending_referral[user_id]

# ======================= ОБРАБОТЧИКИ КОМАНД =======================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    args = message.text.split()
    user_id = message.from_user.id
    if len(args) > 1 and args[1].startswith("ref_"):
        ref_code = args[1].replace("ref_", "")
        await process_referral(user_id, ref_code)
    get_or_create_ref_data(user_id)
    await message.answer(
        "🛡️ <b>Добро пожаловать в Kildear VPN!</b>\n\n"
        "👇 <b>Выберите тариф:</b>",
        reply_markup=get_plans_keyboard(),
        parse_mode="HTML"
    )

@dp.message(Command("docs"))
async def cmd_docs(message: Message):
    await message.answer(
        "📄 <b>Документы Kildear VPN</b>\n\n"
        "Политика конфиденциальности: https://kildear-vpn-atst.onrender.com/privacy\n"
        "Пользовательское соглашение: https://kildear-vpn-atst.onrender.com/terms\n\n"
        "📧 Контакты: kildearVPN@yandex.ru",
        parse_mode="HTML"
    )

@dp.message(Command("ref"))
async def cmd_ref(message: Message):
    user_id = message.from_user.id
    data = get_or_create_ref_data(user_id)
    ref_code = data["ref_code"]
    refs_count = len(data["refs"])
    free_days = user_free_days.get(user_id, 0)
    await message.answer(
        f"👥 <b>Ваша реферальная ссылка</b>\n\n"
        f"🔗 <code>https://t.me/kildear_vpn_bot?start=ref_{ref_code}</code>\n\n"
        f"📊 Приглашено друзей: <b>{refs_count}</b>\n"
        f"🎁 Бесплатных дней накоплено: <b>{free_days}</b>",
        parse_mode="HTML"
    )

# ======================= ОБРАБОТЧИКИ КНОПОК =======================
@dp.callback_query(F.data == "referral_info")
async def referral_info(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    data = get_or_create_ref_data(user_id)
    ref_code = data["ref_code"]
    refs_count = len(data["refs"])
    free_days = user_free_days.get(user_id, 0)
    await callback.message.edit_text(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"🔗 <code>https://t.me/kildear_vpn_bot?start=ref_{ref_code}</code>\n\n"
        f"📊 Приглашено друзей: <b>{refs_count}</b>\n"
        f"🎁 Бесплатных дней накоплено: <b>{free_days}</b>",
        reply_markup=get_referral_keyboard(ref_code),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "my_refs")
async def my_refs(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    data = get_or_create_ref_data(user_id)
    refs_count = len(data["refs"])
    free_days = user_free_days.get(user_id, 0)
    await callback.message.edit_text(
        f"📊 <b>Мои приглашения</b>\n\n"
        f"Приглашено друзей: <b>{refs_count}</b>\n"
        f"Бесплатных дней накоплено: <b>{free_days}</b>",
        reply_markup=get_referral_keyboard(data["ref_code"]),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🛡️ <b>Добро пожаловать в Kildear VPN!</b>\n\n"
        "👇 <b>Выберите тариф:</b>",
        reply_markup=get_plans_keyboard(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("copy_ref_"))
async def copy_ref(callback: CallbackQuery):
    await callback.answer("Ссылка скопирована!", show_alert=True)

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

    if plan["price"] == 0:
        await callback.message.answer("🎁 <b>Тестовый доступ на 2 дня!</b>", parse_mode="HTML")
        vpn_link = await create_vpn_user(user_id, plan["days"])
        if vpn_link:
            await callback.message.answer(
                f"✅ <b>VPN-ключ готов!</b>\n\n"
                f"🔗 <b>Ссылка:</b>\n<code>{vpn_link}</code>\n\n"
                f"📅 Подписка до: {(datetime.now() + timedelta(days=plan['days'])).strftime('%d.%m.%Y')}",
                reply_markup=get_plans_keyboard(),
                parse_mode="HTML"
            )
        else:
            await callback.message.answer(
                "❌ Не удалось создать VPN-ключ.",
                reply_markup=get_plans_keyboard()
            )
        return

    await callback.message.answer(
        f"⏳ Создаю счёт для оплаты...\n"
        f"Тариф: {plan['label']}\n"
        f"Стоимость: {plan['price']} ₽",
        parse_mode="HTML"
    )

    payment_url_card, payment_id_card = await create_yookassa_invoice(
        amount=plan["price"],
        description=f"Kildear VPN — {plan['label']} (карта)",
        order_id=order_id,
        payment_method="bank_card"
    )

    payment_url_sbp, payment_id_sbp = await create_yookassa_invoice(
        amount=plan["price"],
        description=f"Kildear VPN — {plan['label']} (СБП)",
        order_id=order_id,
        payment_method="sbp"
    )

    if not payment_url_card:
        await callback.message.answer("❌ Ошибка при создании счёта.")
        return

    user_orders[user_id] = {
        "order_id": order_id,
        "payment_id": payment_id_card,
        "payment_id_sbp": payment_id_sbp,
        "plan": plan_key,
        "days": plan["days"],
        "price": plan["price"],
        "devices": plan["devices"],
        "status": "pending"
    }

    await state.set_state(OrderState.waiting_for_payment)

    await callback.message.answer(
        f"💳 <b>Оплата через ЮKassa</b>\n\n"
        f"Тариф: {plan['label']}\n"
        f"Стоимость: {plan['price']} ₽\n"
        f"Устройств: {plan['devices']}\n\n"
        f"Выберите способ оплаты:",
        reply_markup=get_payment_keyboard(payment_url_card, payment_url_sbp),
        parse_mode="HTML"
    )

# ======================= ПРОВЕРКА / ОТМЕНА =======================
@dp.callback_query(F.data == "check_payment")
async def check_payment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = callback.from_user.id
    if user_id not in user_orders:
        await callback.message.answer("❌ Сначала выберите тариф.")
        return
    order = user_orders[user_id]
    if order["status"] == "paid":
        await callback.message.answer("✅ Этот заказ уже оплачен!")
        return

    status = await check_yookassa_payment(order["payment_id"])
    if status not in ["succeeded", "waiting_for_capture"] and order.get("payment_id_sbp"):
        status = await check_yookassa_payment(order["payment_id_sbp"])

    if status in ["succeeded", "waiting_for_capture"]:
        order["status"] = "paid"
        await callback.message.answer("⏳ Оплата подтверждена! Создаю VPN-ключ...")
        vpn_link = await create_vpn_user(user_id, order["days"])
        if vpn_link:
            await callback.message.answer(
                f"✅ <b>VPN-ключ готов!</b>\n\n"
                f"🔗 <b>Ссылка:</b>\n<code>{vpn_link}</code>\n\n"
                f"📅 Подписка до: {(datetime.now() + timedelta(days=order['days'])).strftime('%d.%m.%Y')}",
                parse_mode="HTML"
            )
            await activate_referral(user_id)
            del user_orders[user_id]
        else:
            await callback.message.answer("❌ Не удалось создать VPN-ключ.")
    else:
        await callback.message.answer(
            "⏳ Платёж ещё не проведён.\n"
            "Если уже оплатили, подождите 1-2 минуты."
        )

@dp.callback_query(F.data == "cancel_order")
async def cancel_order(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id in user_orders:
        del user_orders[user_id]
    await state.clear()
    await callback.answer("Заказ отменён")
    await callback.message.edit_text("❌ Заказ отменён.", reply_markup=get_plans_keyboard())

# ======================= ВЕБХУК =======================
app = Flask(__name__)

@app.route('/webhook/yookassa', methods=['POST'])
def yookassa_webhook():
    data = request.json
    if data.get("event") in ["payment.succeeded", "payment.waiting_for_capture"]:
        payment_data = data.get("object", {})
        order_id = payment_data.get("metadata", {}).get("order_id")
        if not order_id:
            return "No order_id", 400
        try:
            user_id = int(order_id.split('_')[0])
        except:
            return "Invalid order_id", 400
        if user_id not in user_orders:
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
                     f"🔗 <b>Ваш VPN-ключ:</b>\n<code>{vpn_link}</code>\n\n"
                     f"📅 Подписка до: {(datetime.now() + timedelta(days=order['days'])).strftime('%d.%m.%Y')}",
                parse_mode="HTML"
            ))
            asyncio.run(activate_referral(user_id))
        else:
            asyncio.run(bot.send_message(
                chat_id=user_id,
                text="❌ Оплата прошла, но не удалось создать VPN-ключ."
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
    asyncio.run(main())import os
import asyncio
import json
import logging
import uuid
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

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8656434661:AAHv3yKPvStdiSDcSiBJPxKaYSgmJLtBlpo")
PANEL_URL = "https://2.26.70.65:55347"
DB_URL = "http://2.26.70.65:8081"  # SQLite Web API

YKASSA_SHOP_ID = os.environ.get("YKASSA_SHOP_ID", "1434221")
YKASSA_SECRET_KEY = os.environ.get("YKASSA_SECRET_KEY", "live_fH2K3m3SygBdP8P6bjaOwkRj4UKl5FwsatLZC-PJKt8")
YKASSA_API_URL = "https://api.yookassa.ru/v3/payments"
YKASSA_WEBHOOK_URL = os.environ.get("YKASSA_WEBHOOK_URL", "https://kildear-vpn-bot.onrender.com/webhook/yookassa")

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
user_referrals = {}
user_free_days = {}
pending_referral = {}

# ======================= СОСТОЯНИЯ FSM =======================
class OrderState(StatesGroup):
    waiting_for_payment = State()

# ======================= КЛАВИАТУРА =======================
def get_plans_keyboard():
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
        ],
        [
            InlineKeyboardButton(
                text="👥 Реферальная программа",
                callback_data="referral_info"
            )
        ]
    ])
    return keyboard

def get_payment_keyboard(payment_url_card: str, payment_url_sbp: str = None):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить картой", url=payment_url_card)]
    ])
    if payment_url_sbp:
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text="📱 Оплатить через СБП", url=payment_url_sbp)]
        )
    keyboard.inline_keyboard.append(
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data="check_payment")]
    )
    keyboard.inline_keyboard.append(
        [InlineKeyboardButton(text="❌ Отменить заказ", callback_data="cancel_order")]
    )
    return keyboard

def get_referral_keyboard(ref_code: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔗 Скопировать ссылку",
                callback_data=f"copy_ref_{ref_code}"
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Мои приглашения",
                callback_data="my_refs"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="back_to_menu"
            )
        ]
    ])
    return keyboard

# ======================= СОЗДАНИЕ КЛИЕНТА ЧЕРЕЗ SQLITE WEB API =======================
async def create_vpn_user(telegram_id: int, days: int) -> Optional[str]:
    """Создаёт VPN-ключ через HTTP-API SQLite Web"""
    username = f"user_{telegram_id}_{int(datetime.now().timestamp())}"
    expiry_date = datetime.now() + timedelta(days=days)
    expiry_timestamp = int(expiry_date.timestamp() * 1000)

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Получаем первый активный Inbound
            async with session.get(f"{DB_URL}/api/query/inbounds") as resp:
                if resp.status != 200:
                    logging.error(f"Ошибка получения Inbound: {resp.status}")
                    return None
                data = await resp.json()
                
                if not data:
                    logging.error("Нет активных Inbound в базе")
                    return None
                
                inbound = data[0]
                inbound_id = inbound["id"]
                settings = json.loads(inbound["settings"])
                
                if "clients" not in settings:
                    settings["clients"] = []
                
                # Проверяем, не существует ли уже клиент
                for client in settings["clients"]:
                    if client.get("email") == username:
                        return f"{PANEL_URL}/sub/{username}"
                
                # Добавляем нового клиента
                new_client = {
                    "id": username,
                    "flow": "xtls-rprx-vision",
                    "email": username,
                    "limitIp": 5,
                    "totalGB": 0
                }
                
                settings["clients"].append(new_client)
                
                # 2. Обновляем Inbound через API
                update_data = {
                    "settings": json.dumps(settings)
                }
                
                async with session.post(
                    f"{DB_URL}/api/query/inbounds/{inbound_id}",
                    json=update_data
                ) as resp:
                    if resp.status == 200:
                        sub_url = f"{PANEL_URL}/sub/{username}"
                        return sub_url
                    else:
                        logging.error(f"Ошибка обновления Inbound: {resp.status}")
                        return None
    except Exception as e:
        logging.error(f"Ошибка при работе с БД: {e}")
        return None

# ======================= РАБОТА С ЮKASSA =======================
async def create_yookassa_invoice(amount: float, description: str, order_id: str, payment_method: str = "bank_card") -> tuple:
    idempotence_key = str(uuid.uuid4())

    if payment_method == "sbp":
        payment_method_data = {"type": "sbp"}
    else:
        payment_method_data = {"type": "bank_card"}

    payload = {
        "amount": {"value": str(amount), "currency": "RUB"},
        "payment_method_data": payment_method_data,
        "confirmation": {"type": "redirect", "return_url": "https://t.me/kildear_vpn_bot"},
        "description": description,
        "metadata": {"order_id": order_id, "telegram_id": order_id.split('_')[0]},
        "capture": True
    }

    auth = aiohttp.BasicAuth(YKASSA_SHOP_ID, YKASSA_SECRET_KEY)
    headers = {"Content-Type": "application/json", "Idempotence-Key": idempotence_key}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(YKASSA_API_URL, json=payload, headers=headers, auth=auth) as resp:
                data = await resp.json()
                if data.get("status") in ["pending", "waiting_for_capture"]:
                    return data["confirmation"]["confirmation_url"], data["id"]
                else:
                    return None, None
        except Exception as e:
            logging.error(f"Ошибка ЮKassa: {e}")
            return None, None

async def check_yookassa_payment(payment_id: str) -> str:
    auth = aiohttp.BasicAuth(YKASSA_SHOP_ID, YKASSA_SECRET_KEY)
    check_url = f"{YKASSA_API_URL}/{payment_id}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(check_url, auth=auth) as resp:
                data = await resp.json()
                return data.get("status")
        except Exception as e:
            return None

# ======================= РЕФЕРАЛЬНАЯ СИСТЕМА =======================
def generate_ref_code(user_id: int) -> str:
    import hashlib
    return hashlib.md5(f"{user_id}_{datetime.now().timestamp()}".encode()).hexdigest()[:8]

def get_or_create_ref_data(user_id: int):
    if user_id not in user_referrals:
        user_referrals[user_id] = {"ref_code": generate_ref_code(user_id), "refs": []}
        user_free_days[user_id] = 0
    return user_referrals[user_id]

async def add_free_days(user_id: int, days: int):
    if user_id not in user_free_days:
        user_free_days[user_id] = 0
    user_free_days[user_id] += days

async def process_referral(new_user_id: int, ref_code: str):
    inviter_id = None
    for uid, data in user_referrals.items():
        if data["ref_code"] == ref_code:
            inviter_id = uid
            break
    if not inviter_id or inviter_id == new_user_id:
        return
    if new_user_id in user_referrals[inviter_id]["refs"]:
        return
    pending_referral[new_user_id] = inviter_id

async def activate_referral(user_id: int):
    if user_id not in pending_referral:
        return
    inviter_id = pending_referral[user_id]
    if inviter_id not in user_referrals:
        return
    if user_id not in user_referrals[inviter_id]["refs"]:
        user_referrals[inviter_id]["refs"].append(user_id)
        await add_free_days(inviter_id, 7)
        await bot.send_message(
            chat_id=inviter_id,
            text=f"🎉 <b>Ваш друг купил подписку!</b>\n\n"
                 f"Вы получили <b>7 дней</b> бесплатной подписки.\n"
                 f"Всего бесплатных дней: <b>{user_free_days.get(inviter_id, 0)}</b>",
            parse_mode="HTML"
        )
    del pending_referral[user_id]

# ======================= ОБРАБОТЧИКИ КОМАНД =======================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    args = message.text.split()
    user_id = message.from_user.id
    if len(args) > 1 and args[1].startswith("ref_"):
        ref_code = args[1].replace("ref_", "")
        await process_referral(user_id, ref_code)
    get_or_create_ref_data(user_id)
    await message.answer(
        "🛡️ <b>Добро пожаловать в Kildear VPN!</b>\n\n"
        "👇 <b>Выберите тариф:</b>",
        reply_markup=get_plans_keyboard(),
        parse_mode="HTML"
    )

@dp.message(Command("docs"))
async def cmd_docs(message: Message):
    await message.answer(
        "📄 <b>Документы Kildear VPN</b>\n\n"
        "Политика конфиденциальности: https://kildear-vpn-atst.onrender.com/privacy\n"
        "Пользовательское соглашение: https://kildear-vpn-atst.onrender.com/terms\n\n"
        "📧 Контакты: kildearVPN@yandex.ru",
        parse_mode="HTML"
    )

@dp.message(Command("ref"))
async def cmd_ref(message: Message):
    user_id = message.from_user.id
    data = get_or_create_ref_data(user_id)
    ref_code = data["ref_code"]
    refs_count = len(data["refs"])
    free_days = user_free_days.get(user_id, 0)
    await message.answer(
        f"👥 <b>Ваша реферальная ссылка</b>\n\n"
        f"🔗 <code>https://t.me/kildear_vpn_bot?start=ref_{ref_code}</code>\n\n"
        f"📊 Приглашено друзей: <b>{refs_count}</b>\n"
        f"🎁 Бесплатных дней накоплено: <b>{free_days}</b>",
        parse_mode="HTML"
    )

# ======================= ОБРАБОТЧИКИ КНОПОК =======================
@dp.callback_query(F.data == "referral_info")
async def referral_info(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    data = get_or_create_ref_data(user_id)
    ref_code = data["ref_code"]
    refs_count = len(data["refs"])
    free_days = user_free_days.get(user_id, 0)
    await callback.message.edit_text(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"🔗 <code>https://t.me/kildear_vpn_bot?start=ref_{ref_code}</code>\n\n"
        f"📊 Приглашено друзей: <b>{refs_count}</b>\n"
        f"🎁 Бесплатных дней накоплено: <b>{free_days}</b>",
        reply_markup=get_referral_keyboard(ref_code),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "my_refs")
async def my_refs(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    data = get_or_create_ref_data(user_id)
    refs_count = len(data["refs"])
    free_days = user_free_days.get(user_id, 0)
    await callback.message.edit_text(
        f"📊 <b>Мои приглашения</b>\n\n"
        f"Приглашено друзей: <b>{refs_count}</b>\n"
        f"Бесплатных дней накоплено: <b>{free_days}</b>",
        reply_markup=get_referral_keyboard(data["ref_code"]),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🛡️ <b>Добро пожаловать в Kildear VPN!</b>\n\n"
        "👇 <b>Выберите тариф:</b>",
        reply_markup=get_plans_keyboard(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("copy_ref_"))
async def copy_ref(callback: CallbackQuery):
    await callback.answer("Ссылка скопирована!", show_alert=True)

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

    if plan["price"] == 0:
        await callback.message.answer("🎁 <b>Тестовый доступ на 2 дня!</b>", parse_mode="HTML")
        vpn_link = await create_vpn_user(user_id, plan["days"])
        if vpn_link:
            await callback.message.answer(
                f"✅ <b>VPN-ключ готов!</b>\n\n"
                f"🔗 <b>Ссылка:</b>\n<code>{vpn_link}</code>\n\n"
                f"📅 Подписка до: {(datetime.now() + timedelta(days=plan['days'])).strftime('%d.%m.%Y')}",
                reply_markup=get_plans_keyboard(),
                parse_mode="HTML"
            )
        else:
            await callback.message.answer(
                "❌ Не удалось создать VPN-ключ.",
                reply_markup=get_plans_keyboard()
            )
        return

    await callback.message.answer(
        f"⏳ Создаю счёт для оплаты...\n"
        f"Тариф: {plan['label']}\n"
        f"Стоимость: {plan['price']} ₽",
        parse_mode="HTML"
    )

    payment_url_card, payment_id_card = await create_yookassa_invoice(
        amount=plan["price"],
        description=f"Kildear VPN — {plan['label']} (карта)",
        order_id=order_id,
        payment_method="bank_card"
    )

    payment_url_sbp, payment_id_sbp = await create_yookassa_invoice(
        amount=plan["price"],
        description=f"Kildear VPN — {plan['label']} (СБП)",
        order_id=order_id,
        payment_method="sbp"
    )

    if not payment_url_card:
        await callback.message.answer("❌ Ошибка при создании счёта.")
        return

    user_orders[user_id] = {
        "order_id": order_id,
        "payment_id": payment_id_card,
        "payment_id_sbp": payment_id_sbp,
        "plan": plan_key,
        "days": plan["days"],
        "price": plan["price"],
        "devices": plan["devices"],
        "status": "pending"
    }

    await state.set_state(OrderState.waiting_for_payment)

    await callback.message.answer(
        f"💳 <b>Оплата через ЮKassa</b>\n\n"
        f"Тариф: {plan['label']}\n"
        f"Стоимость: {plan['price']} ₽\n"
        f"Устройств: {plan['devices']}\n\n"
        f"Выберите способ оплаты:",
        reply_markup=get_payment_keyboard(payment_url_card, payment_url_sbp),
        parse_mode="HTML"
    )

# ======================= ПРОВЕРКА / ОТМЕНА =======================
@dp.callback_query(F.data == "check_payment")
async def check_payment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = callback.from_user.id
    if user_id not in user_orders:
        await callback.message.answer("❌ Сначала выберите тариф.")
        return
    order = user_orders[user_id]
    if order["status"] == "paid":
        await callback.message.answer("✅ Этот заказ уже оплачен!")
        return

    status = await check_yookassa_payment(order["payment_id"])
    if status not in ["succeeded", "waiting_for_capture"] and order.get("payment_id_sbp"):
        status = await check_yookassa_payment(order["payment_id_sbp"])

    if status in ["succeeded", "waiting_for_capture"]:
        order["status"] = "paid"
        await callback.message.answer("⏳ Оплата подтверждена! Создаю VPN-ключ...")
        vpn_link = await create_vpn_user(user_id, order["days"])
        if vpn_link:
            await callback.message.answer(
                f"✅ <b>VPN-ключ готов!</b>\n\n"
                f"🔗 <b>Ссылка:</b>\n<code>{vpn_link}</code>\n\n"
                f"📅 Подписка до: {(datetime.now() + timedelta(days=order['days'])).strftime('%d.%m.%Y')}",
                parse_mode="HTML"
            )
            await activate_referral(user_id)
            del user_orders[user_id]
        else:
            await callback.message.answer("❌ Не удалось создать VPN-ключ.")
    else:
        await callback.message.answer(
            "⏳ Платёж ещё не проведён.\n"
            "Если уже оплатили, подождите 1-2 минуты."
        )

@dp.callback_query(F.data == "cancel_order")
async def cancel_order(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id in user_orders:
        del user_orders[user_id]
    await state.clear()
    await callback.answer("Заказ отменён")
    await callback.message.edit_text("❌ Заказ отменён.", reply_markup=get_plans_keyboard())

# ======================= ВЕБХУК =======================
app = Flask(__name__)

@app.route('/webhook/yookassa', methods=['POST'])
def yookassa_webhook():
    data = request.json
    if data.get("event") in ["payment.succeeded", "payment.waiting_for_capture"]:
        payment_data = data.get("object", {})
        order_id = payment_data.get("metadata", {}).get("order_id")
        if not order_id:
            return "No order_id", 400
        try:
            user_id = int(order_id.split('_')[0])
        except:
            return "Invalid order_id", 400
        if user_id not in user_orders:
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
                     f"🔗 <b>Ваш VPN-ключ:</b>\n<code>{vpn_link}</code>\n\n"
                     f"📅 Подписка до: {(datetime.now() + timedelta(days=order['days'])).strftime('%d.%m.%Y')}",
                parse_mode="HTML"
            ))
            asyncio.run(activate_referral(user_id))
        else:
            asyncio.run(bot.send_message(
                chat_id=user_id,
                text="❌ Оплата прошла, но не удалось создать VPN-ключ."
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
