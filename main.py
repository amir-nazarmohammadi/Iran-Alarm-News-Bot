import os
import re
import json
import asyncio
import hashlib
import logging
import tempfile

from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError


# =====================================================
# تنظیمات اصلی
# =====================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TG_SESSION = os.environ["TG_SESSION"]

DESTINATION = "@Iran_Alarm_News"
FOOTER = "@Iran_Alarm_News"

# فقط همین چهار کانال؛ هیچ منبع دیگری اضافه نمی‌شود.
SOURCES = [
    "@newscenter",
    "@Politicalnewschannell",
    "@km_ap",
    "@IranianMinds",
]

# فقط ۱۰ پیام آخر هر کانال بررسی می‌شود.
MAX_MESSAGES = 10

MAX_FINGERPRINTS = 20000
STATE_FILE = Path("data/state.json")


# =====================================================
# گزارش اجرا
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("IranAlarmNews")


# =====================================================
# فیلتر تبلیغات
# =====================================================

GENERAL_AD_KEYWORDS = [
    "تبلیغات", "تبلیغ ویژه", "تبلیغ رایگان",
    "اسپانسر", "کد تخفیف", "تخفیف ویژه",
    "فروش ویژه", "خرید کنید", "ثبت سفارش",
    "سفارش دهید", "برای خرید", "درآمد دلاری",
    "کسب درآمد", "همکاری در فروش",
    "زیرمجموعه گیری", "زیرمجموعه‌گیری",
    "فرصت محدود", "sponsored", "advertisement",
    "affiliate", "promo code", "referral"
]

GAMBLING_KEYWORDS = [
    "شرط بندی", "شرط‌بندی", "سایت پیش بینی",
    "سایت پیش‌بینی", "کازینو", "قمار",
    "برد تضمینی", "casino", "betting"
]

VPN_TERMS = [
    "vpn", "وی پی ان", "فیلترشکن", "فیلتر شکن",
    "فیلتر‌شکن", "پروکسی", "proxy", "v2ray",
    "vless", "vmess", "reality", "کانفیگ",
    "openvpn", "wireguard", "amnezia", "outline"
]

VPN_PROMOTION_TERMS = [
    "خرید", "فروش", "اشتراک", "دانلود", "دریافت",
    "سرور فعال", "سرور اختصاصی", "سرور پرسرعت",
    "سرور رایگان", "تست رایگان", "کانفیگ رایگان",
    "کانفیگ جدید", "کانفیگ اختصاصی", "اکانت ویژه",
    "تمدید اشتراک", "تمدید سرویس", "لینک کانفیگ",
    "پشتیبانی", "سفارش", "اتصال", "رایگان دریافت",
    "پرسرعت و پایدار", "بدون قطعی",
    "بدون محدودیت", "خرید اکانت", "فروش اکانت",
    "عضویت در کانال", "برای دریافت"
]

INVITE_PATTERNS = [
    r"t\.me/\+[a-zA-Z0-9_-]+",
    r"t\.me/joinchat/[a-zA-Z0-9_-]+"
]

URL_PATTERN = re.compile(
    r"https?://\S+|www\.\S+|t\.me/\S+",
    re.IGNORECASE
)

# حذف آیدی کانال‌ها و نام‌های کاربری مبدأ از متن
USERNAME_PATTERN = re.compile(
    r"(?<![\w])@[A-Za-z0-9_]{4,}"
)

PROMOTIONAL_PATTERNS = [
    r"برای اطلاعات بیشتر",
    r"جهت ثبت.?نام",
    r"همین حالا خرید",
    r"لینک خرید",
    r"برای سفارش",
    r"عضو کانال .* شوید",
    r"به کانال .* بپیوندید",
    r"جهت دریافت",
    r"برای دریافت",
]


def normalize(text):
    text = (text or "").lower()
    text = text.replace("ي", "ی")
    text = text.replace("ك", "ک")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def contains_any(text, words):
    return any(normalize(word) in text for word in words)


def is_advertisement(text):
    text = normalize(text)

    if not text:
        return False, ""

    if contains_any(text, GENERAL_AD_KEYWORDS):
        return True, "تبلیغات عمومی"

    if contains_any(text, GAMBLING_KEYWORDS):
        return True, "قمار و شرط‌بندی"

    for pattern in INVITE_PATTERNS:
        if re.search(pattern, text):
            return True, "لینک دعوت"

    for pattern in PROMOTIONAL_PATTERNS:
        if re.search(pattern, text):
            return True, "دعوت تبلیغاتی"

    has_vpn = contains_any(text, VPN_TERMS)
    has_vpn_promotion = contains_any(
        text, VPN_PROMOTION_TERMS
    )

    if has_vpn and has_vpn_promotion:
        return True, "تبلیغات VPN و پروکسی"

    return False, ""


# =====================================================
# مدیریت وضعیت
# =====================================================

def load_state():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not STATE_FILE.exists():
        return {
            "last_ids": {},
            "fingerprints": []
        }

    try:
        state = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        state.setdefault("last_ids", {})
        state.setdefault("fingerprints", [])

        return state

    except Exception:
        log.exception("خطا در خواندن فایل وضعیت")

        return {
            "last_ids": {},
            "fingerprints": []
        }


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    temp_file = STATE_FILE.with_suffix(".tmp")

    temp_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    temp_file.replace(STATE_FILE)


# =====================================================
# تشخیص پیام تکراری
# =====================================================

def make_fingerprint(message):
    text = normalize(message.message or "")
    text = URL_PATTERN.sub("", text)

    media_type = (
        type(message.media).__name__
        if message.media else "text"
    )

    if text:
        content = text
    else:
        media_id = ""

        if message.photo:
            media_id = str(message.photo.id)
        elif message.document:
            media_id = str(message.document.id)

        content = f"{media_type}|{media_id}|{message.id}"

    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def is_duplicate(message, state):
    return make_fingerprint(message) in state["fingerprints"]


def remember_message(message, state):
    fingerprint = make_fingerprint(message)

    if fingerprint not in state["fingerprints"]:
        state["fingerprints"].append(fingerprint)

    state["fingerprints"] = (
        state["fingerprints"][-MAX_FINGERPRINTS:]
    )


# =====================================================
# آماده‌سازی متن
# =====================================================

def prepare_text(text):
    text = text or ""

    # حذف لینک‌ها و آیدی‌های کانال‌های مبدأ
    text = URL_PATTERN.sub("", text)
    text = USERNAME_PATTERN.sub("", text)

    # حذف خطوط خالی اضافی
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if text:
        text += "\n\n"

    text += FOOTER

    return text


# =====================================================
# انتشار پیام تکی
# خروجی True یعنی پیام پردازش شده است.
# خروجی False یعنی انتشار ناموفق بوده است.
# =====================================================

async def publish_single(client, destination, message, state):

    original_text = message.message or ""

    rejected, reason = is_advertisement(original_text)

    if rejected:
        log.info(
            "پیام %s به دلیل %s رد شد",
            message.id, reason
        )
        return True

    if is_duplicate(message, state):
        log.info("پیام تکراری رد شد: %s", message.id)
        return True

    final_text = prepare_text(original_text)

    try:
        if message.media:

            with tempfile.TemporaryDirectory() as temp_dir:

                media_path = await client.download_media(
                    message,
                    file=temp_dir
                )

                if not media_path:
                    log.warning(
                        "دانلود رسانه پیام %s ناموفق بود",
                        message.id
                    )
                    return False

                await client.send_file(
                    destination,
                    media_path,
                    caption=final_text
                )

        elif final_text:
            await client.send_message(
                destination,
                final_text,
                link_preview=False
            )

        else:
            return True

        remember_message(message, state)

        log.info("پیام %s منتشر شد", message.id)
        return True

    except FloodWaitError:
        raise

    except Exception:
        log.exception("خطا در انتشار پیام %s", message.id)
        return False


# =====================================================
# انتشار آلبوم
# =====================================================

async def publish_album(client, destination, album, state):

    # اگر یکی از اجزای آلبوم تبلیغاتی باشد،
    # کل آلبوم رد می‌شود.

    for message in album:
        rejected, reason = is_advertisement(
            message.message or ""
        )

        if rejected:
            log.info("کل آلبوم رد شد: %s", reason)
            return True

    if any(is_duplicate(message, state) for message in album):
        log.info("آلبوم تکراری رد شد")
        return True

    try:
        with tempfile.TemporaryDirectory() as temp_dir:

            media_paths = []
            captions = []

            for index, message in enumerate(album):

                media_path = await client.download_media(
                    message,
                    file=temp_dir
                )

                if not media_path:
                    log.warning("دانلود بخشی از آلبوم ناموفق بود")
                    return False

                media_paths.append(media_path)

                original_caption = message.message or ""

                if index == 0:
                    captions.append(
                        prepare_text(original_caption)
                    )
                elif original_caption:
                    caption = URL_PATTERN.sub(
                        "", original_caption
                    )
                    caption = USERNAME_PATTERN.sub("", caption)
                    captions.append(caption.strip())
                else:
                    captions.append(None)

            if not media_paths:
                return True

            await client.send_file(
                destination,
                media_paths,
                caption=captions
            )

        for message in album:
            remember_message(message, state)

        log.info("آلبوم با %s فایل منتشر شد", len(media_paths))
        return True

    except FloodWaitError:
        raise

    except Exception:
        log.exception("خطا در انتشار آلبوم")
        return False


# =====================================================
# بررسی هر کانال
# =====================================================

async def process_source(client, source, destination, state):

    source_key = source.lower()

    log.info("در حال بررسی کانال: %s", source)

    entity = await client.get_entity(source)

    # فقط ۱۰ پیام آخر کانال دریافت می‌شود.
    latest_messages = []

    async for message in client.iter_messages(
        entity,
        limit=MAX_MESSAGES
    ):
        if message and not message.action:
            latest_messages.append(message)

    if not latest_messages:
        log.info("کانال خالی است: %s", source)
        return

    # مرتب‌سازی از قدیمی به جدید
    latest_messages.sort(key=lambda item: item.id)

    # اولین اجرای این کانال:
    # آخرین شناسه ثبت می‌شود و هیچ پیام قدیمی منتشر نمی‌شود.
    if source_key not in state["last_ids"]:
        latest_id = max(item.id for item in latest_messages)

        state["last_ids"][source_key] = latest_id
        save_state(state)

        log.info(
            "نقطه شروع کانال %s ثبت شد؛ پیام قدیمی منتشر نمی‌شود. آخرین شناسه: %s",
            source, latest_id
        )
        return

    last_id = int(state["last_ids"].get(source_key, 0))

    # فقط پیام‌های جدیدتر از آخرین پیام پردازش‌شده
    new_messages = [
        message
        for message in latest_messages
        if message.id > last_id
    ]

    if not new_messages:
        log.info("خبر جدیدی در %s نیست", source)
        return

    log.info(
        "%s پیام جدید در %s پیدا شد",
        len(new_messages), source
    )

    # گروه‌بندی آلبوم‌ها و پیام‌های تکی
    units = []
    grouped = {}

    for message in new_messages:

        if message.grouped_id:
            grouped.setdefault(message.grouped_id, []).append(message)
        else:
            units.append(([message], message.id))

    for album in grouped.values():
        album.sort(key=lambda item: item.id)
        units.append((album, max(item.id for item in album)))

    # مرتب‌سازی واحدها بر اساس شناسه پیام
    units.sort(key=lambda unit: min(item.id for item in unit[0]))

    for items, unit_last_id in units:

        if len(items) > 1 or items[0].grouped_id:
            success = await publish_album(
                client, destination, items, state
            )
        else:
            success = await publish_single(
                client, destination, items[0], state
            )

        # اگر انتشار ناموفق بود، شناسه جلو نمی‌رود
        # تا در اجرای بعدی دوباره بررسی شود.
        if not success:
            log.warning(
                "پردازش کانال %s متوقف شد؛ پیام ناموفق در اجرای بعد دوباره بررسی می‌شود",
                source
            )
            break

        state["last_ids"][source_key] = unit_last_id
        save_state(state)


# =====================================================
# اجرای اصلی
# =====================================================

async def main():

    state = load_state()

    client = TelegramClient(
        StringSession(TG_SESSION),
        API_ID,
        API_HASH
    )

    log.info("اتصال به تلگرام...")

    await client.start()

    try:
        destination = await client.get_entity(DESTINATION)

        log.info("اتصال موفق؛ شروع بررسی کانال‌ها")

        for source in SOURCES:

            try:
                await process_source(
                    client,
                    source,
                    destination,
                    state
                )

            except FloodWaitError as error:
                log.warning(
                    "تلگرام درخواست انتظار %s ثانیه‌ای داد",
                    error.seconds
                )
                await asyncio.sleep(error.seconds)

            except (RPCError, ValueError):
                log.exception("خطا در منبع: %s", source)

            except Exception:
                log.exception(
                    "خطای پیش‌بینی‌نشده در منبع: %s",
                    source
                )

    finally:
        save_state(state)
        await client.disconnect()
        log.info("پایان اجرای برنامه")


if __name__ == "__main__":
    asyncio.run(main())
