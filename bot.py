import os
import re
import time
import json
import asyncio
import requests
from playwright.sync_api import sync_playwright
from html import unescape
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



# ============== INSTAGRAM SCREENSHOT ==============

def capture_instagram_screenshot(username: str):
    """
    Capture only the Instagram profile header:
    PFP + username + followers/following/posts + bio.
    It also closes/removes the login/signup popup before taking the screenshot.
    """
    username = username.strip().lower().replace("@", "")
    safe_username = re.sub(r"[^a-zA-Z0-9_.-]", "_", username)
    path = os.path.abspath(f"instagram_{safe_username}.png")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            page = browser.new_page(
                viewport={"width": 1280, "height": 800},
                device_scale_factor=1
            )

            page.goto(
                f"https://www.instagram.com/{username}/",
                wait_until="domcontentloaded",
                timeout=20000
            )

            page.wait_for_timeout(3000)

            # ---------------------------------------------------------
            # CLOSE INSTAGRAM LOGIN / SIGN-UP POPUP
            # ---------------------------------------------------------

            # First try Escape.
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(800)
            except Exception:
                pass

            # Try the popup's close button.
            close_selectors = [
                'div[role="dialog"] button[aria-label="Close"]',
                'div[role="dialog"] button[aria-label*="Close" i]',
                'div[role="dialog"] [aria-label="Close"]',
                'button[aria-label="Close"]',
            ]

            popup_closed = False

            for selector in close_selectors:
                try:
                    close_button = page.locator(selector).first

                    if close_button.count() and close_button.is_visible():
                        close_button.click(force=True)
                        page.wait_for_timeout(800)
                        popup_closed = True
                        break
                except Exception:
                    pass

            # Some Instagram versions don't expose an aria-label on the X.
            # If a dialog still exists, click its top-right corner.
            if not popup_closed:
                try:
                    dialog = page.locator('div[role="dialog"]').first

                    if dialog.count() and dialog.is_visible():
                        box = dialog.bounding_box()

                        if box:
                            page.mouse.click(
                                box["x"] + box["width"] - 34,
                                box["y"] + 34
                            )
                            page.wait_for_timeout(800)
                            popup_closed = True
                except Exception:
                    pass

            # Last resort: remove visible modal dialogs from the screenshot.
            # This does NOT affect the Instagram API/check logic.
            try:
                page.locator('[role="dialog"]').evaluate_all(
                    """elements => elements.forEach(element => {
                        element.style.display = 'none';
                    })"""
                )
            except Exception:
                pass

            # ---------------------------------------------------------
            # FIND PROFILE HEADER
            # ---------------------------------------------------------

            profile_header = None

            # Instagram layout can change, so try several selectors.
            selectors = [
                f'header:has-text("{username}")',
                "main header",
                "header",
            ]

            for selector in selectors:
                try:
                    candidate = page.locator(selector).first

                    if candidate.count() and candidate.is_visible():
                        profile_header = candidate
                        break
                except Exception:
                    pass

            # ---------------------------------------------------------
            # SCREENSHOT ONLY THE PROFILE HEADER
            # ---------------------------------------------------------

            if profile_header:
                profile_header.screenshot(path=path)

            else:
                # Fallback crop. This is intentionally small and does NOT
                # capture the complete Instagram page/posts grid.
                page.screenshot(
                    path=path,
                    clip={
                        "x": 0,
                        "y": 0,
                        "width": 900,
                        "height": 250
                    }
                )

            browser.close()

        return path

    except Exception as e:
        print(f"Failed to capture Instagram screenshot for @{username}: {e}")
        return None


# ============== DISCORD COMMANDS ==============

@bot.command(name="start")
async def cmd_start(ctx):
    text = (
        "👋 **YOUR WELCOME TEXT HERE**\n\n"
        "YOUR DESCRIPTION / INTRO TEXT HERE...\n\n"
        "📋 **Commands:**\n"
        "`/monitor <username>` — Start monitoring\n"
        "`/stop` — Stop monitoring\n"
        "`/status` — Current monitor status\n\n"
        "Example:\n"
        "`/monitor cristiano`\n"
        "`/check instagram`"
    )
    await ctx.reply(text)


@bot.command(name="help")
async def cmd_help(ctx):
    await cmd_start(ctx)


# ---------- ADMIN COMMANDS ----------

@bot.command(name="adduser")
async def cmd_adduser(ctx, chat_id: str = None):
    if not is_admin(ctx.author.id):
        await ctx.reply("❌ Admin only command.")
        return

    if not chat_id:
        await ctx.reply("Usage: `/adduser <discord_user_id>`\nExample: `/adduser 123456789`")
        return

    try:
        user_id = int(chat_id.strip())
    except ValueError:
        await ctx.reply("❌ Invalid Discord user ID. It should be a number.")
        return

    if user_id in subscribers:
        await ctx.reply(f"⚠️ User `{user_id}` is already subscribed.")
        return

    subscribers.add(user_id)
    save_subscribers(subscribers)
    await ctx.reply(f"✅ User `{user_id}` added successfully.")


@bot.command(name="removeuser")
async def cmd_removeuser(ctx, chat_id: str = None):
    if not is_admin(ctx.author.id):
        await ctx.reply("❌ Admin only command.")
        return

    if not chat_id:
        await ctx.reply("Usage: `/removeuser <discord_user_id>`\nExample: `/removeuser 123456789`")
        return

    try:
        user_id = int(chat_id.strip())
    except ValueError:
        await ctx.reply("❌ Invalid Discord user ID. It should be a number.")
        return

    if user_id not in subscribers:
        await ctx.reply(f"⚠️ User `{user_id}` is not in subscribers.")
        return

    subscribers.discard(user_id)
    save_subscribers(subscribers)

    with lock:
        # Stop all monitors belonging to this Discord user.
        for channel_id, info in list(monitors.items()):
            if info.get("user_id") == user_id:
                del monitors[channel_id]

    await ctx.reply(f"🔴 User `{user_id}` removed.")


@bot.command(name="listusers")
async def cmd_listusers(ctx):
    if not is_admin(ctx.author.id):
        await ctx.reply("❌ Admin only command.")
        return

    if not subscribers:
        await ctx.reply("📭 No subscribers yet.")
        return

    lines = [f"• `{uid}`" for uid in sorted(subscribers)]
    text = f"👥 **Subscribers ({len(subscribers)}):**\n\n" + "\n".join(lines)
    await ctx.reply(text)


# ---------- USER COMMANDS (subscription required) ----------

@bot.command(name="monitor")
async def cmd_monitor(ctx, *usernames):
    """
    Start monitoring one or more Instagram usernames.

    Example:
        /monitor instagram cristiano
        /monitor @instagram @cristiano
    """
    if not is_allowed(ctx.author.id):
        await not_allowed_message(ctx)
        return

    # Allow both spaces and comma-separated usernames.
    cleaned = []
    for item in usernames:
        for username in item.split(","):
            username = username.strip().lower().replace("@", "")
            if username and username not in cleaned:
                cleaned.append(username)

    if not cleaned:
        await ctx.reply("Usage: `/monitor <username1> <username2> ...`")
        return

    channel_id = ctx.channel.id

    with lock:
        monitors[channel_id] = {
            "usernames": cleaned,
            "statuses": {username: None for username in cleaned},
            "active": True,
            "user_id": ctx.author.id
        }

    await ctx.reply(
        "🟢 **Monitor started**\n"
        + "\n".join(f"• @{username}" for username in cleaned)
        + "\n\nUpdate will be showed if any activity found"
    )

    # First check: screenshot FIRST, then only ACTIVE / ACCOUNT BANNED text.
    for username in cleaned:
        data = await asyncio.to_thread(check_instagram, username)
        status = data.get("status", "BANNED")

        with lock:
            if channel_id not in monitors:
                return
            if not monitors[channel_id].get("active"):
                return
            monitors[channel_id]["statuses"][username] = status

        await send_monitor_update(ctx.channel, username, status)


@bot.command(name="stop")
async def cmd_stop(ctx):
    if not is_allowed(ctx.author.id):
        await not_allowed_message(ctx)
        return

    channel_id = ctx.channel.id

    with lock:
        monitor = monitors.get(channel_id)

        if monitor and monitor.get("active"):
            usernames = list(monitor.get("usernames", []))
            del monitors[channel_id]
        else:
            usernames = []

    if usernames:
        await ctx.reply(
            "🔴 **Monitoring stopped**\n"
            + "\n".join(f"• @{username}" for username in usernames)
        )
    else:
        await ctx.reply("YOUR NO ACTIVE MONITOR TEXT HERE")


@bot.command(name="status")
async def cmd_status(ctx):
    if not is_allowed(ctx.author.id):
        await not_allowed_message(ctx)
        return

    channel_id = ctx.channel.id

    with lock:
        monitor = monitors.get(channel_id)

        if monitor and monitor.get("active"):
            usernames = list(monitor.get("usernames", []))
            statuses = dict(monitor.get("statuses", {}))
        else:
            usernames = []
            statuses = {}

    if not usernames:
        await ctx.reply(
            "YOUR NO ACTIVE MONITOR TEXT HERE\n"
            "Use `/monitor <username>`"
        )
        return

    lines = [
        f"• @{username} — **{statuses.get(username) or 'CHECKING'}**"
        for username in usernames
    ]

    await ctx.reply(
        "📡 **Current monitor status**\n\n" + "\n".join(lines)
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

async def send_monitor_update(bot, chat_id: int, username: str, status: str):
    screenshot_path = await asyncio.to_thread(capture_instagram_screenshot, username)

    if screenshot_path and os.path.exists(screenshot_path):
        try:
            with open(screenshot_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=f"@{username}"
                )
        except Exception as e:
            print(f"Failed to send screenshot for @{username}: {e}")
        finally:
            try:
                os.remove(screenshot_path)
            except OSError:
                pass

    if status == "ACTIVE":
        await bot.send_message(chat_id, f"✅ <b>ACTIVE</b> — @{username}", parse_mode=ParseMode.HTML)
    else:
        await bot.send_message(chat_id, f"🚫 <b>ACCOUNT BANNED</b> — @{username}", parse_mode=ParseMode.HTML)

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

    # First check: screenshot first, then status.
    for username in cleaned:
        data = await asyncio.to_thread(check_instagram, username)
        status = data.get("status", "BANNED")

        with lock:
            current = monitors.get(chat_id)
            if not current or not current.get("active"):
                return
            current["statuses"][username] = status

        await send_monitor_update(context.bot, chat_id, username, status)

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
                                app.bot, chat_id, username, new_status
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
