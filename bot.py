import os
import re
import time
import json
import threading
import asyncio
import requests
from html import unescape, escape
from dotenv import load_dotenv

load_dotenv()

# ============== INSTAGRAM CHECK ==============

def decode_html_entities(text: str) -> str:
    """Exact same as server.js decodeHTMLEntities"""
    if not text:
        return ""
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return unescape(text)


def check_instagram(username: str) -> dict:
    """Exact logic from server.js /api/check"""
    if not username:
        return {"exists": False, "status": "BANNED"}

    username = username.strip().lower().replace("@", "")

    # Method 1
    try:
        headers = {
            "x-ig-app-id": "936619743392459",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": "https://www.instagram.com/"
        }

        response = requests.get(
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
            headers=headers,
            timeout=12
        )

        if response.status_code == 200:
            data = response.json()
            user = data.get("data", {}).get("user")
            if user:
                return {
                    "exists": True,
                    "status": "ACTIVE",
                    "user": {
                        "full_name": user.get("full_name") or username,
                        "username": user.get("username"),
                        "biography": user.get("biography") or "",
                        "followers": user.get("edge_followed_by", {}).get("count", 0),
                        "following": user.get("edge_follow", {}).get("count", 0),
                        "posts": user.get("edge_owner_to_timeline_media", {}).get("count", 0),
                        "profile_pic": user.get("profile_pic_url_hd") or user.get("profile_pic_url") or ""
                    }
                }
    except Exception:
        pass

    # Method 2 - Public page
    try:
        page_res = requests.get(
            f"https://www.instagram.com/{username}/",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
            timeout=12
        )

        html = page_res.text

        if "og:title" in html or f'"username":"{username}"' in html:
            full_name = username
            biography = ""
            profile_pic = ""
            followers = following = posts = "—"

            title_match = re.search(r'property="og:title" content="([^"]+)"', html, re.I)
            if title_match:
                full_name = decode_html_entities(title_match.group(1).split("(")[0].strip())

            desc_match = re.search(r'property="og:description" content="([^"]+)"', html, re.I)
            if desc_match:
                raw = decode_html_entities(desc_match.group(1))
                raw = raw.split(" - See Instagram")[0].strip()
                biography = raw

                nums = re.search(
                    r"([\d,.]+[KMB]?)\s+Followers.*?([\d,.]+[KMB]?)\s+Following.*?([\d,.]+[KMB]?)\s+Posts",
                    raw, re.I
                )
                if nums:
                    followers = nums.group(1)
                    following = nums.group(2)
                    posts = nums.group(3)

            pic_match = re.search(r'property="og:image" content="([^"]+)"', html, re.I)
            if pic_match:
                profile_pic = pic_match.group(1)

            return {
                "exists": True,
                "status": "ACTIVE",
                "user": {
                    "full_name": full_name,
                    "username": username,
                    "biography": biography,
                    "followers": followers,
                    "following": following,
                    "posts": posts,
                    "profile_pic": profile_pic
                }
            }
    except Exception:
        pass

    return {"exists": False, "status": "BANNED"}


def format_profile(data: dict) -> str:
    if not data.get("exists") or not data.get("user"):
        return "❌ Account not found or <b>BANNED</b>"

    u = data["user"]
    return (
        f"✅ <b>ACTIVE</b>\n\n"
        f"👤 <b>{u.get('full_name', '')}</b>\n"
        f"🔗 @{u.get('username', '')}\n"
        f"📝 {u.get('biography') or 'No bio'}\n\n"
        f"👥 Followers: <b>{u.get('followers', '—')}</b>\n"
        f"➡️ Following: <b>{u.get('following', '—')}</b>\n"
        f"📸 Posts: <b>{u.get('posts', '—')}</b>"
    )



# ============== TELEGRAM BOT ==============
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN/TELEGRAM_BOT_TOKEN is not set")
if not ADMIN_ID:
    print("⚠️ ADMIN_ID is not set. Admin commands will be unavailable.")

SUBSCRIBERS_FILE = os.getenv("SUBSCRIBERS_FILE", "subscribers.json")

# chat_id -> {"usernames": [...], "statuses": {...}, "active": bool, "user_id": int}
monitors = {}
lock = threading.Lock()

def load_subscribers() -> set:
    try:
        if os.path.exists(SUBSCRIBERS_FILE):
            with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                return set(int(x) for x in json.load(f))
    except Exception as e:
        print(f"Failed to load subscribers: {e}")
    return set()

def save_subscribers(subs: set):
    try:
        with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(int(x) for x in subs), f)
    except Exception as e:
        print(f"Failed to save subscribers: {e}")

subscribers = load_subscribers()

def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID

def is_allowed(user_id: int) -> bool:
    return is_admin(user_id) or user_id in subscribers

async def not_allowed_message(update: Update):
    await update.effective_message.reply_text(
        "❌ <b>Access Denied</b>\n\n"
        "You don't have an active subscription.\n"
        "Contact the admin to get access.",
        parse_mode=ParseMode.HTML
    )

def clean_usernames(args):
    cleaned = []
    for item in args:
        for username in item.split(","):
            username = username.strip().lower().replace("@", "")
            if username and username not in cleaned:
                cleaned.append(username)
    return cleaned

async def send_monitor_update(bot, chat_id: int, username: str, status: str, data: dict | None = None):
    """Send status and Instagram profile values as text (no screenshot)."""
    if status == "ACTIVE" and data and data.get("user"):
        u = data["user"]

        full_name = escape(str(u.get("full_name") or username))
        ig_username = escape(str(u.get("username") or username))
        biography = escape(str(u.get("biography") or "No bio"))
        followers = escape(str(u.get("followers", "—")))
        following = escape(str(u.get("following", "—")))
        posts = escape(str(u.get("posts", "—")))

        message = (
            "✅ <b>ACTIVE</b>\n\n"
            f"👤 <b>{full_name}</b>\n"
            f"🔗 @{ig_username}\n"
            f"📝 {biography}\n\n"
            f"👥 Followers: <b>{followers}</b>\n"
            f"➡️ Following: <b>{following}</b>\n"
            f"📸 Posts: <b>{posts}</b>"
        )
        await bot.send_message(chat_id, message, parse_mode=ParseMode.HTML)
        return

    await bot.send_message(
        chat_id,
        f"🚫 <b>ACCOUNT BANNED</b> — @{escape(username)}",
        parse_mode=ParseMode.HTML
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "👋 <b>Welcome to Instagram Monitor</b>\n\n"
        "Monitor one or multiple Instagram usernames.\n\n"
        "📋 <b>Commands</b>\n"
        "/monitor username1 username2 — Start monitoring\n"
        "/stop — Stop monitoring\n"
        "/status — Current monitor status\n"
        "/help — Show help\n\n"
        "Example:\n"
        "<code>/monitor instagram cristiano</code>",
        parse_mode=ParseMode.HTML
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("❌ Admin only command.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /adduser <telegram_user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid Telegram user ID.")
        return

    if user_id in subscribers:
        await update.effective_message.reply_text(f"⚠️ User <code>{user_id}</code> is already subscribed.", parse_mode=ParseMode.HTML)
        return

    subscribers.add(user_id)
    save_subscribers(subscribers)
    await update.effective_message.reply_text(f"✅ User <code>{user_id}</code> added successfully.", parse_mode=ParseMode.HTML)

async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("❌ Admin only command.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /removeuser <telegram_user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid Telegram user ID.")
        return

    subscribers.discard(user_id)
    save_subscribers(subscribers)

    with lock:
        for chat_id, info in list(monitors.items()):
            if info.get("user_id") == user_id:
                del monitors[chat_id]

    await update.effective_message.reply_text(f"🔴 User <code>{user_id}</code> removed.", parse_mode=ParseMode.HTML)

async def listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("❌ Admin only command.")
        return

    if not subscribers:
        await update.effective_message.reply_text("📭 No subscribers yet.")
        return

    lines = [f"• <code>{uid}</code>" for uid in sorted(subscribers)]
    await update.effective_message.reply_text(
        f"👥 <b>Subscribers ({len(subscribers)}):</b>\n\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML
    )

async def monitor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await not_allowed_message(update)
        return

    cleaned = clean_usernames(context.args)
    if not cleaned:
        await update.effective_message.reply_text(
            "Usage: /monitor <username1> <username2> ...\nExample: /monitor instagram cristiano"
        )
        return

    chat_id = update.effective_chat.id
    with lock:
        monitors[chat_id] = {
            "usernames": cleaned,
            "statuses": {username: None for username in cleaned},
            "active": True,
            "user_id": uid
        }

    await update.effective_message.reply_text(
        "🟢 <b>Monitor started</b>\n" +
        "\n".join(f"• @{username}" for username in cleaned) +
        "\n\nYou'll only receive a message when a status changes.",
        parse_mode=ParseMode.HTML
    )

    # First check: send profile values and status as text.
    for username in cleaned:
        data = await asyncio.to_thread(check_instagram, username)
        status = data.get("status", "BANNED")

        with lock:
            current = monitors.get(chat_id)
            if not current or not current.get("active"):
                return
            current["statuses"][username] = status

        await send_monitor_update(context.bot, chat_id, username, status, data)

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await not_allowed_message(update)
        return

    chat_id = update.effective_chat.id
    with lock:
        monitor = monitors.get(chat_id)
        if monitor and monitor.get("active"):
            usernames = list(monitor.get("usernames", []))
            del monitors[chat_id]
        else:
            usernames = []

    if usernames:
        await update.effective_message.reply_text(
            "🔴 <b>Monitoring stopped</b>\n" +
            "\n".join(f"• @{username}" for username in usernames),
            parse_mode=ParseMode.HTML
        )
    else:
        await update.effective_message.reply_text("ℹ️ No active monitor.")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await not_allowed_message(update)
        return

    chat_id = update.effective_chat.id
    with lock:
        monitor = monitors.get(chat_id)
        if monitor and monitor.get("active"):
            usernames = list(monitor.get("usernames", []))
            statuses = dict(monitor.get("statuses", {}))
        else:
            usernames, statuses = [], {}

    if not usernames:
        await update.effective_message.reply_text(
            "ℹ️ No active monitor.\nUse /monitor <username>"
        )
        return

    lines = [
        f"• @{username} — <b>{statuses.get(username) or 'CHECKING'}</b>"
        for username in usernames
    ]
    await update.effective_message.reply_text(
        "📡 <b>Current monitor status</b>\n\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML
    )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"Telegram error: {context.error}")

async def telegram_monitor_loop(app):
    """Checks every 2 seconds and notifies only on status changes."""
    while True:
        try:
            with lock:
                items = [
                    (
                        chat_id,
                        info.get("usernames", [])[:],
                        info.get("statuses", {}).copy()
                    )
                    for chat_id, info in monitors.items()
                    if info.get("active")
                ]

            for chat_id, usernames, old_statuses in items:
                for username in usernames:
                    with lock:
                        current = monitors.get(chat_id)
                        if not current or not current.get("active"):
                            break

                    try:
                        data = await asyncio.to_thread(check_instagram, username)
                        new_status = data.get("status", "BANNED")
                        old_status = old_statuses.get(username)

                        if old_status is not None and new_status != old_status:
                            await send_monitor_update(
                                app.bot, chat_id, username, new_status, data
                            )

                        with lock:
                            if chat_id in monitors:
                                monitors[chat_id]["statuses"][username] = new_status

                    except Exception as e:
                        print(f"Monitor check error for @{username}: {e}")

        except Exception as e:
            print(f"Monitor loop error: {e}")

        await asyncio.sleep(2)

async def post_init(app):
    app.create_task(telegram_monitor_loop(app))

def main():
    print("🚀 Instagram Ban Monitor Telegram Bot starting...")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"👥 Loaded subscribers: {len(subscribers)}")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("monitor", monitor_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("adduser", adduser))
    app.add_handler(CommandHandler("removeuser", removeuser))
    app.add_handler(CommandHandler("listusers", listusers))
    app.add_error_handler(error_handler)

    print("✅ Telegram bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
