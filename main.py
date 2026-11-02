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

DESTINATION = os.getenv(
    "DESTINATION_CHANNEL",
    "@Iran_Alarm_News"
)

SOURCES = [
    channel.strip()
    for channel in os.getenv(
        "SOURCE_CHANNELS",
        "@newscenter,@Politicalnewschannell,@km_ap,@IranianMinds"
    ).split(",")
    if channel.strip()
]

FOOTER = "@Iran_Alarm_News"

STATE_FILE = Path("data/state.json")

# حداکثر تعداد پیام برای خواندن در هر منبع در هر اجرا
# اگر پیام‌های جدید بیشتر باشند، در اجراهای بعدی ادامه می‌دهد.
MAX_MESSAGES = 1000

# حداکثر تعداد اثرانگشت ذخیره‌شده
MAX_FINGERPRINTS = 20000


# =====================================================
# گزارش اجرای برنامه
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
    "تبلیغات",
    "تبلیغ ویژه",
    "تبلیغ رایگان",
    "اسپانسر",
    "کد تخفیف",
    "تخفیف ویژه",
    "فروش ویژه",
    "خرید کنید",
    "ثبت سفارش",
    "سفارش دهید",
    "برای خرید",
    "درآمد دلاری",
    "کسب درآمد",
    "همکاری در فروش",
    "زیرمجموعه گیری",
    "زیرمجموعه‌گیری",
    "فرصت محدود",
    "sponsored",
    "advertisement",
    "affiliate",
    "promo code",
    "referral"
]


GAMBLING_KEYWORDS = [
    "شرط بندی",
    "شرط‌بندی",
    "سایت پیش بینی",
    "سایت پیش‌بینی",
    "کازینو",
    "قمار",
    "برد تضمینی",
    "casino",
    "betting"
]


VPN_TERMS = [
    "vpn",
    "وی پی ان",
    "فیلترشکن",
    "فیلتر شکن",
    "فیلتر‌شکن",
    "پروکسی",
    "proxy",
    "v2ray",
    "vless",
    "vmess",
    "reality",
    "کانفیگ",
    "کانفیگ",
    "openvpn",
    "wireguard",
    "amnezia",
    "outline"
]


VPN_PROMOTION_TERMS = [
    "خرید",
    "فروش",
    "اشتراک",
    "دانلود",
    "دریافت",
    "سرور فعال",
    "سرور اختصاصی",
    "سرور پرسرعت",
    "سرور رایگان",
    "تست رایگان",
    "کانفیگ رایگان",
    "کانفیگ جدید",
    "کانفیگ اختصاصی",
    "اکانت ویژه",
    "تمدید اشتراک",
    "تمدید سرویس",
    "لینک کانفیگ",
    "پشتیبانی",
    "سفارش",
    "اتصال",
    "رایگان دریافت",
    "پرسرعت و پایدار",
    "بدون قطعی",
    "بدون محدودیت",
    "خرید اکانت",
    "فروش اکانت",
    "عضویت در کانال",
    "برای دریافت"
]


INVITE_PATTERNS = [
    r"t\.me/\+[a-zA-Z0-9_-]+",
    r"t\.me/joinchat/[a-zA-Z0-9_-]+"
]


URL_PATTERN = re.compile(
    r"https?://\S+|www\.\S+|t\.me/\S+",
    re.IGNORECASE
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
    return any(
        normalize(word) in text
        for word in words
    )


def is_advertisement(text):
    """
    تشخیص تبلیغات عمومی، قمار و تبلیغات VPN.

    اشاره خبری به VPN به تنهایی باعث حذف خبر نمی‌شود.
    """

    text = normalize(text)

    if not text:
        return False, ""

    # تبلیغات عمومی
    if contains_any(text, GENERAL_AD_KEYWORDS):
        return True, "تبلیغات عمومی"

    # قمار و شرط‌بندی
    if contains_any(text, GAMBLING_KEYWORDS):
        return True, "قمار و شرط‌بندی"

    # لینک دعوت
    for pattern in INVITE_PATTERNS:
        if re.search(pattern, text):
            return True, "لینک دعوت"

    # دعوت به خرید و ثبت‌نام
    for pattern in PROMOTIONAL_PATTERNS:
        if re.search(pattern, text):
            return True, "دعوت تبلیغاتی"

    # تبلیغات VPN و پروکسی
    has_vpn = contains_any(text, VPN_TERMS)

    has_vpn_promotion = contains_any(
        text,
        VPN_PROMOTION_TERMS
    )

    if has_vpn and has_vpn_promotion:
        return True, "تبلیغات VPN و پروکسی"

    return False, ""


# =====================================================
# مدیریت وضعیت و جلوگیری از انتشار تکراری
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
            STATE_FILE.read_text(
                encoding="utf-8"
            )
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


def make_fingerprint(message):

    text = normalize(
        message.message or ""
    )

    # حذف لینک‌ها از متن برای تشخیص خبرهای یکسان
    text = URL_PATTERN.sub("", text)

    # تشخیص نوع رسانه
    media_type = (
        type(message.media).__name__
        if message.media
        else "text"
    )

    # اگر متن وجود داشته باشد، متن مبنای تشخیص است.
    if text:
        content = text

    else:
        # رسانه بدون کپشن:
        # شناسه رسانه در صورت وجود استفاده می‌شود.
        media_id = ""

        if message.photo:
            media_id = str(message.photo.id)

        elif message.document:
            media_id = str(message.document.id)

        content = (
            f"{media_type}|{media_id}|{message.id}"
        )

    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def is_duplicate(message, state):

    fingerprint = make_fingerprint(message)

    return (
        fingerprint in state["fingerprints"]
    )


def remember_message(message, state):

    fingerprint = make_fingerprint(message)

    state["fingerprints"].append(
        fingerprint
    )

    state["fingerprints"] = (
        state["fingerprints"][-MAX_FINGERPRINTS:]
    )


# =====================================================
# آماده‌سازی متن خبر
# =====================================================

def prepare_text(text):

    text = text or ""

    # حذف لینک‌های موجود در متن
    text = URL_PATTERN.sub("", text)

    # حذف خطوط خالی اضافی
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

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
    state
):

    original_text = message.message or ""

    # فیلتر تبلیغات
    rejected, reason = is_advertisement(
        original_text
    )

    if rejected:

        log.info(
            "پیام %s رد شد: %s",
            message.id,
            reason
        )

        return

    # جلوگیری از تکرار
    if is_duplicate(message, state):

        log.info(
            "پیام تکراری رد شد: %s",
            message.id
        )

        return

    final_text = prepare_text(
        original_text
    )

    try:

        # عکس، ویدئو و فایل
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

                    return

                await client.send_file(
                    destination,
                    media_path,
                    caption=final_text
                )

        # پیام متنی
        elif final_text:

            await client.send_message(
                destination,
                final_text,
                link_preview=False
            )

        else:
            return

        remember_message(
            message,
            state
        )

        log.info(
            "پیام %s منتشر شد",
            message.id
        )

    except FloodWaitError:
        raise

    except Exception:

        log.exception(
            "خطا در انتشار پیام %s",
            message.id
        )

        return


# =====================================================
# انتشار آلبوم
# =====================================================

async def publish_album(
    client,
    destination,
    album,
    state
):

    # اگر حتی یک کپشن تبلیغاتی باشد،
    # کل آلبوم رد می‌شود.

    for message in album:

        rejected, reason = is_advertisement(
            message.message or ""
        )

        if rejected:

            log.info(
                "کل آلبوم رد شد: %s",
                reason
            )

            return

    # جلوگیری از انتشار آلبوم تکراری
    if any(
        is_duplicate(message, state)
        for message in album
    ):

        log.info(
            "آلبوم تکراری رد شد"
        )

        return

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
                    continue

                media_paths.append(
                    media_path
                )

                original_caption = (
                    message.message or ""
                )

                if index == 0:
                    captions.append(
                        prepare_text(original_caption)
                    )

                elif original_caption:
                    captions.append(
                        URL_PATTERN.sub(
                            "",
                            original_caption
                        ).strip()
                    )

                else:
                    captions.append(None)

            if not media_paths:
                return

            await client.send_file(
                destination,
                media_paths,
                caption=captions
            )

        for message in album:
            remember_message(
                message,
                state
            )

        log.info(
            "آلبوم با %s فایل منتشر شد",
            len(media_paths)
        )

    except FloodWaitError:
        raise

    except Exception:

        log.exception(
            "خطا در انتشار آلبوم"
        )


# =====================================================
# بررسی هر کانال خبری
# =====================================================

async def process_source(
    client,
    source,
    destination,
    state
):

    source_key = source.lower()

    last_id = int(
        state["last_ids"].get(
            source_key,
            0
        )
    )

    log.info(
        "در حال بررسی: %s",
        source
    )

    entity = await client.get_entity(
        source
    )

    messages = []

    async for message in client.iter_messages(
        entity,
        min_id=last_id,
        reverse=True,
        limit=MAX_MESSAGES
    ):

        if message and not message.action:
            messages.append(message)

    if not messages:

        log.info(
            "خبر جدیدی در %s وجود ندارد",
            source
        )

        return

    log.info(
        "%s پیام جدید از %s دریافت شد",
        len(messages),
        source
    )

    index = 0

    while index < len(messages):

        message = messages[index]

        # تشخیص آلبوم
        if message.grouped_id:

            group_id = message.grouped_id

            album = []

            while (
                index < len(messages)
                and messages[index].grouped_id == group_id
            ):

                album.append(
                    messages[index]
                )

                index += 1

            await publish_album(
                client,
                destination,
                album,
                state
            )

            # ذخیره وضعیت بعد از پردازش آلبوم
            state["last_ids"][source_key] = max(
                item.id for item in album
            )

            save_state(state)

        else:

            await publish_single(
                client,
                destination,
                message,
                state
            )

            state["last_ids"][source_key] = message.id

            save_state(state)

            index += 1


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

    log.info(
        "اتصال به تلگرام..."
    )

    await client.start()

    try:

        destination = await client.get_entity(
            DESTINATION
        )

        log.info(
            "اتصال موفق؛ شروع بررسی منابع خبری"
        )

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
