import os
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram import F
import json
import uuid
import requests
from aiohttp import web

# ======================= ПРЯМЫЕ НАСТРОЙКИ =======================
BOT_TOKEN = "8656434661:AAHv3yKPvStdiSDcSiBJPxKaYSgmJLtBlpo"

# Данные 3x-ui панели
XRAY_PANEL_URL = "https://2.26.70.65:55347"
XRAY_PANEL_PATH = "/mMH522DscvfBbpFwaA"
XRAY_USERNAME = "fHRTAk9lFz"
XRAY_PASSWORD = "pM0xjYSy4N"
XRAY_INBOUND_ID = 2
XRAY_API_TOKEN = "tlZpRhpGQA54Uyta9p9chp42oymKG8NjoauzprvEqHSHqrye"

# Настройки VPN
VPN_SERVER_IP = "2.26.70.65"
VPN_SERVER_PORT = "40224"
VPN_DOMAIN = "2.26.70.65"
VPN_PATH = "/mMH522DscvfBbpFwaA"

# Подписка
SUBSCRIPTION_URL = "https://2.26.70.65:2096/sub/mc3psfn9f3t39r2x"

# ======================= ТАРИФЫ =======================
PLANS = {
    "2d": {"days": 2, "price": 0, "devices": 1, "label": "🎁 2 дня бесплатно", "emoji": "🎁"},
    "1m": {"days": 30, "price": 139, "devices": 2, "label": "🔥 1 месяц", "emoji": "🔥"},
    "3m": {"days": 90, "price": 469, "devices": 3, "label": "⭐ 3 месяца", "emoji": "⭐"},
    "1y": {"days": 365, "price": 899, "devices": 5, "label": "💎 1 год", "emoji": "💎"},
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
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, 
                  username TEXT,
                  first_name TEXT,
                  last_name TEXT,
                  registration_date TIMESTAMP,
                  current_subscription TEXT,
                  subscription_end TIMESTAMP,
                  devices INTEGER DEFAULT 1,
                  subscription_uuid TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  plan_id TEXT,
                  start_date TIMESTAMP,
                  end_date TIMESTAMP,
                  status TEXT,
                  price INTEGER,
                  payment_id TEXT,
                  vpn_key TEXT,
                  uuid TEXT,
                  client_id TEXT,
                  port INTEGER,
                  subscription_uuid TEXT,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS vpn_keys
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  key TEXT UNIQUE,
                  user_id INTEGER,
                  subscription_id INTEGER,
                  created_at TIMESTAMP,
                  expires_at TIMESTAMP,
                  status TEXT DEFAULT 'active',
                  devices INTEGER DEFAULT 1,
                  uuid TEXT,
                  client_id TEXT,
                  port INTEGER,
                  subscription_uuid TEXT,
                  FOREIGN KEY (user_id) REFERENCES users (user_id),
                  FOREIGN KEY (subscription_id) REFERENCES subscriptions (id))''')
    
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
    logger.info("База данных инициализирована")

init_db()

# ======================= РАБОТА С 3X-UI =======================
class XrayAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False
        self.token = XRAY_API_TOKEN
        self.base_url = f"{XRAY_PANEL_URL}{XRAY_PANEL_PATH}"
        self.logged_in = False
        self.login()
    
    def login(self) -> bool:
        """Авторизация для получения кук"""
        try:
            url = f"{self.base_url}/login"
            data = {
                "username": XRAY_USERNAME,
                "password": XRAY_PASSWORD
            }
            
            response = self.session.post(url, json=data, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    self.logged_in = True
                    logger.info("✅ Успешная авторизация в панели")
                    return True
                else:
                    logger.error(f"❌ Ошибка авторизации: {result}")
                    return False
            else:
                logger.error(f"❌ HTTP {response.status_code}: {response.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return False
    
    def add_client(self, uuid: str, email: str, expiry_time: int) -> bool:
        """Добавление клиента через API"""
        try:
            # ПРАВИЛЬНЫЙ API ПУТЬ из вашего URL
            url = f"{self.base_url}/panel/api/inbounds/addClient"
            
            payload = {
                "clients": [{
                    "id": uuid,
                    "email": email,
                    "flow": "xtls-rprx-vision",
                    "limitIp": 1,
                    "totalGB": 0,
                    "expiryTime": expiry_time,
                    "enable": True,
                    "tgId": "",
                    "subId": ""
                }],
                "inboundId": XRAY_INBOUND_ID
            }
            
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            logger.info(f"📤 URL: {url}")
            logger.info(f"📤 Клиент: {email}")
            logger.info(f"📤 Inbound: {XRAY_INBOUND_ID}")
            
            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            
            logger.info(f"📥 Статус: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    logger.info(f"📥 Ответ: {json.dumps(result, indent=2)}")
                    if result.get('success'):
                        logger.info(f"✅ Клиент {email} создан!")
                        return True
                    else:
                        logger.error(f"❌ Ошибка: {result}")
                        return False
                except:
                    logger.info(f"✅ Клиент создан (статус 200)")
                    return True
            else:
                logger.error(f"❌ HTTP {response.status_code}: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return False
    
    def get_clients(self) -> Optional[list]:
        """Получение списка клиентов"""
        try:
            url = f"{self.base_url}/panel/api/inbounds/get/{XRAY_INBOUND_ID}"
            headers = {"Authorization": f"Bearer {self.token}"}
            
            response = self.session.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    return result.get('obj', {}).get('clients', [])
            return None
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            return None

# Создаем экземпляр API
xray_api = XrayAPI()

# Проверяем подключение
logger.info("🔍 Проверка подключения...")
clients = xray_api.get_clients()
if clients is not None:
    logger.info(f"✅ Найдено клиентов: {len(clients)}")
else:
    logger.warning("⚠️ Не удалось получить список клиентов")

# ======================= ГЕНЕРАЦИЯ КЛЮЧА =======================
def generate_vless_link(uuid: str, email: str) -> str:
    vless_link = (
        f"vless://{uuid}@"
        f"{VPN_SERVER_IP}:{VPN_SERVER_PORT}"
        f"?type=ws"
        f"&security=none"
        f"&encryption=none"
        f"&host={VPN_DOMAIN}"
        f"&path={VPN_PATH}"
        f"#{email}"
    )
    return vless_link

def create_vpn_user(user_id: int, plan_id: str) -> tuple:
    user_uuid = str(uuid.uuid4())
    email = f"user_{user_id}_{int(datetime.now().timestamp())}"
    plan = PLANS[plan_id]
    expiry_time = int((datetime.now() + timedelta(days=plan['days'])).timestamp() * 1000)
    
    success = xray_api.add_client(user_uuid, email, expiry_time)
    
    if success:
        logger.info(f"✅ Клиент создан: {email}")
    else:
        logger.warning(f"⚠️ Клиент НЕ создан! Ключ будет для теста")
    
    vless_link = generate_vless_link(user_uuid, email)
    return vless_link, user_uuid, email

# ======================= ФУНКЦИИ БД =======================
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
            'devices': user[7],
            'subscription_uuid': user[8] if len(user) > 8 else None
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
            'payment_id': sub[7],
            'vpn_key': sub[8] if len(sub) > 8 else None,
            'uuid': sub[9] if len(sub) > 9 else None,
            'client_id': sub[10] if len(sub) > 10 else None,
            'port': sub[11] if len(sub) > 11 else None,
            'subscription_uuid': sub[12] if len(sub) > 12 else None
        }
    return None

def create_subscription(user_id: int, plan_id: str, price: int, payment_id: str = None) -> int:
    plan = PLANS[plan_id]
    start_date = datetime.now()
    end_date = start_date + timedelta(days=plan['days'])
    
    vpn_key, user_uuid, client_id = create_vpn_user(user_id, plan_id)
    
    subscription_uuid = str(uuid.uuid4())
    
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    
    c.execute("UPDATE subscriptions SET status = 'inactive' WHERE user_id = ? AND status = 'active'", (user_id,))
    
    c.execute("""INSERT INTO subscriptions 
                 (user_id, plan_id, start_date, end_date, status, price, payment_id, vpn_key, uuid, client_id, port, subscription_uuid)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (user_id, plan_id, start_date, end_date, 'active', price, payment_id, vpn_key, user_uuid, client_id, VPN_SERVER_PORT, subscription_uuid))
    
    subscription_id = c.lastrowid
    
    c.execute("""INSERT INTO vpn_keys 
                 (key, user_id, subscription_id, created_at, expires_at, status, devices, uuid, client_id, port, subscription_uuid)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (vpn_key, user_id, subscription_id, start_date, end_date, 'active', plan['devices'], user_uuid, client_id, VPN_SERVER_PORT, subscription_uuid))
    
    c.execute("""UPDATE users 
                 SET current_subscription = ?, subscription_end = ?, devices = ?, subscription_uuid = ?
                 WHERE user_id = ?""",
              (plan_id, end_date, plan['devices'], subscription_uuid, user_id))
    
    conn.commit()
    conn.close()
    
    logger.info(f"✅ Подписка создана: user={user_id}, plan={plan_id}")
    return subscription_id

def get_user_vpn_keys(user_id: int) -> list:
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("""SELECT key, expires_at, status, devices, port FROM vpn_keys 
                 WHERE user_id = ? AND status = 'active' AND expires_at > datetime('now')
                 ORDER BY created_at DESC""", (user_id,))
    keys = c.fetchall()
    conn.close()
    return keys

# ======================= КЛАВИАТУРЫ =======================
def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Купить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys")],
        [InlineKeyboardButton(text="📥 Ссылка для подписки", callback_data="my_subscription")],
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

def get_keys_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить ключи", callback_data="refresh_keys")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    return keyboard

# ======================= ОБРАБОТЧИКИ =======================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    create_user(user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    
    text = f"""
🌟 <b>Добро пожаловать в VPN сервис Kildear!</b>

📋 Тарифы:
• 🎁 2 дня бесплатно
• 🔥 1 месяц — 139 ₽
• ⭐ 3 месяца — 469 ₽
• 💎 1 год — 899 ₽
"""
    await message.answer(text, reply_markup=get_main_keyboard(), parse_mode="HTML")

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("🏠 Главное меню", reply_markup=get_main_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "buy_subscription")
async def show_plans(callback: CallbackQuery):
    active_sub = get_active_subscription(callback.from_user.id)
    plans_text = "📱 Выберите тариф:\n\n"
    
    if active_sub:
        try:
            end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
            days_left = (end_date - datetime.now()).days
            if days_left > 0:
                plans_text += f"✅ Активна до {end_date.strftime('%d.%m.%Y')} (осталось {days_left} дн.)\n\n"
        except:
            pass
    
    for plan_id, plan in PLANS.items():
        price_text = f"{plan['price']} ₽" if plan['price'] > 0 else "Бесплатно"
        plans_text += f"{plan['emoji']} {plan['label']} — {price_text}\n"
    
    await callback.message.edit_text(plans_text, reply_markup=get_plans_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "my_keys")
async def show_keys(callback: CallbackQuery):
    user_id = callback.from_user.id
    keys = get_user_vpn_keys(user_id)
    
    if not keys:
        await callback.message.edit_text("🔑 Нет активных ключей", reply_markup=get_keys_keyboard(), parse_mode="HTML")
        await callback.answer()
        return
    
    text = "🔑 Ваши ключи:\n\n"
    for idx, key_data in enumerate(keys, 1):
        key, expires_at, status, devices, port = key_data
        expires = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S.%f')
        days_left = (expires - datetime.now()).days
        text += f"<b>#{idx}</b>\n<code>{key}</code>\n📱 {devices} уст. ⏳ {days_left} дн.\n\n"
    
    await callback.message.edit_text(text, reply_markup=get_keys_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "my_subscription")
async def show_subscription(callback: CallbackQuery):
    user_id = callback.from_user.id
    active_sub = get_active_subscription(user_id)
    
    if not active_sub:
        await callback.message.edit_text("❌ Нет активной подписки", reply_markup=get_main_keyboard(), parse_mode="HTML")
        await callback.answer()
        return
    
    subscription_url = f"{SUBSCRIPTION_URL}/{active_sub.get('subscription_uuid', '')}"
    end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
    
    text = f"""
📥 Ссылка для подписки:
<code>{subscription_url}</code>

📅 До: {end_date.strftime('%d.%m.%Y')}
"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Копировать", callback_data=f"copy_{active_sub.get('subscription_uuid', '')}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("copy_"))
async def copy_sub(callback: CallbackQuery):
    uuid_val = callback.data.replace("copy_", "")
    await callback.message.answer(f"🔗 <code>{SUBSCRIPTION_URL}/{uuid_val}</code>", parse_mode="HTML")
    await callback.answer("✅ Скопируйте!")

@dp.callback_query(F.data == "refresh_keys")
async def refresh_keys(callback: CallbackQuery):
    await show_keys(callback)

@dp.callback_query(F.data.startswith("plan_"))
async def process_plan(callback: CallbackQuery):
    plan_id = callback.data.replace("plan_", "")
    plan = PLANS.get(plan_id)
    if not plan:
        await callback.answer("❌ Тариф не найден")
        return
    
    user_id = callback.from_user.id
    
    if plan['price'] == 0:
        conn = sqlite3.connect('subscriptions.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id = ? AND plan_id = '2d'", (user_id,))
        count = c.fetchone()[0]
        conn.close()
        
        if count > 0:
            await callback.answer("❌ Вы уже использовали бесплатный период!", show_alert=True)
            return
        
        create_subscription(user_id, plan_id, 0)
        active_sub = get_active_subscription(user_id)
        vpn_key = active_sub.get('vpn_key') if active_sub else None
        
        text = f"""
🎉 Бесплатная подписка активирована!

📅 {plan['days']} дней
🔌 Порт: {VPN_SERVER_PORT}

🔑 Ключ:
<code>{vpn_key}</code>
"""
        await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="HTML")
        await callback.answer()
        return
    
    text = f"""
💳 Оплата подписки

Тариф: {plan['label']}
Сумма: {plan['price']} ₽

⚠️ Тестовый режим
"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Тестовая активация", callback_data=f"test_{plan_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("test_"))
async def test_activate(callback: CallbackQuery):
    plan_id = callback.data.replace("test_", "")
    plan = PLANS.get(plan_id)
    user_id = callback.from_user.id
    
    if not plan:
        await callback.answer("❌ Ошибка")
        return
    
    create_subscription(user_id, plan_id, plan['price'], "test")
    active_sub = get_active_subscription(user_id)
    vpn_key = active_sub.get('vpn_key') if active_sub else None
    
    text = f"""
✅ Подписка активирована!

🎉 {plan['label']}
📅 {plan['days']} дней
🔌 Порт: {VPN_SERVER_PORT}

🔑 Ключ:
<code>{vpn_key}</code>
"""
    await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "my_profile")
async def show_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    active_sub = get_active_subscription(user_id)
    keys = get_user_vpn_keys(user_id)
    
    text = f"👤 Профиль\n\n🆔 ID: {user_id}"
    if user:
        text += f"\n📝 Имя: {user.get('first_name', '?')}"
    
    if active_sub:
        try:
            end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
            days_left = (end_date - datetime.now()).days
            plan = PLANS.get(active_sub['plan_id'], {})
            text += f"\n\n📱 Подписка: {plan.get('label', '?')}"
            text += f"\n📅 До: {end_date.strftime('%d.%m.%Y')} ({days_left} дн.)"
            text += f"\n🔌 Порт: {active_sub.get('port', '?')}"
        except:
            pass
    else:
        text += "\n\n❌ Нет подписки"
    
    text += f"\n🔑 Ключей: {len(keys)}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Купить", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="🔑 Ключи", callback_data="my_keys")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "help")
async def show_help(callback: CallbackQuery):
    text = """
🆘 Помощь

1. Нажмите "Купить подписку"
2. Выберите тариф
3. Для теста нажмите "Тестовая активация"

Ключ появится в "Мои ключи".
"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

# ======================= WEBHOOK =======================
async def webhook_handler(request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return web.Response(status=500)

# ======================= ЗАПУСК =======================
async def on_startup():
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webhook"
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_webhook(webhook_url)
        logger.info(f"✅ Вебхук: {webhook_url}")
    except Exception as e:
        logger.error(f"Ошибка: {e}")

async def main():
    port = int(os.environ.get('PORT', 8080))
    
    app = web.Application()
    app.router.add_post('/webhook', webhook_handler)
    app.router.add_get('/', lambda request: web.Response(text='Bot is running!'))
    
    await on_startup()
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"🚀 Бот запущен на порту {port}")
    logger.info(f"📡 Панель: {XRAY_PANEL_URL}{XRAY_PANEL_PATH}/panel/clients")
    logger.info(f"📡 Inbound ID: {XRAY_INBOUND_ID}")
    logger.info(f"📡 API: {XRAY_PANEL_URL}{XRAY_PANEL_PATH}/panel/api/")
    
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())
