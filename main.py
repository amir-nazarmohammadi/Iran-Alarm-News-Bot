
import os
import re
import json
import asyncio
import hashlib
import logging
import tempfile
import base64
import urllib.request
import urllib.error

from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError


# =====================================================
# تنظیمات اصلی و امنیت منابع
# =====================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TG_SESSION = os.environ["TG_SESSION"]

OWNER_ID = 96635648

DESTINATION_DEFAULT = "@Iran_Alarm_News"
FOOTER = "@Iran_Alarm_News"

# منابع مجاز؛ پنل نمی‌تواند منبع جدیدی خارج از این لیست اضافه کند.
ALLOWED_SOURCES = [
    "@newscenter",
    "@Politicalnewschannell",
    "@km_ap",
    "@IranianMinds",
]

MAX_MESSAGES = 10
MAX_FINGERPRINTS = 20000

STATE_FILE = Path("data/state.json")
CONFIG_FILE = Path("config.json")

REPO = os.getenv("GITHUB_REPOSITORY", "")
GH_TOKEN = os.getenv("GH_TOKEN", "")
CONFIG_BRANCH = os.getenv("CONFIG_BRANCH", "main")
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json")


# =====================================================
# گزارش اجرا
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("IranAlarmNews")


# =====================================================
# تنظیمات پیش‌فرض
# =====================================================

DEFAULT_CONFIG = {
    "destination": DESTINATION_DEFAULT,
    "paused": False,
    "sources": ALLOWED_SOURCES,
    "enabled_sources": ALLOWED_SOURCES,
    "admins": [],
    "filters": {
        "ads": True,
        "gambling": True,
        "vpn_promotions": True,
    }
}


# =====================================================
# دریافت تنظیمات جدید از GitHub
# =====================================================

def load_config():

    config = DEFAULT_CONFIG.copy()

    try:
        # دریافت آخرین نسخه config.json از GitHub
        if REPO and GH_TOKEN:

            url = (
                f"https://api.github.com/repos/{REPO}"
                f"/contents/{CONFIG_PATH}?ref={CONFIG_BRANCH}"
            )

            request = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {GH_TOKEN}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
            )

            with urllib.request.urlopen(
                request,
                timeout=20
            ) as response:

                result = json.loads(response.read().decode("utf-8"))

            raw = base64.b64decode(
                result["content"]
            ).decode("utf-8")

            remote_config = json.loads(raw)

        else:
            # حالت جایگزین: خواندن فایل محلی
            if CONFIG_FILE.exists():
                remote_config = json.loads(
                    CONFIG_FILE.read_text(encoding="utf-8")
                )
            else:
                remote_config = {}

        config.update(remote_config)

        log.info("تنظیمات از GitHub دریافت شد.")

    except Exception:
        log.exception(
            "دریافت تنظیمات ناموفق بود؛ تنظیمات پیش‌فرض/محلی استفاده می‌شود."
        )

        if CONFIG_FILE.exists():
            try:
                local_config = json.loads(
                    CONFIG_FILE.read_text(encoding="utf-8")
                )
                config.update(local_config)
            except Exception:
                log.exception("خواندن تنظیمات محلی هم ناموفق بود.")

    # منابع مجاز را محدود می‌کنیم.
    config["sources"] = ALLOWED_SOURCES

    enabled = set(
        config.get("enabled_sources", ALLOWED_SOURCES)
    )

    config["enabled_sources"] = [
        source for source in ALLOWED_SOURCES
        if source in enabled
    ]

    # مقصد انتشار قابل تغییر نیست.
    # برای جلوگیری از تغییر ناخواسته مقصد، آن را ثابت نگه می‌داریم.
    config["destination"] = DESTINATION_DEFAULT

    filters = DEFAULT_CONFIG["filters"].copy()
    filters.update(config.get("filters", {}))
    config["filters"] = filters

    return config


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


def is_advertisement(text, filters):

    text = normalize(text)

    if not text:
        return False, ""

    if filters.get("ads", True):

        if contains_any(text, GENERAL_AD_KEYWORDS):
            return True, "تبلیغات عمومی"

        for pattern in PROMOTIONAL_PATTERNS:
            if re.search(pattern, text):
                return True, "دعوت تبلیغاتی"

        for pattern in INVITE_PATTERNS:
            if re.search(pattern, text):
                return True, "لینک دعوت"

    if filters.get("gambling", True):

        if contains_any(text, GAMBLING_KEYWORDS):
            return True, "قمار و شرط‌بندی"

    if filters.get("vpn_promotions", True):

        has_vpn = contains_any(text, VPN_TERMS)

        has_vpn_promotion = contains_any(
            text,
            VPN_PROMOTION_TERMS
        )

        if has_vpn and has_vpn_promotion:
            return True, "تبلیغات VPN و پروکسی"

    return False, ""


# =====================================================
# مدیریت وضعیت
# =====================================================

def load_state():

    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

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

    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = STATE_FILE.with_suffix(".tmp")

    temp_file.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2
        ),
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

    return (
        make_fingerprint(message)
        in state["fingerprints"]
    )


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

    text = URL_PATTERN.sub("", text)
    text = USERNAME_PATTERN.sub("", text)

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if text:
        text += "\n\n"

    text += FOOTER

    return text


# =====================================================
# انتشار پیام تکی
# =====================================================

async def publish_single(
    client,
    destination,
    message,
    state,
    filters
):

    original_text = message.message or ""

    rejected, reason = is_advertisement(
        original_text,
        filters
    )

    if rejected:

        log.info(
            "پیام %s رد شد: %s",
            message.id,
            reason
        )

        return True

    if is_duplicate(message, state):

        log.info(
            "پیام تکراری رد شد: %s",
            message.id
        )

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

        log.info(
            "پیام %s منتشر شد",
            message.id
        )

        return True

    except FloodWaitError:
        raise

    except Exception:

        log.exception(
            "خطا در انتشار پیام %s",
            message.id
        )

        return False


# =====================================================
# انتشار آلبوم
# =====================================================

async def publish_album(
    client,
    destination,
    album,
    state,
    filters
):

    # اگر یکی از اعضای آلبوم تبلیغاتی باشد،
    # کل آلبوم رد می‌شود.

    for message in album:

        rejected, reason = is_advertisement(
            message.message or "",
            filters
        )

        if rejected:

            log.info(
                "کل آلبوم رد شد: %s",
                reason
            )

            return True

    if any(
        is_duplicate(message, state)
        for message in album
    ):

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

                    log.warning(
                        "دانلود بخشی از آلبوم ناموفق بود"
                    )

                    return False

                media_paths.append(media_path)

                original_caption = message.message or ""

                if index == 0:

                    captions.append(
                        prepare_text(original_caption)
                    )

                elif original_caption:

                    caption = URL_PATTERN.sub(
                        "",
                        original_caption
                    )

                    caption = USERNAME_PATTERN.sub(
                        "",
                        caption
                    )

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

        log.info(
            "آلبوم با %s فایل منتشر شد",
            len(media_paths)
        )

        return True

    except FloodWaitError:
        raise

    except Exception:

        log.exception("خطا در انتشار آلبوم")
        return False


# =====================================================
# بررسی هر کانال
# =====================================================

async def process_source(
    client,
    source,
    destination,
    state,
    filters
):

    source_key = source.lower()

    log.info(
        "در حال بررسی کانال: %s",
        source
    )

    entity = await client.get_entity(source)

    latest_messages = []

    async for message in client.iter_messages(
        entity,
        limit=MAX_MESSAGES
    ):

        if message and not message.action:
            latest_messages.append(message)

    if not latest_messages:

        log.info(
            "کانال خالی است: %s",
            source
        )

        return

    latest_messages.sort(
        key=lambda item: item.id
    )

    # اولین اجرا: ثبت نقطه شروع بدون انتشار اخبار قدیمی
    if source_key not in state["last_ids"]:

        latest_id = max(
            item.id for item in latest_messages
        )

        state["last_ids"][source_key] = latest_id

        save_state(state)

        log.info(
            "نقطه شروع %s ثبت شد؛ پیام قدیمی منتشر نمی‌شود. ID=%s",
            source,
            latest_id
        )

        return

    last_id = int(
        state["last_ids"].get(source_key, 0)
    )

    new_messages = [
        message
        for message in latest_messages
        if message.id > last_id
    ]

    if not new_messages:

        log.info(
            "خبر جدیدی در %s نیست",
            source
        )

        return

    log.info(
        "%s پیام جدید در %s پیدا شد",
        len(new_messages),
        source
    )

    units = []
    grouped = {}

    for message in new_messages:

        if message.grouped_id:

            grouped.setdefault(
                message.grouped_id,
                []
            ).append(message)

        else:

            units.append(
                ([message], message.id)
            )

    for album in grouped.values():

        album.sort(
            key=lambda item: item.id
        )

        units.append(
            (album, max(item.id for item in album))
        )

    units.sort(
        key=lambda unit: min(
            item.id for item in unit[0]
        )
    )

    for items, unit_last_id in units:

        if len(items) > 1 or items[0].grouped_id:

            success = await publish_album(
                client,
                destination,
                items,
                state,
                filters
            )

        else:

            success = await publish_single(
                client,
                destination,
                items[0],
                state,
                filters
            )

        if not success:

            log.warning(
                "پردازش %s متوقف شد؛ پیام ناموفق دوباره بررسی می‌شود.",
                source
            )

            break

        state["last_ids"][source_key] = unit_last_id

        save_state(state)


# =====================================================
# اجرای اصلی
# =====================================================

async def main():

    # تنظیمات در هر اجرای برنامه تازه دریافت می‌شوند.
    config = load_config()

    if config.get("paused", False):

        log.info(
            "انتشار توسط پنل مدیریت متوقف شده است."
        )

        return

    enabled_sources = config["enabled_sources"]

    destination_name = config["destination"]
    filters = config["filters"]

    if not enabled_sources:

        log.info(
            "هیچ منبع خبری فعالی وجود ندارد."
        )

        return

    state = load_state()

    client = TelegramClient(
        StringSession(TG_SESSION),
        API_ID,
        API_HASH
    )

    log.info("اتصال به تلگرام...")

    await client.start()

    try:

        destination = await client.get_entity(
            destination_name
        )

        log.info(
            "اتصال موفق؛ شروع بررسی منابع"
        )

        for source in enabled_sources:

            try:

                await process_source(
                    client,
                    source,
                    destination,
                    state,
                    filters
                )

            except FloodWaitError as error:

                log.warning(
                    "تلگرام درخواست انتظار %s ثانیه‌ای داد",
                    error.seconds
                )

                await asyncio.sleep(
                    error.seconds
                )

            except (RPCError, ValueError):

                log.exception(
                    "خطا در منبع: %s",
                    source
                )

            except Exception:

                log.exception(
                    "خطای پیش‌بینی‌نشده در منبع: %s",
                    source
                )

    finally:

        save_state(state)

        await client.disconnect()

        log.info(
            "پایان اجرای برنامه"
        )


if __name__ == "__main__":
    asyncio.run(main())
