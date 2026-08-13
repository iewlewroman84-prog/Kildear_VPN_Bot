import os
import logging
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram import F
import aiohttp
import base64
import hashlib

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8656434661:AAHv3yKPvStdiSDcSiBJPxKaYSgmJLtBlpo")

# ЮKassa
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

# ======================= НАСТРОЙКА ЛОГГИРОВАНИЯ =======================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ======================= ИНИЦИАЛИЗАЦИЯ БОТА =======================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ======================= БАЗА ДАННЫХ =======================
def init_db():
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    
    # Таблица пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, 
                  username TEXT,
                  first_name TEXT,
                  last_name TEXT,
                  registration_date TIMESTAMP,
                  current_subscription TEXT,
                  subscription_end TIMESTAMP,
                  devices INTEGER DEFAULT 1)''')
    
    # Таблица подписок
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  plan_id TEXT,
                  start_date TIMESTAMP,
                  end_date TIMESTAMP,
                  status TEXT,
                  price INTEGER,
                  payment_id TEXT,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    
    # Таблица платежей
    c.execute('''CREATE TABLE IF NOT EXISTS payments
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  payment_id TEXT UNIQUE,
                  user_id INTEGER,
                  plan_id TEXT,
                  amount INTEGER,
                  status TEXT,
                  created_at TIMESTAMP,
                  paid_at TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    
    conn.commit()
    conn.close()

init_db()

# ======================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =======================
def get_user(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    
    if user:
        return {
            'user_id': user[0],
            'username': user[1],
            'first_name': user[2],
            'last_name': user[3],
            'registration_date': user[4],
            'current_subscription': user[5],
            'subscription_end': user[6],
            'devices': user[7]
        }
    return None

def create_user(user_id: int, username: str, first_name: str, last_name: str = None):
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("""INSERT OR IGNORE INTO users 
                 (user_id, username, first_name, last_name, registration_date, devices) 
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (user_id, username, first_name, last_name, datetime.now(), 1))
    conn.commit()
    conn.close()

def get_active_subscription(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("""SELECT * FROM subscriptions 
                 WHERE user_id = ? AND status = 'active' AND end_date > datetime('now')
                 ORDER BY end_date DESC LIMIT 1""", (user_id,))
    sub = c.fetchone()
    conn.close()
    
    if sub:
        return {
            'id': sub[0],
            'user_id': sub[1],
            'plan_id': sub[2],
            'start_date': sub[3],
            'end_date': sub[4],
            'status': sub[5],
            'price': sub[6],
            'payment_id': sub[7]
        }
    return None

def create_subscription(user_id: int, plan_id: str, price: int, payment_id: str = None):
    plan = PLANS[plan_id]
    start_date = datetime.now()
    end_date = start_date + timedelta(days=plan['days'])
    
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    
    # Деактивируем старые подписки
    c.execute("UPDATE subscriptions SET status = 'inactive' WHERE user_id = ? AND status = 'active'", (user_id,))
    
    # Создаем новую подписку
    c.execute("""INSERT INTO subscriptions 
                 (user_id, plan_id, start_date, end_date, status, price, payment_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
              (user_id, plan_id, start_date, end_date, 'active', price, payment_id))
    
    # Обновляем пользователя
    c.execute("""UPDATE users 
                 SET current_subscription = ?, subscription_end = ?, devices = ?
                 WHERE user_id = ?""",
              (plan_id, end_date, plan['devices'], user_id))
    
    conn.commit()
    conn.close()

def create_payment(user_id: int, plan_id: str, amount: int, payment_id: str):
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("""INSERT INTO payments 
                 (payment_id, user_id, plan_id, amount, status, created_at)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (payment_id, user_id, plan_id, amount, 'pending', datetime.now()))
    conn.commit()
    conn.close()

def update_payment_status(payment_id: str, status: str):
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("""UPDATE payments 
                 SET status = ?, paid_at = ?
                 WHERE payment_id = ?""",
              (status, datetime.now() if status == 'succeeded' else None, payment_id))
    conn.commit()
    conn.close()

# ======================= КЛАВИАТУРЫ =======================
def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Купить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="my_profile")],
        [InlineKeyboardButton(text="🆘 Помощь", callback_data="help")]
    ])
    return keyboard

def get_plans_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for plan_id, plan in PLANS.items():
        price_text = f"{plan['price']} ₽" if plan['price'] > 0 else "Бесплатно"
        button_text = f"{plan['emoji']} {plan['label']} — {price_text}"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=button_text, callback_data=f"plan_{plan_id}")
        ])
    
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    ])
    
    return keyboard

def get_payment_keyboard(payment_id: str, confirmation_url: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=confirmation_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_payment_{payment_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_plans")]
    ])
    return keyboard

# ======================= ОБРАБОТЧИКИ КОМАНД =======================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    # Регистрируем пользователя
    create_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    
    # Приветственное сообщение
    welcome_text = f"""
🌟 <b>Добро пожаловать в VPN сервис Kildear!</b>

Защитите свои данные и получите доступ к любому контенту.

📋 <b>Доступные тарифы:</b>
• 🎁 2 дня бесплатно
• 🔥 1 месяц — 139 ₽
• ⭐ 3 месяца — 469 ₽
• 💎 1 год — 899 ₽

Используйте кнопки ниже для управления подпиской.
"""
    
    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>\n\nВыберите действие:",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "buy_subscription")
async def show_plans(callback: CallbackQuery):
    # Проверяем активную подписку
    active_sub = get_active_subscription(callback.from_user.id)
    
    plans_text = "📱 <b>Выберите тарифный план:</b>\n\n"
    
    if active_sub:
        end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
        days_left = (end_date - datetime.now()).days
        plans_text += f"✅ <i>У вас активна подписка до {end_date.strftime('%d.%m.%Y')} (осталось {days_left} дней)</i>\n\n"
    
    for plan_id, plan in PLANS.items():
        price_text = f"{plan['price']} ₽" if plan['price'] > 0 else "Бесплатно"
        plans_text += f"{plan['emoji']} <b>{plan['label']}</b>\n"
        plans_text += f"   • 📅 {plan['days']} дней\n"
        plans_text += f"   • 📱 {plan['devices']} устройств\n"
        plans_text += f"   • 💰 {price_text}\n\n"
    
    await callback.message.edit_text(
        plans_text,
        reply_markup=get_plans_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("plan_"))
async def process_plan_selection(callback: CallbackQuery):
    plan_id = callback.data.replace("plan_", "")
    plan = PLANS.get(plan_id)
    
    if not plan:
        await callback.answer("❌ Тариф не найден")
        return
    
    user_id = callback.from_user.id
    
    # Если бесплатный тариф - активируем сразу
    if plan['price'] == 0:
        # Проверяем, не использовал ли уже пользователь бесплатный период
        conn = sqlite3.connect('subscriptions.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id = ? AND plan_id = '2d'", (user_id,))
        count = c.fetchone()[0]
        conn.close()
        
        if count > 0:
            await callback.answer("❌ Вы уже использовали бесплатный период!", show_alert=True)
            return
        
        # Активируем бесплатную подписку
        create_subscription(user_id, plan_id, 0)
        
        await callback.message.edit_text(
            f"🎉 <b>Поздравляем! Бесплатная подписка активирована!</b>\n\n"
            f"📅 Период: {plan['days']} дней\n"
            f"📱 Устройств: {plan['devices']}\n\n"
            f"Наслаждайтесь безопасным интернетом! 🔒",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    # Создаем платеж в ЮKassa
    try:
        payment_data = await create_yookassa_payment(
            user_id=user_id,
            plan_id=plan_id,
            amount=plan['price']
        )
        
        # Сохраняем платеж в БД
        create_payment(user_id, plan_id, plan['price'], payment_data['id'])
        
        # Показываем пользователю
        payment_text = f"""
💳 <b>Оплата подписки</b>

Тариф: {plan['label']}
Сумма: {plan['price']} ₽
Период: {plan['days']} дней

Нажмите на кнопку ниже для оплаты.
После оплаты нажмите "Проверить оплату".
"""
        
        await callback.message.edit_text(
            payment_text,
            reply_markup=get_payment_keyboard(payment_data['id'], payment_data['confirmation_url']),
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка при создании платежа</b>\n\nПожалуйста, попробуйте позже.",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("check_payment_"))
async def check_payment_status(callback: CallbackQuery):
    payment_id = callback.data.replace("check_payment_", "")
    user_id = callback.from_user.id
    
    try:
        status = await check_yookassa_payment(payment_id)
        
        if status == 'succeeded':
            # Получаем информацию о платеже из БД
            conn = sqlite3.connect('subscriptions.db')
            c = conn.cursor()
            c.execute("SELECT plan_id, amount FROM payments WHERE payment_id = ?", (payment_id,))
            payment_info = c.fetchone()
            conn.close()
            
            if payment_info:
                plan_id = payment_info[0]
                update_payment_status(payment_id, 'succeeded')
                create_subscription(user_id, plan_id, payment_info[1], payment_id)
                
                plan = PLANS[plan_id]
                await callback.message.edit_text(
                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                    f"🎉 Подписка на тариф «{plan['label']}» активирована!\n"
                    f"📅 Период: {plan['days']} дней\n"
                    f"📱 Устройств: {plan['devices']}\n\n"
                    f"Спасибо за покупку! 🔒",
                    reply_markup=get_main_keyboard(),
                    parse_mode="HTML"
                )
            else:
                await callback.answer("❌ Платеж не найден", show_alert=True)
        elif status == 'pending':
            await callback.answer("⏳ Платеж еще не обработан. Попробуйте позже.", show_alert=True)
        else:
            await callback.answer("❌ Платеж не прошел. Попробуйте снова.", show_alert=True)
            
    except Exception as e:
        logger.error(f"Ошибка при проверке платежа: {e}")
        await callback.answer("❌ Ошибка при проверке платежа", show_alert=True)

@dp.callback_query(F.data == "my_profile")
async def show_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    active_sub = get_active_subscription(user_id)
    
    profile_text = f"""
👤 <b>Ваш профиль</b>

🆔 ID: {user['user_id']}
📝 Имя: {user['first_name']}
"""
    
    if user['username']:
        profile_text += f"@ {user['username']}\n"
    
    if active_sub:
        end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
        days_left = (end_date - datetime.now()).days
        plan = PLANS.get(active_sub['plan_id'], {})
        
        profile_text += f"""
📱 <b>Текущая подписка</b>
• Тариф: {plan.get('label', active_sub['plan_id'])}
• Действует до: {end_date.strftime('%d.%m.%Y')}
• Осталось дней: {days_left if days_left > 0 else 0}
• Устройств: {plan.get('devices', 1)}
"""
    else:
        profile_text += "\n❌ <b>Нет активной подписки</b>"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(
        profile_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "help")
async def show_help(callback: CallbackQuery):
    help_text = """
🆘 <b>Помощь</b>

<b>Как купить подписку?</b>
1. Нажмите "📱 Купить подписку"
2. Выберите тариф
3. Оплатите через ЮKassa

<b>Бесплатный период</b>
Вы можете получить 2 дня бесплатно, чтобы протестировать сервис.

<b>Вопросы и поддержка</b>
Если у вас возникли проблемы, напишите нам:
📧 support@kildear.com

<b>Важно:</b>
• После оплаты подписка активируется автоматически
• Подписка продлевается по истечении срока
• VPN работает на всех устройствах
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(
        help_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

# ======================= ФУНКЦИИ ДЛЯ РАБОТЫ С ЮKASSA =======================
async def create_yookassa_payment(user_id: int, plan_id: str, amount: int) -> Dict:
    """Создание платежа в ЮKassa"""
    idempotence_key = hashlib.md5(f"{user_id}_{plan_id}_{datetime.now().timestamp()}".encode()).hexdigest()
    
    payment_data = {
        "amount": {
            "value": f"{amount}.00",
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": YKASSA_WEBHOOK_URL
        },
        "capture": True,
        "description": f"Подписка VPN {plan_id} для пользователя {user_id}",
        "metadata": {
            "user_id": str(user_id),
            "plan_id": plan_id
        }
    }
    
    auth = base64.b64encode(f"{YKASSA_SHOP_ID}:{YKASSA_SECRET_KEY}".encode()).decode()
    headers = {
        "Content-Type": "application/json",
        "Idempotence-Key": idempotence_key,
        "Authorization": f"Basic {auth}"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(YKASSA_API_URL, json=payment_data, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                return {
                    'id': data['id'],
                    'confirmation_url': data['confirmation']['confirmation_url']
                }
            else:
                error_text = await response.text()
                logger.error(f"Ошибка ЮKassa: {response.status} - {error_text}")
                raise Exception(f"Ошибка создания платежа: {response.status}")

async def check_yookassa_payment(payment_id: str) -> str:
    """Проверка статуса платежа"""
    auth = base64.b64encode(f"{YKASSA_SHOP_ID}:{YKASSA_SECRET_KEY}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{YKASSA_API_URL}/{payment_id}", headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                return data.get('status', 'unknown')
            else:
                error_text = await response.text()
                logger.error(f"Ошибка проверки платежа: {response.status} - {error_text}")
                return 'error'

# ======================= WEBHOOK ДЛЯ ЮKASSA =======================
from aiohttp import web

async def yookassa_webhook(request):
    """Обработка вебхуков от ЮKassa"""
    try:
        data = await request.json()
        logger.info(f"Получен вебхук: {data}")
        
        event = data.get('event')
        payment_data = data.get('object', {})
        payment_id = payment_data.get('id')
        status = payment_data.get('status')
        
        if event == 'payment.succeeded':
            # Обновляем статус платежа
            update_payment_status(payment_id, 'succeeded')
            
            # Получаем информацию о платеже
            conn = sqlite3.connect('subscriptions.db')
            c = conn.cursor()
            c.execute("SELECT user_id, plan_id, amount FROM payments WHERE payment_id = ?", (payment_id,))
            payment_info = c.fetchone()
            conn.close()
            
            if payment_info:
                user_id, plan_id, amount = payment_info
                create_subscription(user_id, plan_id, amount, payment_id)
                
                # Отправляем уведомление пользователю
                try:
                    plan = PLANS[plan_id]
                    await bot.send_message(
                        user_id,
                        f"✅ <b>Оплата прошла успешно!</b>\n\n"
                        f"🎉 Подписка на тариф «{plan['label']}» активирована!\n"
                        f"📅 Период: {plan['days']} дней\n"
                        f"📱 Устройств: {plan['devices']}\n\n"
                        f"Спасибо за покупку! 🔒",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление пользователю: {e}")
        
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Ошибка в вебхуке: {e}")
        return web.Response(status=500)

# ======================= ЗАПУСК БОТА =======================
async def main():
    # Настройка вебхука для ЮKassa (если нужно)
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Запускаем веб-сервер для вебхуков (опционально)
    # app = web.Application()
    # app.router.add_post('/webhook/yookassa', yookassa_webhook)
    # web.run_app(app, host='0.0.0.0', port=8080)
    
    # Или запускаем бота с поллингом
    asyncio.run(main())
