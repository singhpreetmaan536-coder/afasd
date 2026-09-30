import os
import re
import time
import json
import threading
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
    """
    Reliable Instagram status check.

    ACTIVE  = confirmed profile data was returned.
    BANNED  = Instagram explicitly indicates the profile is unavailable.
    UNKNOWN = request was blocked/limited/incomplete; never treat this as
              ACTIVE or BANNED.
    """
    if not username:
        return {"exists": False, "status": "UNKNOWN", "reason": "empty_username"}

    username = username.strip().lower().replace("@", "")

    headers = {
        "x-ig-app-id": "936619743392459",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://www.instagram.com/{username}/",
    }

    # ---------------- METHOD 1: Instagram profile API ----------------
    try:
        response = requests.get(
            "https://www.instagram.com/api/v1/users/web_profile_info/",
            params={"username": username},
            headers=headers,
            timeout=12,
        )

        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = {}

            user = payload.get("data", {}).get("user")

            # Only a real user object can prove ACTIVE.
            if isinstance(user, dict) and (
                user.get("username") or user.get("pk") or user.get("id")
            ):
                returned_username = str(user.get("username") or "").lower()
                if returned_username and returned_username != username:
                    return {
                        "exists": False,
                        "status": "UNKNOWN",
                        "reason": "username_mismatch",
                    }

                return {
                    "exists": True,
                    "status": "ACTIVE",
                    "user": {
                        "full_name": user.get("full_name") or username,
                        "username": user.get("username") or username,
                        "biography": user.get("biography") or "",
                        "followers": user.get("edge_followed_by", {}).get("count", 0),
                        "following": user.get("edge_follow", {}).get("count", 0),
                        "posts": user.get("edge_owner_to_timeline_media", {}).get("count", 0),
                        "profile_pic": (
                            user.get("profile_pic_url_hd")
                            or user.get("profile_pic_url")
                            or ""
                        ),
                    },
                    "source": "profile_api",
                }

            # A clean API response with no user is meaningful only when
            # Instagram explicitly identifies the profile as missing.
            raw_api = response.text.lower()
            explicit_missing = any(
                marker in raw_api
                for marker in (
                    '"user":null',
                    '"user": null',
                    '"user_not_found"',
                    '"profile_not_found"',
                    '"user not found"',
                )
            )
            if explicit_missing:
                return {
                    "exists": False,
                    "status": "BANNED",
                    "reason": "profile_not_found",
                }

            return {
                "exists": False,
                "status": "UNKNOWN",
                "reason": "incomplete_api_response",
            }

        # 404 can be a genuine unavailable profile, but Instagram also
        # uses non-200 responses for rate limits/challenges. Do not guess.
        if response.status_code == 404:
            return {
                "exists": False,
                "status": "BANNED",
                "reason": "http_404",
            }

        return {
            "exists": False,
            "status": "UNKNOWN",
            "reason": f"http_{response.status_code}",
        }

    except (requests.RequestException, ValueError) as e:
        print(f"Instagram API check failed for @{username}: {e}")

    # ---------------- METHOD 2: Public profile HTML ----------------
    try:
        page_res = requests.get(
            f"https://www.instagram.com/{username}/",
            headers={
                "User-Agent": headers["User-Agent"],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.instagram.com/",
            },
            timeout=12,
        )

        html = page_res.text or ""
        html_lower = html.lower()

        # Explicit unavailable-profile evidence.
        unavailable_markers = (
            "page isn't available",
            "page isn’t available",
            "sorry, this page isn't available",
            "sorry, this page isn’t available",
            "the link you followed may be broken",
            "profile isn't available",
            "profile isn’t available",
        )

        if page_res.status_code == 404 or any(
            marker in html_lower for marker in unavailable_markers
        ):
            return {
                "exists": False,
                "status": "BANNED",
                "reason": "profile_unavailable",
            }

        if page_res.status_code != 200:
            return {
                "exists": False,
                "status": "UNKNOWN",
                "reason": f"html_http_{page_res.status_code}",
            }

        # IMPORTANT:
        # Generic Instagram HTML, og:title alone, login pages, challenge
        # pages, etc. are NOT enough to call an account ACTIVE.
        title_match = re.search(
            r'property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
            html,
            re.I,
        )
        desc_match = re.search(
            r'property=["\']og:description["\']\s+content=["\']([^"\']+)["\']',
            html,
            re.I,
        )
        pic_match = re.search(
            r'property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
            html,
            re.I,
        )

        full_name = ""
        biography = ""
        profile_pic = ""
        followers = following = posts = "—"

        if title_match:
            full_name = decode_html_entities(
                title_match.group(1).split("(")[0].strip()
            )

        if desc_match:
            raw = decode_html_entities(desc_match.group(1))
            raw = raw.split(" - See Instagram")[0].strip()
            biography = raw

            nums = re.search(
                r"([\d,.]+[KMB]?)\s+Followers.*?"
                r"([\d,.]+[KMB]?)\s+Following.*?"
                r"([\d,.]+[KMB]?)\s+Posts",
                raw,
                re.I,
            )
            if nums:
                followers = nums.group(1)
                following = nums.group(2)
                posts = nums.group(3)

        if pic_match:
            profile_pic = decode_html_entities(pic_match.group(1))

        # Require multiple pieces of profile evidence.
        # og:title by itself is deliberately insufficient.
        title_has_username = bool(
            full_name and username.lower() in full_name.lower()
        )
        has_stats = followers != "—" and following != "—" and posts != "—"
        has_profile_marker = bool(
            re.search(
                rf'"username"\s*:\s*"{re.escape(username)}"',
                html,
                re.I,
            )
        )

        if title_has_username and (has_stats or has_profile_marker):
            return {
                "exists": True,
                "status": "ACTIVE",
                "user": {
                    "full_name": full_name or username,
                    "username": username,
                    "biography": biography,
                    "followers": followers,
                    "following": following,
                    "posts": posts,
                    "profile_pic": profile_pic,
                },
                "source": "public_html",
            }

        return {
            "exists": False,
            "status": "UNKNOWN",
            "reason": "generic_or_incomplete_instagram_html",
        }

    except requests.RequestException as e:
        print(f"Instagram HTML check failed for @{username}: {e}")
    except Exception as e:
        print(f"Instagram HTML parse failed for @{username}: {e}")

    # Never convert network blocks/timeouts/incomplete responses into BANNED.
    return {
        "exists": False,
        "status": "UNKNOWN",
        "reason": "instagram_check_inconclusive",
    }


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

async def send_monitor_update(
    bot,
    chat_id: int,
    username: str,
    status: str,
    data: dict | None = None,
):
    """Send screenshot + a status message for a CONFIRMED state."""
    if status == "UNKNOWN":
        await bot.send_message(
            chat_id,
            f"⚠️ <b>CHECKING</b> — @{username}\n"
            "Instagram did not return enough reliable data. Retrying...",
            parse_mode=ParseMode.HTML,
        )
        return

    screenshot_path = await asyncio.to_thread(
        capture_instagram_screenshot, username
    )

    if screenshot_path and os.path.exists(screenshot_path):
        try:
            with open(screenshot_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=f"@{username}",
                )
        except Exception as e:
            print(f"Failed to send screenshot for @{username}: {e}")
        finally:
            try:
                os.remove(screenshot_path)
            except OSError:
                pass

    if status == "ACTIVE":
        u = (data or {}).get("user") or {}
        bio = u.get("biography") or "No bio"

        message = (
            "🟢 <b>ACTIVE</b>\n\n"
            f"👤 <b>{u.get('full_name') or username}</b>\n"
            f"🔗 @{u.get('username') or username}\n\n"
            "📋 <b>Profile Details</b>\n"
            f"• Followers: <b>{u.get('followers', '—')}</b>\n"
            f"• Following: <b>{u.get('following', '—')}</b>\n"
            f"• Posts: <b>{u.get('posts', '—')}</b>\n"
            f"• Bio: {bio}\n\n"
            "📡 <b>Activity Status</b>\n"
            "✅ ACTIVE"
        )
        await bot.send_message(chat_id, message, parse_mode=ParseMode.HTML)

    elif status == "BANNED":
        await bot.send_message(
            chat_id,
            f"🔴 <b>ACCOUNT BANNED</b>\n\n"
            f"👤 Account\n@{username}\n\n"
            "📋 <b>Profile Details</b>\n"
            "• Profile unavailable\n\n"
            "📡 <b>Activity Status</b>\n"
            "🚫 BANNED",
            parse_mode=ParseMode.HTML,
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
            "Usage: /monitor <username1> <username2> ...\n"
            "Example: /monitor instagram cristiano"
        )
        return

    chat_id = update.effective_chat.id

    with lock:
        monitors[chat_id] = {
            "usernames": cleaned,
            "statuses": {username: None for username in cleaned},
            "pending": {username: None for username in cleaned},
            "pending_counts": {username: 0 for username in cleaned},
            "active": True,
            "user_id": uid,
        }

    await update.effective_message.reply_text(
        "📡 <b>Monitor Started</b>\n\n"
        + "\n".join(f"👤 @{username}" for username in cleaned)
        + "\n\n"
        "⏳ Checking account status...\n"
        "🔒 Status changes require repeated confirmed checks.",
        parse_mode=ParseMode.HTML,
    )

    # Initial check. UNKNOWN is not converted into BANNED/ACTIVE.
    for username in cleaned:
        data = await asyncio.to_thread(check_instagram, username)
        status = data.get("status", "UNKNOWN")

        with lock:
            current = monitors.get(chat_id)
            if not current or not current.get("active"):
                return

            if status in ("ACTIVE", "BANNED"):
                current["statuses"][username] = status
                current["pending"][username] = None
                current["pending_counts"][username] = 0

        if status in ("ACTIVE", "BANNED"):
            await send_monitor_update(
                context.bot, chat_id, username, status, data
            )
        else:
            await send_monitor_update(
                context.bot, chat_id, username, "UNKNOWN", data
            )


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
    """
    Checks every 2 seconds.

    UNKNOWN never changes the confirmed state.
    A real ACTIVE <-> BANNED change must be confirmed by
    2 consecutive reliable checks before notifying.
    """
    CONFIRMATIONS_REQUIRED = 2

    while True:
        try:
            with lock:
                items = [
                    (
                        chat_id,
                        info.get("usernames", [])[:],
                        info.get("statuses", {}).copy(),
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
                        data = await asyncio.to_thread(
                            check_instagram, username
                        )
                        new_status = data.get("status", "UNKNOWN")

                        if new_status == "UNKNOWN":
                            # Do NOT overwrite the last confirmed status.
                            print(
                                f"⚠️ UNKNOWN check for @{username}: "
                                f"{data.get('reason', 'unknown')}"
                            )
                            continue

                        with lock:
                            current = monitors.get(chat_id)
                            if not current or not current.get("active"):
                                continue

                            old_status = current["statuses"].get(username)
                            pending = current["pending"].get(username)
                            pending_count = current["pending_counts"].get(
                                username, 0
                            )

                            # First confirmed result establishes the baseline.
                            if old_status is None:
                                current["statuses"][username] = new_status
                                current["pending"][username] = None
                                current["pending_counts"][username] = 0
                                should_notify = False
                            elif new_status == old_status:
                                current["pending"][username] = None
                                current["pending_counts"][username] = 0
                                should_notify = False
                            else:
                                # Possible change: require repeated confirmation.
                                if pending == new_status:
                                    pending_count += 1
                                else:
                                    pending = new_status
                                    pending_count = 1

                                current["pending"][username] = pending
                                current["pending_counts"][username] = pending_count

                                should_notify = (
                                    pending_count >= CONFIRMATIONS_REQUIRED
                                )

                                if should_notify:
                                    current["statuses"][username] = new_status
                                    current["pending"][username] = None
                                    current["pending_counts"][username] = 0

                        if should_notify:
                            await send_monitor_update(
                                app.bot,
                                chat_id,
                                username,
                                new_status,
                                data,
                            )

                    except Exception as e:
                        print(
                            f"Monitor check error for @{username}: {e}"
                        )

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
