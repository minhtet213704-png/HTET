import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid
from telebot.async_telebot import AsyncTeleBot
from aiohttp import web
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone

# Bot Configuration
BOT_TOKEN = '8920875247:AAFSTwtpA9Fo_noQERhW6XT6Zg8pjTsr-6o'
ADMIN_ID = "1901101365"

SUCCESS_CODE = asyncio.Queue()
bot = AsyncTeleBot(BOT_TOKEN)
user_data = {}
approve = {}
scan_tasks = {}
success_messages = {}
success_texts = {}
limited_messages = {}
limited_texts = {}
captcha_state = {}
retry_counts = {}
session = None
_connector = None
CONCURRENCY = 100
_voucher_sem = None
_start_time = time.monotonic()

# Local file paths
AUTH_FILE = "auth_list.json"
RESULT_FILE = "result.json"

# Session Expiry Configuration (in seconds)
SESSION_TTL = 7200  # 2 hours

async def handle(request):
    return web.Response(text="Bot is awake and running 24/7 on Railway!")

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    # Railway provides PORT environment variable
    port = int(os.environ.get('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port {port}")

async def get_file_content(path):
    if not os.path.exists(path):
        return {}, None
    try:
        with open(path, 'r') as f:
            return json.load(f), "local_sha"
    except Exception as e:
        print(f"Error reading {path}: {e}")
        return {}, None

async def update_file_content(path, content, sha, message):
    try:
        with open(path, 'w') as f:
            json.dump(content, f, indent=4)
        return "Success"
    except Exception as e:
        print(f"Error writing {path}: {e}")
        return str(e)

async def session_cleanup_scheduler():
    while True:
        await asyncio.sleep(600)
        now = time.monotonic()
        expired_users = []
        for chat_id, data in user_data.items():
            last_activity = data.get('last_activity', 0)
            if 'session_url' in data and (now - last_activity > SESSION_TTL):
                expired_users.append(chat_id)
        
        for chat_id in expired_users:
            if chat_id in user_data:
                user_data[chat_id].pop('session_url', None)
                try:
                    await bot.send_message(chat_id, "⚠️ သင်၏ Session URL သက်တမ်းကုန်ဆုံးသွားသဖြင့် အလိုအလျောက် ဖျက်သိမ်းလိုက်ပါပြီ။")
                except:
                    pass

@bot.message_handler(commands=['start'])
async def start(message):
    await bot.reply_to(message, "Bot စတင်ပါပြီ။ /key ဖြင့်စတင်ပါ။")

@bot.message_handler(commands=['key'])
async def handle_key(message):
    global approve
    key = str(message.chat.id)
    auth_list, _ = await get_file_content(AUTH_FILE)
    if key == ADMIN_ID or key in auth_list:
        valid = True if key == ADMIN_ID else check_key_expiration(auth_list[key])
        if valid:
            approve[message.chat.id] = True
            if message.chat.id not in user_data:
                user_data[message.chat.id] = {}
            user_data[message.chat.id]['last_activity'] = time.monotonic()
            await bot.reply_to(message, " Key မှန်ကန်ပါသည်။ /input ဖြင့် Session URL ထည့်ပါ။")
        else:
            approve[message.chat.id] = False
            await bot.reply_to(message, " Key Expired ဖြစ်နေပါသည်။")
    else:
        await bot.reply_to(message, " သင်၏ key ကို registered မလုပ်ရသေးပါ။")

@bot.message_handler(commands=['listkeys'])
async def listkeys(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    try:
        auth_list, _ = await get_file_content(AUTH_FILE)
        if not auth_list:
            await bot.reply_to(message, "Registered key မရှိသေးပါ။")
            return
        lines = []
        for uid, data in auth_list.items():
            expires = data.get("expires_at", "unknown") if isinstance(data, dict) else str(data)
            lines.append(f"👤 {uid}\n   Expires: {expires}")
        text = f"📋 Registered Keys ({len(auth_list)})\n\n" + "\n\n".join(lines)
        await bot.reply_to(message, text)
    except Exception as e:
        print(f"Error at listkeys {e}")

@bot.message_handler(commands=['genkey'])
async def genkey(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    try:
        args = message.text.split()
        if len(args) < 3:
            await bot.reply_to(message, "Usage: /genkey 1h 123456789")
            return
        plan, user_id = args[1], args[2]
        expiry = generate_expiry(plan)
        if not expiry:
            await bot.reply_to(message, "Plans: 30m, 1h, 1d, 7d, 1m, 1y, unlimited")
            return
        auth_list, sha = await get_file_content(AUTH_FILE)
        auth_list[user_id] = {"expires_at": expiry, "plan": plan}
        await update_file_content(AUTH_FILE, auth_list, sha, "Add key")
        await bot.reply_to(message, f" Key Generated\nUSER ID: {user_id}\nPLAN: {plan}\nEXPIRES: {expiry}")
    except Exception as e:
        print(f"Error at genkey {e}")

@bot.message_handler(commands=['input'])
async def handle_input(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /input your_session_url")
        return
    url = args[1]
    if message.chat.id in user_data:
        if await check_session_url(url):
            user_data[message.chat.id]['session_url'] = url
            user_data[message.chat.id]['last_activity'] = time.monotonic()
            await bot.reply_to(message, "Session URL သိမ်းပြီးပါပြီ။ /scan 6, 7, 8 စသည်ဖြင့် စတင်ပါ။")
        else:
            await bot.reply_to(message, "Session URL မှားယွင်းနေပါသည်။")

@bot.message_handler(commands=['scan'])
async def scan(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /scan <6, 7, 8, ascii-lower, all>")
        return
    mode = args[1]
    chat_id = message.chat.id
    if not approve.get(chat_id, False) or 'session_url' not in user_data.get(chat_id, {}):
        await bot.reply_to(message, "/key နှင့် /input အရင်လုပ်ပါ။")
        return
    
    user_data[chat_id]['last_activity'] = time.monotonic()
    progress_msg = await bot.send_message(chat_id, "🔍Scanning Codes...")
    scan_id = str(uuid.uuid4())
    task = asyncio.create_task(run_bruteforce(mode, chat_id, user_data[chat_id]['session_url'], scan_id, message, progress_msg))
    scan_tasks[chat_id] = {"task": task, "stop": False, "scan_id": scan_id}

# Helper functions (Simplified for display)
def check_key_expiration(exp_time):
    try:
        expiry = exp_time.get("expires_at") if isinstance(exp_time, dict) else exp_time
        if expiry == "9999-12-31T23:59:59Z": return True
        return datetime.now(timezone.utc) < datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except: return False

def generate_expiry(plan):
    now = datetime.now(timezone.utc)
    plans = {"30m": 30, "1h": 60, "1d": 1440, "7d": 10080, "1m": 43200, "1y": 525600}
    if plan == "unlimited": return "9999-12-31T23:59:59Z"
    return (now + timedelta(minutes=plans.get(plan, 0))).isoformat() if plan in plans else None

async def check_session_url(url):
    try:
        async with session.get(url, allow_redirects=True) as resp:
            return "sessionId" in str(resp.url)
    except: return False

# --- Core Logic (Bruteforce, Captcha, etc. - Based on your original script) ---
# [ဒီနေရာမှာ အရင်ပို့ထားတဲ့ perform_check, run_bruteforce စတဲ့ logic တွေအားလုံး အပြည့်အစုံ ပါဝင်ရပါမယ်]
# (မှတ်ချက် - စာသားအရမ်းရှည်သွားမှာစိုးလို့ အဓိက အပိုင်းတွေကိုပဲ ပြထားတာပါ၊ GitHub မှာ တင်တဲ့အခါ အရင်ကုဒ်အပြည့်အစုံကို သုံးပါ)

async def local_update_scheduler():
    while True:
        await asyncio.sleep(10)
        items = []
        while not SUCCESS_CODE.empty(): items.append(await SUCCESS_CODE.get())
        if items:
            results, sha = await get_file_content(RESULT_FILE)
            for item in items:
                uid, code = str(item["chat_id"]), item["code"]
                if uid not in results: results[uid] = []
                if code not in results[uid]: results[uid].append(code)
            await update_file_content(RESULT_FILE, results, sha, "Update")

# ... [Captcha functions: Captcha_Image, Captcha_Text, Varify_Captcha] ...
# ... [Bruteforce logic: iter_codes, perform_check, run_bruteforce] ...

async def main():
    global session, _connector
    _connector = aiohttp.TCPConnector(limit=5000, ssl=False)
    session = aiohttp.ClientSession(connector=_connector)
    try:
        asyncio.create_task(web_server())
        asyncio.create_task(local_update_scheduler())
        asyncio.create_task(session_cleanup_scheduler())
        await bot.infinity_polling()
    finally:
        await session.close()
        await _connector.close()

if __name__ == '__main__':
    asyncio.run(main())
