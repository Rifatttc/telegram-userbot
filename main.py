# main.py – Telegram Userbot with Phone Lookup, Bot Info, and Anonymous ID Resolver
# Uses Telethon (user account) + Aiogram (bot interface) + SQLite (history/stats)

import re
import logging
import asyncio
from typing import Optional

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor

from telethon import TelegramClient, functions, types as ttypes
from telethon.errors import FloodWaitError, PhoneNumberInvalidError
from telethon.tl.types import InputPhoneContact
from telethon.sessions import StringSession

import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

# ---------- Environment Variables ----------
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
STRING_SESSION = os.getenv("STRING_SESSION", None)
DATABASE_PATH = os.getenv("DATABASE_PATH", "userbot.db")

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- SQLite Setup ----------
conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    type TEXT NOT NULL,
    input TEXT NOT NULL,
    result TEXT,
    success BOOLEAN NOT NULL DEFAULT 1
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")
conn.commit()

def add_history(htype: str, inp: str, result: str, success: bool):
    cursor.execute(
        "INSERT INTO history (type, input, result, success) VALUES (?, ?, ?, ?)",
        (htype, inp, result, success)
    )
    conn.commit()

def get_stats():
    total = cursor.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    success = cursor.execute("SELECT COUNT(*) FROM history WHERE success = 1").fetchone()[0]
    failures = total - success
    success_rate = round((success / total * 100), 2) if total else 0
    return total, success, failures, success_rate

# ---------- Telethon Client ----------
telethon_client: Optional[TelegramClient] = None

async def get_telethon_client() -> TelegramClient:
    """Return an already authenticated Telethon client, or create one from saved session."""
    global telethon_client
    if telethon_client and telethon_client.is_connected():
        return telethon_client

    # Try to load session string from database
    row = cursor.execute("SELECT value FROM settings WHERE key='string_session'").fetchone()
    session_str = row[0] if row else STRING_SESSION

    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        # If no valid session, we need to login via bot (see /login command)
        raise Exception("Not authorized. Please use /login to authenticate first.")

    telethon_client = client
    return client

def save_session_to_db(session_str: str):
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('string_session', ?)",
        (session_str,)
    )
    conn.commit()

# ---------- Aiogram Bot & Dispatcher ----------
bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ---------- FSM States ----------
class PhoneSearch(StatesGroup):
    waiting_for_phone = State()

class BotUsers(StatesGroup):
    waiting_for_bot_username = State()

class ResolveAnon(StatesGroup):
    waiting_for_message = State()

class LoginStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()

# ---------- Helper Functions ----------
def build_phone_result_markup(user_id: int, username: Optional[str], phone: str) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=2)
    if username:
        profile_btn = InlineKeyboardButton("👤 Open Profile", url=f"https://t.me/{username}")
    else:
        profile_btn = InlineKeyboardButton("👤 Open Profile", url=f"tg://user?id={user_id}")
    copy_btn = InlineKeyboardButton("📋 Copy ID", callback_data=f"copy_{user_id}")
    save_btn = InlineKeyboardButton("💾 Save to History", callback_data=f"save_{phone}_{user_id}")
    new_search_btn = InlineKeyboardButton("🔍 New Search", callback_data="new_phone")
    markup.add(profile_btn, copy_btn)
    markup.add(save_btn, new_search_btn)
    return markup

def build_bot_result_markup(bot_username: str, bot_id: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🤖 Open Bot", url=f"https://t.me/{bot_username}"))
    markup.add(InlineKeyboardButton("🔍 Search Again", callback_data="new_bot_users"))
    return markup

def build_anon_result_markup(sender_id: int, username: Optional[str]) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=2)
    if username:
        profile_btn = InlineKeyboardButton("👤 Open Profile", url=f"https://t.me/{username}")
    else:
        profile_btn = InlineKeyboardButton("👤 Open Profile", url=f"tg://user?id={sender_id}")
    copy_btn = InlineKeyboardButton("📋 Copy ID", callback_data=f"copy_{sender_id}")
    save_btn = InlineKeyboardButton("💾 Save to History", callback_data=f"save_anon_{sender_id}")
    new_btn = InlineKeyboardButton("🔍 Resolve Another", callback_data="new_anon")
    markup.add(profile_btn, copy_btn)
    markup.add(save_btn, new_btn)
    return markup

def main_menu_keyboard():
    return InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("🔍 Search by Number", callback_data="phone_search"),
        InlineKeyboardButton("🤖 Get Bot Users", callback_data="bot_users"),
        InlineKeyboardButton("👤 Resolve Anonymous ID", callback_data="resolve_anon"),
        InlineKeyboardButton("📜 History", callback_data="history"),
        InlineKeyboardButton("📊 Stats", callback_data="stats")
    )

# ---------- Handlers ----------
@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    await message.answer(
        "👋 <b>Welcome to Telegram Userbot!</b>\n"
        "I can look up phone numbers, fetch bot info, and resolve anonymous IDs using your Telethon account.\n\n"
        "Select an option below:",
        reply_markup=main_menu_keyboard()
    )

@dp.message_handler(commands=["login"])
async def login_start(message: types.Message):
    await message.answer("📞 Please send your phone number (with country code, e.g., +88017...) to start login.")
    await LoginStates.waiting_for_phone.set()

@dp.message_handler(state=LoginStates.waiting_for_phone)
async def login_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    # Validate phone format
    if not re.match(r'^\+?\d{7,15}$', phone):
        await message.answer("❌ Invalid phone number. Try again.")
        return
    await state.update_data(phone=phone)
    await message.answer("⏳ Sending code...")
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        if await client.is_user_authorized():
            await message.answer("⚠️ You are already logged in.")
            await state.finish()
            return
        result = await client.send_code_request(phone)
        # Store client in state for later use
        await state.update_data(telethon_client=client, phone_code_hash=result.phone_code_hash)
        await message.answer("✅ Code sent. Please send the code you received (without spaces, e.g., 12345).")
        await LoginStates.waiting_for_code.set()
    except Exception as e:
        await message.answer(f"⚠️ Error sending code: {str(e)}")
        await state.finish()

@dp.message_handler(state=LoginStates.waiting_for_code)
async def login_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()
    client = data.get('telethon_client')
    phone = data.get('phone')
    phone_code_hash = data.get('phone_code_hash')
    if not client:
        await message.answer("❌ Session expired. Use /login again.")
        await state.finish()
        return
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        session_str = client.session.save()
        save_session_to_db(session_str)
        await message.answer("✅ Login successful! You can now use all features.", reply_markup=main_menu_keyboard())
        await state.finish()
    except Exception as e:
        await message.answer(f"⚠️ Login failed: {str(e)}")
        await state.finish()

# Phone Search
@dp.callback_query_handler(lambda c: c.data == "phone_search")
async def start_phone_search(call: types.CallbackQuery):
    await call.message.edit_text("📞 Please send the phone number (with or without '+', any country code).")
    await PhoneSearch.waiting_for_phone.set()
    await call.answer()

@dp.message_handler(state=PhoneSearch.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone_raw = message.text.strip()
    phone_clean = re.sub(r'[^\d+]', '', phone_raw)
    if not phone_clean.startswith('+'):
        phone_clean = '+' + phone_clean
    if len(phone_clean) < 8 or len(phone_clean) > 15:
        await message.answer("❌ Invalid phone number length. Try again.", reply_markup=main_menu_keyboard())
        await state.finish()
        return

    await message.answer("⏳ Checking...")
    try:
        client = await get_telethon_client()
        contact = InputPhoneContact(client_id=0, phone=phone_clean, first_name="", last_name="")
        result = await client(ImportContactsRequest([contact]))
        if result.users:
            user = result.users[0]
            user_id = user.id
            username = user.username
            first_name = getattr(user, 'first_name', '') or ''
            last_name = getattr(user, 'last_name', '') or ''
            full_name = f"{first_name} {last_name}".strip()
            # Build display message
            display = f"✅ <b>Number Registered!</b>\n📞 {phone_clean}\n👤 {full_name}\n🆔 {user_id}\n"
            if username:
                display += f"🔗 https://t.me/{username}\n📛 @{username}"
            else:
                display += "📛 No username"
            add_history("phone_lookup", phone_clean, str(user_id), True)
            await message.answer(display, reply_markup=build_phone_result_markup(user_id, username, phone_clean))
        else:
            add_history("phone_lookup", phone_clean, "Not registered", False)
            await message.answer("❌ This number is not registered on Telegram.", reply_markup=main_menu_keyboard())
    except FloodWaitError as e:
        await message.answer(f"⏳ Too many requests. Try again in {e.seconds} seconds.", reply_markup=main_menu_keyboard())
    except Exception as e:
        add_history("phone_lookup", phone_clean, f"Error: {str(e)}", False)
        await message.answer(f"⚠️ An error occurred: {str(e)}", reply_markup=main_menu_keyboard())
    await state.finish()

# Bot Users (Info only – API limitation explained)
@dp.callback_query_handler(lambda c: c.data == "bot_users")
async def start_bot_users(call: types.CallbackQuery):
    await call.message.edit_text("🤖 Send the bot's username (e.g., @userinfobot).")
    await BotUsers.waiting_for_bot_username.set()
    await call.answer()

@dp.message_handler(state=BotUsers.waiting_for_bot_username)
async def process_bot_username(message: types.Message, state: FSMContext):
    bot_username = message.text.strip().lstrip('@')
    if not bot_username:
        await message.answer("❌ Please provide a valid username.", reply_markup=main_menu_keyboard())
        await state.finish()
        return
    await message.answer("⏳ Fetching bot info...")
    try:
        client = await get_telethon_client()
        entity = await client.get_entity(bot_username)
        if not entity.bot:
            await message.answer("❌ This is not a bot.", reply_markup=main_menu_keyboard())
            await state.finish()
            return
        bot_id = entity.id
        bot_name = entity.title or entity.username
        display = (
            f"🤖 <b>Bot Information</b>\n"
            f"Name: {bot_name}\n"
            f"Username: @{entity.username}\n"
            f"ID: {bot_id}\n"
            f"Description: {entity.description if hasattr(entity,'description') else 'N/A'}\n\n"
            "⚠️ <i>Telegram API does not allow retrieving a list of users who interacted with a bot. "
            "This feature shows the bot's profile instead.</i>"
        )
        add_history("bot_lookup", f"@{bot_username}", str(bot_id), True)
        await message.answer(display, reply_markup=build_bot_result_markup(entity.username, bot_id))
    except ValueError:
        add_history("bot_lookup", f"@{bot_username}", "Bot not found", False)
        await message.answer("❌ Bot not found. Check the username and try again.", reply_markup=main_menu_keyboard())
    except FloodWaitError as e:
        await message.answer(f"⏳ Rate limited. Wait {e.seconds}s.", reply_markup=main_menu_keyboard())
    except Exception as e:
        add_history("bot_lookup", f"@{bot_username}", f"Error: {str(e)}", False)
        await message.answer(f"⚠️ Error: {str(e)}", reply_markup=main_menu_keyboard())
    await state.finish()

# Anonymous ID Resolver
@dp.callback_query_handler(lambda c: c.data == "resolve_anon")
async def start_anon_resolve(call: types.CallbackQuery):
    await call.message.edit_text(
        "👤 Forward or paste a message from an anonymous bot.\n"
        "You can also send the message link (e.g., https://t.me/...)."
    )
    await ResolveAnon.waiting_for_message.set()
    await call.answer()

@dp.message_handler(state=ResolveAnon.waiting_for_message, content_types=types.ContentType.ANY)
async def process_anon_message(message: types.Message, state: FSMContext):
    try:
        client = await get_telethon_client()
        # Try to get forward info from the message itself
        forward_from = message.forward_from
        forward_sender_name = message.forward_sender_name
        if forward_from:
            user = await client.get_entity(forward_from.id)
            user_id = user.id
            username = user.username
            display = (
                f"👤 <b>Original Sender Found!</b>\n"
                f"ID: {user_id}\n"
                f"Username: @{username}\n"
                f"Name: {user.first_name} {user.last_name or ''}"
            )
            add_history("anon_resolve", f"From {forward_from.id}", str(user_id), True)
            await message.answer(display, reply_markup=build_anon_result_markup(user_id, username))
        elif forward_sender_name:
            display = (
                f"👤 <b>Anonymous Sender</b>\n"
                f"Name: {forward_sender_name}\n"
                f"❌ Cannot resolve real User ID – the forward is protected."
            )
            add_history("anon_resolve", forward_sender_name, "Anonymous", False)
            await message.answer(display)
        else:
            # Try to parse message link
            text = message.text or message.caption or ""
            tme_link = re.search(r"https://t\.me/(\w+)/(\d+)", text)
            if tme_link:
                chat_username, msg_id = tme_link.group(1), int(tme_link.group(2))
                msg = await client.get_messages(chat_username, ids=msg_id)
                if msg and msg.forward:
                    original = await client.get_entity(msg.forward.sender_id)
                    user_id = original.id
                    username = original.username
                    display = (
                        f"👤 <b>Original Sender from Link</b>\n"
                        f"ID: {user_id}\n"
                        f"Username: @{username}\n"
                        f"Name: {original.first_name} {original.last_name or ''}"
                    )
                    add_history("anon_resolve", text, str(user_id), True)
                    await message.answer(display, reply_markup=build_anon_result_markup(user_id, username))
                else:
                    await message.answer("❌ Could not resolve the link's sender.")
            else:
                await message.answer("❌ No forward information found. Please send a forwarded message or a valid t.me link.",
                                     reply_markup=main_menu_keyboard())
    except Exception as e:
        add_history("anon_resolve", "Unknown", f"Error: {str(e)}", False)
        await message.answer(f"⚠️ Error: {str(e)}", reply_markup=main_menu_keyboard())
    await state.finish()

# History
@dp.callback_query_handler(lambda c: c.data == "history")
async def show_history(call: types.CallbackQuery):
    rows = cursor.execute(
        "SELECT timestamp, type, input, result, success FROM history ORDER BY id DESC LIMIT 20"
    ).fetchall()
    if not rows:
        await call.message.edit_text("📜 No history yet.", reply_markup=main_menu_keyboard())
        return
    text = "📜 <b>Last 20 Searches</b>\n\n"
    for ts, htype, inp, res, suc in rows:
        icon = "✅" if suc else "❌"
        text += f"{icon} {ts[:16]} | {htype}: {inp} → {res}\n"
    await call.message.edit_text(text, reply_markup=main_menu_keyboard())
    await call.answer()

# Stats
@dp.callback_query_handler(lambda c: c.data == "stats")
async def show_stats(call: types.CallbackQuery):
    total, success, failures, rate = get_stats()
    text = (
        f"📊 <b>Userbot Statistics</b>\n\n"
        f"Total searches: {total}\n"
        f"Successful: {success}\n"
        f"Failed: {failures}\n"
        f"Success rate: {rate}%"
    )
    await call.message.edit_text(text, reply_markup=main_menu_keyboard())
    await call.answer()

# Copy ID (simulated)
@dp.callback_query_handler(lambda c: c.data.startswith("copy_"))
async def copy_id(call: types.CallbackQuery):
    user_id = call.data.split("_")[1]
    await call.answer(f"ID: {user_id} copied (simulated)", show_alert=True)

# Save actions (already saved)
@dp.callback_query_handler(lambda c: c.data.startswith("save_"))
async def save_to_history(call: types.CallbackQuery):
    await call.answer("✅ Already saved to history.", show_alert=True)

# New search callbacks
@dp.callback_query_handler(lambda c: c.data == "new_phone")
async def new_phone_search(call: types.CallbackQuery):
    await start_phone_search(call)

@dp.callback_query_handler(lambda c: c.data == "new_bot_users")
async def new_bot_users(call: types.CallbackQuery):
    await start_bot_users(call)

@dp.callback_query_handler(lambda c: c.data == "new_anon")
async def new_anon_resolve(call: types.CallbackQuery):
    await start_anon_resolve(call)

# ---------- Main ----------
if __name__ == "__main__":
    logger.info("Starting bot...")
    executor.start_polling(dp, skip_updates=True)
