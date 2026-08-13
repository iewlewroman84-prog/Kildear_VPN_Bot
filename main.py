import os
import asyncio
import json
import logging
import hashlib
import hmac
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

PANEL_URL = "https://my.xorek.cloud:2053"
PANEL_USERNAME = os.environ.get("PANEL_USERNAME", "admin")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "password")

# ЮKassa
YKASSA_SHOP_ID = os.environ.get("YKASSA_SHOP_ID", "1434221")
YKASSA_SECRET_KEY = os.environ.get("YKASSA_SECRET_KEY", "ВАШ_СЕКРЕТНЫЙ_КЛЮЧ")
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

# Хранилище данных
user_orders = {}
user_referrals = {}  # {user_id: {"ref_code": "123456789", "refs": [user_id1, user_id2]}}
user_free_days = {}  # {user_id: free_days}
pending_referral = {}  # {user_id: inviter_id} для отслеживания переходов по ссылке

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

def get_payment_keyboard(payment_url: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить через ЮKassa", url=payment_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data="check_payment")],
        [InlineKeyboardButton(text="❌ Отменить заказ", callback_data="cancel_order")]
    ])
    return keyboard

# ======================= РАБОТА С ПАНЕЛЬЮ =======================
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

# ======================= РАБОТА С ЮKASSA =======================
async def create_yookassa_invoice(amount: float, description: str, order_id: str) -> tuple:
    idempotence_key = str(uuid.uuid4())

    payload = {
        "amount": {
            "value": str(amount),
            "currency": "RUB"
        },
        "payment_method_data": {
            "type": "bank_card"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": "https://t.me/kildear_vpn_bot"
        },
        "description": description,
        "metadata": {
            "order_id": order_id,
            "telegram_id": order_id.split('_')[0]
        },
        "capture": True
    }

    auth = aiohttp.BasicAuth(YKASSA_SHOP_ID, YKASSA_SECRET_KEY)
    headers = {
        "Content-Type": "application/json",
        "Idempotence-Key": idempotence_key
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(YKASSA_API_URL, json=payload, headers=headers, auth=auth) as resp:
                data = await resp.json()
                logging.info(f"Ответ ЮKassa: {data}")
                if data.get("status") in ["pending", "waiting_for_capture"]:
                    return data["confirmation"]["confirmation_url"], data["id"]
                else:
                    logging.error(f"Ошибка ЮKassa: {data.get('description')}")
                    return None, None
        except Exception as e:
            logging.error(f"Ошибка соединения с ЮKassa: {e}")
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
            logging.error(f"Ошибка проверки платежа: {e}")
            return None

# ======================= РЕФЕРАЛЬНАЯ СИСТЕМА =======================
def generate_ref_code(user_id: int) -> str:
    """Генерирует уникальный реферальный код"""
    import hashlib
    return hashlib.md5(f"{user_id}_{datetime.now().timestamp()}".encode()).hexdigest()[:8]

def get_or_create_ref_data(user_id: int):
    """Получает или создаёт реферальные данные для пользователя"""
    if user_id not in user_referrals:
        user_referrals[user_id] = {
            "ref_code": generate_ref_code(user_id),
            "refs": []
        }
        user_free_days[user_id] = 0
    return user_referrals[user_id]

async def add_free_days(user_id: int, days: int):
    """Добавляет бесплатные дни пользователю"""
    if user_id not in user_free_days:
        user_free_days[user_id] = 0
    user_free_days[user_id] += days
    logging.info(f"Пользователю {user_id} добавлено {days} бесплатных дней. Всего: {user_free_days[user_id]}")

async def process_referral(new_user_id: int, ref_code: str):
    """Обрабатывает переход по реферальной ссылке"""
    # Ищем пользователя с таким реферальным кодом
    inviter_id = None
    for uid, data in user_referrals.items():
        if data["ref_code"] == ref_code:
            inviter_id = uid
            break

    if not inviter_id or inviter_id == new_user_id:
        return

    # Проверяем, не приглашал ли уже этот пользователь
    if new_user_id in user_referrals[inviter_id]["refs"]:
        return

    # Сохраняем информацию о том, кто пригласил
    pending_referral[new_user_id] = inviter_id
    logging.info(f"Пользователь {new_user_id} перешел по ссылке от {inviter_id}")

async def activate_referral(user_id: int):
    """Активирует реферальную связь после покупки"""
    if user_id not in pending_referral:
        return

    inviter_id = pending_referral[user_id]
    if inviter_id not in user_referrals:
        return

    # Добавляем пользователя в список приглашённых
    if user_id not in user_referrals[inviter_id]["refs"]:
        user_referrals[inviter_id]["refs"].append(user_id)

        # Начисляем 7 бесплатных дней пригласившему
        await add_free_days(inviter_id, 7)

        # Уведомляем пригласившего
        await bot.send_message(
            chat_id=inviter_id,
            text=f"🎉 <b>Ваш друг купил подписку!</b>\n\n"
                 f"Вы получили <b>7 дней</b> бесплатной подписки.\n"
                 f"Всего бесплатных дней: <b>{user_free_days.get(inviter_id, 0)}</b>\n\n"
                 f"Приглашайте ещё друзей и получайте больше бонусов! 🚀",
            parse_mode="HTML"
        )

    # Удаляем из ожидания
    del pending_referral[user_id]

# ======================= ОБРАБОТЧИКИ КОМАНД =======================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    args = message.text.split()
    user_id = message.from_user.id

    # Проверяем, есть ли реферальный код
    if len(args) > 1 and args[1].startswith("ref_"):
        ref_code = args[1].replace("ref_", "")
        await process_referral(user_id, ref_code)

    # Убеждаемся, что у пользователя есть реферальные данные
    get_or_create_ref_data(user_id)

    await message.answer(
        "🛡️ <b>Добро пожаловать в Kildear VPN!</b>\n\n"
        "Мы предоставляем защищённый доступ в интернет.\n"
        "Ваши данные надёжно зашифрованы.\n\n"
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
        f"🎁 Бесплатных дней накоплено: <b>{free_days}</b>\n\n"
        f"<i>За каждого друга, купившего подписку, вы получаете 7 дней бесплатного доступа!</i>",
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
        f"Приглашайте друзей и получайте <b>7 дней бесплатной подписки</b> за каждого друга, купившего любой тариф!\n\n"
        f"🔗 <b>Ваша ссылка:</b>\n"
        f"<code>https://t.me/kildear_vpn_bot?start=ref_{ref_code}</code>\n\n"
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
        f"Бесплатных дней накоплено: <b>{free_days}</b>\n\n"
        f"<i>Каждый друг, купивший подписку, приносит вам 7 дней бесплатного доступа!</i>",
        reply_markup=get_referral_keyboard(get_or_create_ref_data(user_id)["ref_code"]),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🛡️ <b>Добро пожаловать в Kildear VPN!</b>\n\n"
        "Мы предоставляем защищённый доступ в интернет.\n"
        "Ваши данные надёжно зашифрованы.\n\n"
        "👇 <b>Выберите тариф:</b>",
        reply_markup=get_plans_keyboard(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("copy_ref_"))
async def copy_ref(callback: CallbackQuery):
    await callback.answer("Ссылка скопирована! Отправьте её другу.", show_alert=True)

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
        # Проверяем, есть ли у пользователя накопленные бесплатные дни
        free_days = user_free_days.get(user_id, 0)
        if free_days > 0:
            # Можно использовать бесплатные дни вместо тестового периода
            await callback.message.answer(
                f"🎁 <b>У вас есть {free_days} бесплатных дней!</b>\n\n"
                f"Хотите активировать их сейчас?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ Активировать {free_days} дней",
                            callback_data="activate_free_days"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🎁 Использовать тестовый 2 дня",
                            callback_data="use_test_2d"
                        )
                    ]
                ]),
                parse_mode="HTML"
            )
            return

        # Если нет бесплатных дней, выдаём тестовый период
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
                f"3. Наслаждайтесь защищённым интернетом!\n\n"
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

    payment_url, payment_id = await create_yookassa_invoice(
        amount=plan["price"],
        description=f"Kildear VPN — {plan['label']}",
        order_id=order_id
    )

    if not payment_url:
        await callback.message.answer("❌ Ошибка при создании счёта. Попробуйте позже.")
        return

    user_orders[user_id] = {
        "order_id": order_id,
        "payment_id": payment_id,
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
        f"Нажмите кнопку ниже, чтобы оплатить.\n"
        f"После оплаты нажмите «Проверить оплату».",
        reply_markup=get_payment_keyboard(payment_url),
        parse_mode="HTML"
    )

# ======================= АКТИВАЦИЯ БЕСПЛАТНЫХ ДНЕЙ =======================
@dp.callback_query(F.data == "activate_free_days")
async def activate_free_days(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    free_days = user_free_days.get(user_id, 0)

    if free_days <= 0:
        await callback.message.answer("❌ У вас нет накопленных бесплатных дней.")
        return

    vpn_link = await create_vpn_user(user_id, free_days)

    if vpn_link:
        user_free_days[user_id] = 0
        await callback.message.answer(
            f"✅ <b>Бесплатные дни активированы!</b>\n\n"
            f"🔗 <b>Ссылка для подключения:</b>\n"
            f"<code>{vpn_link}</code>\n\n"
            f"📱 <b>Инструкция:</b>\n"
            f"1. Скачайте приложение V2RayNG или Hiddify\n"
            f"2. Скопируйте ссылку и вставьте в приложение\n"
            f"3. Наслаждайтесь защищённым интернетом!\n\n"
            f"📅 Подписка активна до: {(datetime.now() + timedelta(days=free_days)).strftime('%d.%m.%Y')}",
            reply_markup=get_plans_keyboard(),
            parse_mode="HTML"
        )
    else:
        await callback.message.answer(
            "❌ Не удалось создать VPN-ключ. Попробуйте позже."
        )

@dp.callback_query(F.data == "use_test_2d")
async def use_test_2d(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id

    vpn_link = await create_vpn_user(user_id, 2)

    if vpn_link:
        await callback.message.answer(
            f"🎁 <b>Тестовый доступ на 2 дня!</b>\n\n"
            f"✅ <b>VPN-ключ готов!</b>\n\n"
            f"🔗 <b>Ссылка для подключения:</b>\n"
            f"<code>{vpn_link}</code>\n\n"
            f"📱 <b>Инструкция:</b>\n"
            f"1. Скачайте приложение V2RayNG или Hiddify\n"
            f"2. Скопируйте ссылку и вставьте в приложение\n"
            f"3. Наслаждайтесь защищённым интернетом!\n\n"
            f"📅 Подписка активна до: {(datetime.now() + timedelta(days=2)).strftime('%d.%m.%Y')}\n\n"
            f"💡 После теста выберите платный тариф 👇",
            reply_markup=get_plans_keyboard(),
            parse_mode="HTML"
        )
    else:
        await callback.message.answer(
            "❌ Не удалось создать VPN-ключ. Попробуйте позже."
        )

# ======================= ПРОВЕРКА / ОТМЕНА =======================
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

    status = await check_yookassa_payment(order["payment_id"])

    if status in ["succeeded", "waiting_for_capture"]:
        order["status"] = "paid"
        await callback.message.answer("⏳ Оплата подтверждена! Создаю VPN-ключ...")

        vpn_link = await create_vpn_user(user_id, order["days"])

        if vpn_link:
            await callback.message.answer(
                f"✅ <b>VPN-ключ готов!</b>\n\n"
                f"🔗 <b>Ссылка для подключения:</b>\n"
                f"<code>{vpn_link}</code>\n\n"
                f"📱 <b>Инструкция:</b>\n"
                f"1. Скачайте приложение V2RayNG или Hiddify\n"
                f"2. Скопируйте ссылку и вставьте в приложение\n"
                f"3. Наслаждайтесь защищённым интернетом!\n\n"
                f"📅 Подписка активна до: {(datetime.now() + timedelta(days=order['days'])).strftime('%d.%m.%Y')}",
                parse_mode="HTML"
            )

            # Активируем реферальную связь (если пользователь был приглашён)
            await activate_referral(user_id)

            del user_orders[user_id]
        else:
            await callback.message.answer(
                "❌ Не удалось создать VPN-ключ. Обратитесь к администратору."
            )
    else:
        await callback.message.answer(
            "⏳ Платёж ещё не проведён.\n"
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

# ======================= ВЕБХУК ДЛЯ ЮKASSA =======================
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

            # Активируем реферальную связь
            asyncio.run(activate_referral(user_id))
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
