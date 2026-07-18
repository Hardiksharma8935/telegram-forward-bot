import os
import re
import json
import html
import threading
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
import telebot

BOT_TOKEN       = os.environ.get("BOT_TOKEN")
GROUP_CHAT_ID   = os.environ.get("GROUP_CHAT_ID")
# Railway pe restart/redeploy hone par local file delete ho jaati hai (ephemeral
# filesystem). Agar duplicate list permanently save rakhni hai, toh Railway me
# ek Volume attach karo aur SENT_LINKS_FILE env var ko us mounted path par
# point karo (e.g. "/data/sent_links.json"). Warna default sirf current
# deployment ke session tak hi yaad rahega.
SENT_LINKS_FILE = os.environ.get("SENT_LINKS_FILE", "sent_links.json")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN set nahi hai!")
if not GROUP_CHAT_ID:
    raise ValueError("❌ GROUP_CHAT_ID set nahi hai!")

bot = telebot.TeleBot(BOT_TOKEN)

# Koi bhi t.me link (public ya private) ko dhoondne ke liye regex
TG_LINK_REGEX = r'(https?://(?:t\.me|telegram\.me)/[^\s]+)'

SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

file_lock = threading.Lock()


# ── Link normalize karo (duplicate detection ke liye) ──────────────────────────

def normalize_link(url: str) -> str:
    """
    Alag-alag formatting wale same links ko ek jaisa bana deta hai, taaki
    duplicate check chhoote na — trailing punctuation (jaise sentence ke
    end ka '.'), trailing slash, http vs https, aur t.me vs telegram.me,
    sab yahan normalize ho jaate hain.
    """
    url = url.strip().rstrip('.,)>]}\'"')
    parts = urlsplit(url)
    host = parts.netloc.lower().replace("telegram.me", "t.me")
    path = parts.path.rstrip("/")
    return urlunsplit(("https", host, path, "", ""))


# ── Link ka naam fetch karo ────────────────────────────────────────────────────

def get_tg_title(url: str):
    """
    t.me preview page se group/channel naam nikalo.
    - Valid link   → naam (string) return karo
    - Expired link → None return karo
    """
    try:
        r = requests.get(url, headers=SCRAPE_HEADERS, timeout=8)
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        # ── Expired / invalid check ────────────────────────────────────────────
        desc = soup.find("div", class_="tgme_page_description")
        if desc and any(x in desc.text.lower() for x in
                        ["no longer valid", "invalid", "expired", "link is not valid"]):
            return None

        # ── Naam nikalo ────────────────────────────────────────────────────────
        # Method 1: og:title meta tag (sabse reliable)
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content") and og["content"].lower() != "telegram":
            return og["content"].strip()

        # Method 2: tgme_page_title div (fallback)
        title_div = soup.find("div", class_="tgme_page_title")
        if title_div:
            span = title_div.find("span")
            if span and span.text.strip():
                return span.text.strip()

        return None

    except Exception as e:
        print(f"⚠️  Scrape error [{url}]: {e}")
        return None


# ── Duplicate tracking ─────────────────────────────────────────────────────────

def load_sent_links() -> set:
    try:
        with open(SENT_LINKS_FILE, "r") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_sent_links(links: set):
    with open(SENT_LINKS_FILE, "w") as f:
        json.dump(list(links), f)

sent_links = load_sent_links()
print(f"📋 {len(sent_links)} links pehle se record mein hain.")


# ── Startup ────────────────────────────────────────────────────────────────────
print("🔧 Webhook delete kar rahe hain...")
bot.remove_webhook()
print("✅ Polling shuru...")


# ── Handlers ───────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def handle_start(message):
    if message.chat.type != "private":
        return
    bot.reply_to(
        message,
        "👋 Hello!\n\n"
        "Telegram group/channel ka koi bhi link bhejo — public ya private.\n"
        "Main uska naam aur link group mein forward kar dunga. 🔗\n\n"
        "❌ Expired aur duplicate links ignore ho jayenge."
    )


@bot.message_handler(func=lambda m: m.chat.type == "private" and bool(m.text))
def handle_message(message):
    global sent_links
    text = message.text

    # Message me se saare links ek sath nikal rahe hain (Bulk support)
    raw_links = re.findall(TG_LINK_REGEX, text)

    if not raw_links:
        bot.reply_to(message, "Koi Telegram link nahi mila.\nExample: https://t.me/groupname")
        return

    sent      = 0
    duplicate = 0
    expired   = 0

    # Ek-ek karke saare links ko check karne ke liye loop
    for raw_url in raw_links:
        url = normalize_link(raw_url)

        # ── 1. DUPLICATE CHECK (SABSE PEHLE) ───────────────────────────────────
        # Same link kitni bhi baar bheja jaye — pehli baar ke baad hamesha
        # yahin se turant ignore ho jayega, dobara scrape bhi nahi hoga.
        with file_lock:
            is_duplicate = url in sent_links

        if is_duplicate:
            duplicate += 1
            print(f"🔁 Duplicate skip kiya (Instant Ignore): {url}")
            continue

        # ── 2. EXPIRED / NAME CHECK ───────────────────────────────────────────
        title = get_tg_title(url)

        if title is None:
            expired += 1
            print(f"⏭️  Expired ya Invalid link skip kiya: {url}")
            continue

        # ── 3. GROUP ME FORWARD (naam upar, link neeche) ───────────────────────
        try:
            safe_title = html.escape(title)
            safe_url   = html.escape(url)
            bot.send_message(
                GROUP_CHAT_ID,
                f"📢 <b>{safe_title}</b>\n🔗 {safe_url}",
                parse_mode="HTML"
            )
            # Link ko database/file me add karo taaki dobara na bheje
            with file_lock:
                sent_links.add(url)
                save_sent_links(sent_links)
            sent += 1
            print(f"✅ Group me forward kiya: {title}")
        except Exception as e:
            print(f"❌ Group me bhejne me error aaya: {e}")

    # ── 4. USER KO REPORT REPLIES ──────────────────────────────────────────────
    report = []
    if sent > 0:
        report.append(f"✅ {sent} naye link(s) forward ho gaye!")
    if duplicate > 0:
        report.append(f"🔁 {duplicate} duplicate link(s) ignore kiye gaye.")
    if expired > 0:
        report.append(f"⚠️ {expired} expired/invalid link(s) skip kiye gaye.")

    if report:
        bot.reply_to(message, "\n".join(report))
    else:
        bot.reply_to(message, "Kuch process nahi hua.")


# ── Start ──────────────────────────────────────────────────────────────────────
print("🤖 Bot chal raha hai...")
bot.infinity_polling(timeout=30, long_polling_timeout=20, none_stop=True)
