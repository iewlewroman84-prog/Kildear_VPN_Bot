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
import aiohttp
import base64
import hashlib
import json
import uuid
import requests
import random
from aiohttp import web

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8656434661:AAHv3yKPvStdiSDcSiBJPxKaYSgmJLtBlpo")

# Настройки 3x-ui панели (из ваших данных)
XRAY_PANEL_URL = os.environ.get("XRAY_PANEL_URL", "https://2.26.70.65:55347")
XRAY_PANEL_PATH = os.environ.get("XRAY_PANEL_PATH", "/mMH522DscvfBbpFwaA")
XRAY_USERNAME = os.environ.get("XRAY_USERNAME", "fHRTAk9lFz")
XRAY_PASSWORD = os.environ.get("XRAY_PASSWORD", "pM0xjYSy4N")
XRAY_INBOUND_ID = os.environ.get("XRAY_INBOUND_ID", "1")  # ID входящего подключения
XRAY_API_TOKEN = os.environ.get("XRAY_API_TOKEN", "tlZpRhpGQA54Uyta9p9chp42oymKG8NjoauzprvEqHSHqrye")

# Настройки подписки
SUBSCRIPTION_PATH = os.environ.get("SUBSCRIPTION_PATH", "/sub/mc3psfn9f3t39r2x")
SUBSCRIPTION_URL = f"https://2.26.70.65:2096{SUBSCRIPTION_PATH}"

# Настройки VPN сервера
VPN_SERVER_IP = os.environ.get("VPN_SERVER_IP", "2.26.70.65")
VPN_DOMAIN = os.environ.get("VPN_DOMAIN", "2.26.70.65")

# Настройки ЮKassa
YKASSA_SHOP_ID = os.environ.get("YKASSA_SHOP_ID", "1434221")
YKASSA_SECRET_KEY = os.environ.get("YKASSA_SECRET_KEY", "live_fH2K3m3SygBdP8P6bjaOwkRj4UKl5FwsatLZC-PJKt8")
YKASSA_API_URL = "https://api.yookassa.ru/v3/payments"
YKASSA_WEBHOOK_URL = os.environ.get("YKASSA_WEBHOOK_URL", "https://kildear-vpn-bot.onrender.com/webhook/yookassa")

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

# ======================= ИНТЕГРАЦИЯ С 3X-UI =======================
class XrayPanelAPI:
    def __init__(self, url: str, path: str, username: str, password: str, api_token: str = None):
        self.url = url.rstrip('/')
        self.path = path.rstrip('/')
        self.username = username
        self.password = password
        self.api_token = api_token
        self.session = requests.Session()
        self.session.verify = False
        self.cookie = None
        self.logged_in = False
        self.login()
    
    def login(self) -> bool:
        """Авторизация в панели 3x-ui"""
        try:
            # Пробуем логин через API
            if self.api_token:
                logger.info("Попытка входа через API токен")
                self.logged_in = True
                return True
            
            # Стандартный логин
            login_url = f"{self.url}{self.path}/login"
            login_data = {
                "username": self.username,
                "password": self.password
            }
            
            logger.info(f"Попытка входа в 3x-ui: {login_url}")
            
            response = self.session.post(
                login_url,
                json=login_data,
                timeout=30
            )
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    if result.get('success'):
                        self.cookie = response.cookies.get_dict()
                        self.logged_in = True
                        logger.info("✅ Успешная авторизация в 3x-ui")
                        return True
                except:
                    if response.cookies:
                        self.cookie = response.cookies.get_dict()
                        self.logged_in = True
                        logger.info("✅ Успешная авторизация в 3x-ui (по кукам)")
                        return True
            
            logger.error(f"❌ Ошибка авторизации: {response.status_code}")
            return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка при авторизации: {e}")
            return False
    
    def create_client(self, uuid: str, email: str, expiry_time: int, inbound_id: int = None) -> bool:
        """Создание клиента в 3x-ui"""
        if not inbound_id:
            inbound_id = XRAY_INBOUND_ID
        
        try:
            # Пробуем через API токен
            if self.api_token:
                add_client_url = f"{self.url}{self.path}/panel/api/inbounds/addClient"
                headers = {
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json"
                }
                
                client_data = {
                    "id": uuid,
                    "email": email,
                    "flow": "xtls-rprx-vision",
                    "limitIp": 1,
                    "totalGB": 0,
                    "expiryTime": expiry_time,
                    "enable": True,
                    "tgId": "",
                    "subId": ""
                }
                
                payload = {
                    "clients": [client_data],
                    "inboundId": int(inbound_id)
                }
                
                logger.info(f"📤 Создание клиента через API: {email}")
                
                response = self.session.post(
                    add_client_url,
                    json=payload,
                    headers=headers,
                    timeout=30
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get('success'):
                        logger.info(f"✅ Клиент {email} создан через API")
                        return True
                    else:
                        logger.error(f"❌ Ошибка API: {result}")
                
                logger.warning("⚠️ API метод не сработал, пробуем стандартный...")
            
            # Стандартный метод через куки
            if not self.logged_in:
                if not self.login():
                    return False
            
            add_client_url = f"{self.url}{self.path}/xray/inbound/addClient/{inbound_id}"
            
            client_data = {
                "id": uuid,
                "email": email,
                "flow": "xtls-rprx-vision",
                "limitIp": 1,
                "totalGB": 0,
                "expiryTime": expiry_time,
                "enable": True,
                "tgId": "",
                "subId": ""
            }
            
            payload = {
                "clients": [client_data]
            }
            
            logger.info(f"📤 Создание клиента: {email}")
            
            response = self.session.post(
                add_client_url,
                json=payload,
                cookies=self.cookie,
                timeout=30
            )
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    if result.get('success'):
                        logger.info(f"✅ Клиент {email} создан в 3x-ui")
                        return True
                    else:
                        logger.error(f"❌ Ошибка создания: {result}")
                        return False
                except:
                    logger.info(f"✅ Клиент {email} создан (статус 200)")
                    return True
            else:
                logger.error(f"❌ HTTP ошибка: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка при создании клиента: {e}")
            return False
    
    def get_inbounds(self) -> Optional[list]:
        """Получение списка входящих подключений"""
        try:
            if self.api_token:
                url = f"{self.url}{self.path}/panel/api/inbounds/list"
                headers = {"Authorization": f"Bearer {self.api_token}"}
                response = self.session.get(url, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get('success'):
                        return result.get('obj', [])
            
            if not self.logged_in:
                if not self.login():
                    return None
            
            url = f"{self.url}{self.path}/xray/inbound/list"
            response = self.session.get(url, cookies=self.cookie, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    return result.get('obj', [])
            
            return None
                
        except Exception as e:
            logger.error(f"Ошибка получения inbounds: {e}")
            return None

# Инициализируем API
try:
    xray_api = XrayPanelAPI(
        XRAY_PANEL_URL, 
        XRAY_PANEL_PATH, 
        XRAY_USERNAME, 
        XRAY_PASSWORD,
        XRAY_API_TOKEN
    )
    logger.info("API 3x-ui инициализирован")
    
    # Проверяем подключение
    inbounds = xray_api.get_inbounds()
    if inbounds:
        logger.info(f"✅ Найдено входящих подключений: {len(inbounds)}")
        for inbound in inbounds:
            logger.info(f"  - ID: {inbound.get('id')}, Порт: {inbound.get('port')}, Протокол: {inbound.get('protocol')}")
    else:
        logger.warning("⚠️ Не удалось получить список inbounds")
        
except Exception as e:
    logger.error(f"❌ Ошибка инициализации API: {e}")
    xray_api = None

# ======================= ГЕНЕРАЦИЯ КЛЮЧЕЙ =======================
def generate_random_port() -> int:
    return random.randint(10000, 65535)

def generate_vless_link(uuid: str, email: str, port: int) -> str:
    flow = "xtls-rprx-vision"
    encryption = "none"
    security = "none"
    
    vless_link = (
        f"vless://{uuid}@"
        f"{VPN_SERVER_IP}:{port}"
        f"?type=tcp&security={security}"
        f"&encryption={encryption}"
        f"&flow={flow}"
        f"&sni={VPN_DOMAIN}"
        f"&fp=chrome"
        f"#{email}"
    )
    
    return vless_link

def create_vpn_user_on_server(user_id: int, plan_id: str) -> tuple:
    user_uuid = str(uuid.uuid4())
    email = f"user_{user_id}_{int(datetime.now().timestamp())}"
    port = generate_random_port()
    plan = PLANS[plan_id]
    expiry_time = int((datetime.now() + timedelta(days=plan['days'])).timestamp() * 1000)
    
    client_created = False
    if xray_api:
        client_created = xray_api.create_client(user_uuid, email, expiry_time, int(XRAY_INBOUND_ID))
        if client_created:
            logger.info(f"✅ Клиент создан на панели: {email}, порт: {port}")
        else:
            logger.warning(f"⚠️ Не удалось создать клиента на панели")
    
    vless_link = generate_vless_link(user_uuid, email, port)
    return vless_link, user_uuid, email, port

# ======================= ФУНКЦИИ ДЛЯ ЮKASSA =======================
async def create_yookassa_payment(user_id: int, plan_id: str, amount: int) -> Dict:
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
                return 'error'

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
    
    vpn_key, user_uuid, client_id, port = create_vpn_user_on_server(user_id, plan_id)
    subscription_uuid = str(uuid.uuid4())
    
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    
    c.execute("UPDATE subscriptions SET status = 'inactive' WHERE user_id = ? AND status = 'active'", (user_id,))
    
    c.execute("""INSERT INTO subscriptions 
                 (user_id, plan_id, start_date, end_date, status, price, payment_id, vpn_key, uuid, client_id, port, subscription_uuid)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (user_id, plan_id, start_date, end_date, 'active', price, payment_id, vpn_key, user_uuid, client_id, port, subscription_uuid))
    
    subscription_id = c.lastrowid
    
    c.execute("""INSERT INTO vpn_keys 
                 (key, user_id, subscription_id, created_at, expires_at, status, devices, uuid, client_id, port, subscription_uuid)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (vpn_key, user_id, subscription_id, start_date, end_date, 'active', plan['devices'], user_uuid, client_id, port, subscription_uuid))
    
    c.execute("""UPDATE users 
                 SET current_subscription = ?, subscription_end = ?, devices = ?, subscription_uuid = ?
                 WHERE user_id = ?""",
              (plan_id, end_date, plan['devices'], subscription_uuid, user_id))
    
    conn.commit()
    conn.close()
    
    logger.info(f"✅ Подписка создана: user={user_id}, plan={plan_id}, port={port}")
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

def get_payment_keyboard(payment_id: str, confirmation_url: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=confirmation_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_payment_{payment_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    return keyboard

def get_keys_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить ключи", callback_data="refresh_keys")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    return keyboard

# ======================= ОБРАБОТЧИКИ КОМАНД =======================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    create_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    
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
    active_sub = get_active_subscription(callback.from_user.id)
    
    plans_text = "📱 <b>Выберите тарифный план:</b>\n\n"
    
    if active_sub:
        try:
            end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
            days_left = (end_date - datetime.now()).days
            if days_left > 0:
                plans_text += f"✅ <i>У вас активна подписка до {end_date.strftime('%d.%m.%Y')} (осталось {days_left} дней)</i>\n\n"
            else:
                plans_text += f"⏰ <i>Подписка истекла {end_date.strftime('%d.%m.%Y')}</i>\n\n"
        except:
            pass
    
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

@dp.callback_query(F.data == "my_keys")
async def show_keys(callback: CallbackQuery):
    user_id = callback.from_user.id
    keys = get_user_vpn_keys(user_id)
    
    if not keys:
        keys_text = """
🔑 <b>У вас нет активных VPN ключей</b>

Для получения ключа необходимо приобрести подписку.
Нажмите "📱 Купить подписку" чтобы выбрать тариф.
"""
        await callback.message.edit_text(
            keys_text,
            reply_markup=get_keys_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    keys_text = "🔑 <b>Ваши активные VPN ключи:</b>\n\n"
    
    for idx, key_data in enumerate(keys, 1):
        try:
            key, expires_at, status, devices, port = key_data[0], key_data[1], key_data[2], key_data[3], key_data[4] if len(key_data) > 4 else "?"
            
            expires = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S.%f')
            days_left = (expires - datetime.now()).days
            
            keys_text += f"<b>Ключ #{idx}</b>\n"
            keys_text += f"<code>{key}</code>\n"
            keys_text += f"📱 Устройств: {devices}\n"
            keys_text += f"🔌 Порт: {port}\n"
            keys_text += f"⏳ Действует до: {expires.strftime('%d.%m.%Y')}\n"
            keys_text += f"📊 Осталось: {days_left} дней\n"
            keys_text += "\n📌 <b>Инструкция:</b>\n"
            keys_text += "1. Скопируйте ключ полностью\n"
            keys_text += "2. Вставьте в приложение (V2RayNG, Nekoray, Shadowrocket)\n"
            keys_text += "3. Подключитесь к серверу\n\n"
            keys_text += "➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        except Exception as e:
            logger.error(f"Ошибка форматирования ключа: {e}")
    
    await callback.message.edit_text(
        keys_text,
        reply_markup=get_keys_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "my_subscription")
async def show_subscription(callback: CallbackQuery):
    user_id = callback.from_user.id
    active_sub = get_active_subscription(user_id)
    
    if not active_sub:
        await callback.message.edit_text(
            "❌ <b>У вас нет активной подписки</b>\n\n"
            "Купите подписку, чтобы получить ссылку для импорта в приложение.",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    subscription_url = f"{SUBSCRIPTION_URL}/{active_sub.get('subscription_uuid', '')}"
    
    subscription_text = f"""
📥 <b>Ваша ссылка для подписки</b>

🔗 <code>{subscription_url}</code>

📌 <b>Как использовать:</b>
1. Скопируйте ссылку
2. Вставьте в приложение:
   • V2RayNG: Нажмите "+" → "Import from URL"
   • Nekoray: Нажмите "Add" → "Subscription"
   • Shadowrocket: Нажмите "+" → "Subscribe"
   • Hiddify: Нажмите "Add" → "Subscription URL"
3. Приложение автоматически загрузит все ключи

📅 Действует до: {datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f').strftime('%d.%m.%Y')}
🔌 Порт: {active_sub.get('port', '?')}

⚠️ <b>Важно:</b> Ссылка персональная, не передавайте её другим.
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Копировать ссылку", callback_data=f"copy_subscription_{active_sub.get('subscription_uuid', '')}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(
        subscription_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("copy_subscription_"))
async def copy_subscription(callback: CallbackQuery):
    subscription_uuid = callback.data.replace("copy_subscription_", "")
    subscription_url = f"{SUBSCRIPTION_URL}/{subscription_uuid}"
    
    await callback.message.answer(
        f"🔗 <b>Ссылка для подписки:</b>\n\n"
        f"<code>{subscription_url}</code>\n\n"
        f"Просто скопируйте эту ссылку и вставьте в ваше приложение.",
        parse_mode="HTML"
    )
    await callback.answer("✅ Ссылка отправлена!")

@dp.callback_query(F.data == "refresh_keys")
async def refresh_keys(callback: CallbackQuery):
    await show_keys(callback)

@dp.callback_query(F.data.startswith("plan_"))
async def process_plan_selection(callback: CallbackQuery):
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
        port = active_sub.get('port') if active_sub else "?"
        
        success_text = f"""
🎉 <b>Поздравляем! Бесплатная подписка активирована!</b>

📅 Период: {plan['days']} дней
📱 Устройств: {plan['devices']}
🔌 Порт: {port}

🔑 <b>Ваш VPN ключ:</b>
<code>{vpn_key}</code>

📌 <b>Инструкция:</b>
1. Скопируйте ключ
2. Вставьте в приложение
3. Подключитесь

Ключ также доступен в разделе "Мои ключи".
"""
        
        await callback.message.edit_text(
            success_text,
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    try:
        payment_data = await create_yookassa_payment(
            user_id=user_id,
            plan_id=plan_id,
            amount=plan['price']
        )
        
        create_payment(user_id, plan_id, plan['price'], payment_data['id'])
        
        payment_text = f"""
💳 <b>Оплата подписки через ЮKassa</b>

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
            f"❌ <b>Ошибка при создании платежа</b>\n\n{str(e)}",
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
            conn = sqlite3.connect('subscriptions.db')
            c = conn.cursor()
            c.execute("SELECT plan_id, amount FROM payments WHERE payment_id = ?", (payment_id,))
            payment_info = c.fetchone()
            conn.close()
            
            if payment_info:
                plan_id = payment_info[0]
                update_payment_status(payment_id, 'succeeded')
                create_subscription(user_id, plan_id, payment_info[1], payment_id)
                
                active_sub = get_active_subscription(user_id)
                vpn_key = active_sub.get('vpn_key') if active_sub else None
                port = active_sub.get('port') if active_sub else "?"
                plan = PLANS[plan_id]
                subscription_uuid = active_sub.get('subscription_uuid') if active_sub else ""
                
                success_text = f"""
✅ <b>Оплата прошла успешно!</b>

🎉 Подписка на тариф «{plan['label']}» активирована!
📅 Период: {plan['days']} дней
📱 Устройств: {plan['devices']}
🔌 Порт: {port}

🔑 <b>Ваш VPN ключ:</b>
<code>{vpn_key}</code>

📥 <b>Ссылка для подписки:</b>
<code>{SUBSCRIPTION_URL}/{subscription_uuid}</code>

📌 <b>Инструкция:</b>
1. Скопируйте ключ или ссылку
2. Вставьте в приложение
3. Подключитесь

Спасибо за покупку! 🔒
"""
                await callback.message.edit_text(
                    success_text,
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
    keys = get_user_vpn_keys(user_id)
    
    if not user:
        await callback.answer("❌ Пользователь не найден")
        return
    
    profile_text = f"""
👤 <b>Ваш профиль</b>

🆔 ID: {user['user_id']}
📝 Имя: {user['first_name']}
"""
    
    if user['username']:
        profile_text += f"📌 @{user['username']}\n"
    
    if active_sub:
        try:
            end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
            days_left = (end_date - datetime.now()).days
            plan = PLANS.get(active_sub['plan_id'], {})
            
            profile_text += f"""
📱 <b>Текущая подписка</b>
• Тариф: {plan.get('label', active_sub['plan_id'])}
• Действует до: {end_date.strftime('%d.%m.%Y')}
• Осталось дней: {days_left if days_left > 0 else 0}
• Устройств: {plan.get('devices', 1)}
• Порт: {active_sub.get('port', '?')}
"""
        except:
            profile_text += "\n❌ <b>Ошибка чтения данных подписки</b>"
    else:
        profile_text += "\n❌ <b>Нет активной подписки</b>"
    
    profile_text += f"\n🔑 <b>Всего активных ключей:</b> {len(keys)}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys")],
        [InlineKeyboardButton(text="📥 Ссылка для подписки", callback_data="my_subscription")],
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
4. После оплаты нажмите "Проверить оплату"

<b>Как получить VPN ключ?</b>
После оплаты подписки ключ придет в сообщении.
Вы также можете посмотреть его в разделе "Мои ключи".

<b>Как использовать ссылку для подписки?</b>
1. Перейдите в раздел "📥 Ссылка для подписки"
2. Скопируйте ссылку
3. Вставьте в приложение:
   • V2RayNG: "+" → "Import from URL"
   • Nekoray: "Add" → "Subscription"
   • Shadowrocket: "+" → "Subscribe"
   • Hiddify: "Add" → "Subscription URL"
4. Приложение загрузит все ключи автоматически

<b>Бесплатный период</b>
Вы можете получить 2 дня бесплатно.

<b>Вопросы и поддержка</b>
📧 support@kildear.com
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

# ======================= WEBHOOK ДЛЯ ЮKASSA =======================
async def yookassa_webhook(request):
    try:
        data = await request.json()
        logger.info(f"Получен вебхук от ЮKassa: {data}")
        
        event = data.get('event')
        payment_data = data.get('object', {})
        payment_id = payment_data.get('id')
        status = payment_data.get('status')
        
        if event == 'payment.succeeded':
            update_payment_status(payment_id, 'succeeded')
            
            conn = sqlite3.connect('subscriptions.db')
            c = conn.cursor()
            c.execute("SELECT user_id, plan_id, amount FROM payments WHERE payment_id = ?", (payment_id,))
            payment_info = c.fetchone()
            conn.close()
            
            if payment_info:
                user_id, plan_id, amount = payment_info
                create_subscription(user_id, plan_id, amount, payment_id)
                
                active_sub = get_active_subscription(user_id)
                vpn_key = active_sub.get('vpn_key') if active_sub else None
                port = active_sub.get('port') if active_sub else "?"
                subscription_uuid = active_sub.get('subscription_uuid') if active_sub else ""
                plan = PLANS[plan_id]
                
                try:
                    await bot.send_message(
                        user_id,
                        f"✅ <b>Оплата прошла успешно!</b>\n\n"
                        f"🎉 Подписка на тариф «{plan['label']}» активирована!\n"
                        f"📅 Период: {plan['days']} дней\n"
                        f"🔌 Порт: {port}\n"
                        f"🔑 <b>Ваш VPN ключ:</b>\n<code>{vpn_key}</code>\n\n"
                        f"📥 <b>Ссылка для подписки:</b>\n<code>{SUBSCRIPTION_URL}/{subscription_uuid}</code>\n\n"
                        f"Ключ и ссылка также доступны в меню бота.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление: {e}")
        
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Ошибка в вебхуке ЮKassa: {e}")
        return web.Response(status=500)

# ======================= ОБРАБОТЧИК ВЕБХУКА ТЕЛЕГРАМ =======================
async def webhook_handler(request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Ошибка в вебхуке: {e}")
        return web.Response(status=500)

# ======================= ЗАПУСК БОТА =======================
async def on_startup():
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webhook"
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_webhook(webhook_url)
        logger.info(f"✅ Вебхук установлен: {webhook_url}")
    except Exception as e:
        logger.error(f"Ошибка установки вебхука: {e}")

async def main():
    port = int(os.environ.get('PORT', 8080))
    
    app = web.Application()
    app.router.add_post('/webhook', webhook_handler)
    app.router.add_post('/webhook/yookassa', yookassa_webhook)
    app.router.add_get('/', lambda request: web.Response(text='Bot is running!'))
    
    await on_startup()
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"✅ Бот запущен на порту {port}")
    logger.info(f"✅ 3x-ui панель: {XRAY_PANEL_URL}{XRAY_PANEL_PATH}")
    logger.info(f"✅ Подписка: {SUBSCRIPTION_URL}")
    logger.info(f"✅ ЮKassa подключена")
    
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())
