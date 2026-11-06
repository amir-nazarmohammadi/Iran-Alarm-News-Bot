import os
import re
import copy
import json
import asyncio
import hashlib
import logging
import tempfile
import base64
import urllib.parse
import urllib.request
import urllib.error

from datetime import datetime, timedelta, timezone
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import InputPeerChannel
from telethon.utils import get_input_peer


# =====================================================
# تنظیمات اصلی
# =====================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TG_SESSION = os.environ["TG_SESSION"]

# توکن ربات مدیریت؛ فقط برای ارسال هشدار خطا به Owner استفاده می‌شود.
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")

OWNER_ID = 96635648

DESTINATION_DEFAULT = "@Iran_Alarm_News"
FOOTER = "@Iran_Alarm_News"

# منابع پیش‌فرض؛ فقط وقتی استفاده می‌شوند که config.json منبعی نداشته باشد.
DEFAULT_SOURCES = [
    "@newscenter",
    "@Politicalnewschannell",
    "@km_ap",
    "@IranianMinds",
]

MAX_SOURCES = 30
MAX_MESSAGES = 10
MAX_FINGERPRINTS = 20000

# بعد از این تعداد تلاش ناموفق، پیام رد می‌شود تا منبع برای همیشه گیر نکند.
MAX_ATTEMPTS = 4

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4000
MAX_MEDIA_BYTES = 50 * 1024 * 1024

HEARTBEAT_MINUTES = 30
ALERT_COOLDOWN_HOURS = 6

# ایران: UTC+3:30 (ساعت تابستانی ندارد)
TEHRAN = timezone(timedelta(hours=3, minutes=30))

STATE_FILE = Path("data/state.json")
STATS_FILE = Path("data/stats.json")
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
# ابزارهای کمکی
# =====================================================

def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def parse_iso(value):
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def normalize(text):
    text = (text or "").lower()
    text = text.replace("ي", "ی")
    text = text.replace("ك", "ک")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


SOURCE_RE = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$")


def clean_sources(items):

    result = []
    seen = set()

    for item in items or []:

        source = str(item).strip()

        if not source.startswith("@"):
            source = "@" + source

        if not SOURCE_RE.match(source):
            continue

        key = source.lower()

        if key in seen:
            continue

        seen.add(key)
        result.append(source)

    return result[:MAX_SOURCES]


def clean_words(items):

    result = []
    seen = set()

    for item in items or []:

        word = str(item).strip()
        key = normalize(word)

        if len(key) < 2 or len(key) > 60 or key in seen:
            continue

        seen.add(key)
        result.append(word)

    return result[:300]


# =====================================================
# تنظیمات پیش‌فرض
# =====================================================

DEFAULT_CONFIG = {
    "destination": DESTINATION_DEFAULT,
    "paused": False,
    "sources": DEFAULT_SOURCES,
    "enabled_sources": DEFAULT_SOURCES,
    "admins": [],
    "block_words": [],
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

    config = copy.deepcopy(DEFAULT_CONFIG)

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

    # منابع از پنل مدیریت قابل تغییر هستند.
    sources = clean_sources(config.get("sources"))

    if not sources:
        sources = list(DEFAULT_SOURCES)

    enabled_raw = config.get("enabled_sources")

    if enabled_raw is None:
        enabled = {source.lower() for source in sources}
    else:
        enabled = {
            ("@" + str(item).strip().lstrip("@")).lower()
            for item in enabled_raw
        }

    config["sources"] = sources

    config["enabled_sources"] = [
        source for source in sources
        if source.lower() in enabled
    ]

    # مقصد انتشار قابل تغییر نیست.
    # برای جلوگیری از تغییر ناخواسته مقصد، آن را ثابت نگه می‌داریم.
    config["destination"] = DESTINATION_DEFAULT

    config["block_words"] = clean_words(config.get("block_words"))

    filters = copy.deepcopy(DEFAULT_CONFIG["filters"])
    filters.update(config.get("filters") or {})
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


def contains_any(text, words):
    return any(normalize(word) in text for word in words)


def is_advertisement(text, filters, block_words=()):
    """نتیجه: (رد شود؟، علت، جزئیات)"""

    text = normalize(text)

    if not text:
        return False, "", ""

    # کلمات ممنوعه‌ای که از پنل اضافه شده‌اند
    for word in block_words:

        key = normalize(word)

        if key and key in text:
            return True, "کلمه ممنوعه", word

    if filters.get("ads", True):

        if contains_any(text, GENERAL_AD_KEYWORDS):
            return True, "تبلیغات عمومی", ""

        for pattern in PROMOTIONAL_PATTERNS:
            if re.search(pattern, text):
                return True, "دعوت تبلیغاتی", ""

        for pattern in INVITE_PATTERNS:
            if re.search(pattern, text):
                return True, "لینک دعوت", ""

    if filters.get("gambling", True):

        if contains_any(text, GAMBLING_KEYWORDS):
            return True, "قمار و شرط‌بندی", ""

    if filters.get("vpn_promotions", True):

        has_vpn = contains_any(text, VPN_TERMS)

        has_vpn_promotion = contains_any(
            text,
            VPN_PROMOTION_TERMS
        )

        if has_vpn and has_vpn_promotion:
            return True, "تبلیغات VPN و پروکسی", ""

    return False, "", ""


# =====================================================
# مدیریت وضعیت
# =====================================================

def new_state():
    return {
        "last_ids": {},
        "fingerprints": [],
        "entities": {},
        "attempts": {},
        "alerts": {},
        "paused": False,
    }


def load_state():

    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    if not STATE_FILE.exists():
        return new_state()

    try:

        state = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        for key, value in new_state().items():
            state.setdefault(key, value)

        return state

    except Exception:

        log.exception("خطا در خواندن فایل وضعیت")

        return new_state()


def write_json_atomic(path, data, indent=2):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = path.with_suffix(".tmp")

    temp_file.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=indent,
            sort_keys=(path == STATS_FILE)
        ),
        encoding="utf-8"
    )

    temp_file.replace(path)


def save_state(state):
    write_json_atomic(STATE_FILE, state)


# =====================================================
# آمار و گزارش (برای پنل مدیریت)
# =====================================================

STATS_KEEP_DAYS = 14
STATS_KEEP_REJECTED = 30
STATS_KEEP_ERRORS = 20


class Stats:

    def __init__(self):
        self.data = self._load()

    @staticmethod
    def _default():
        return {
            "version": 1,
            "last_run": None,
            "last_publish": None,
            "days": {},
            "recent_rejected": [],
            "errors": [],
        }

    def _load(self):

        data = self._default()

        if STATS_FILE.exists():
            try:
                loaded = json.loads(
                    STATS_FILE.read_text(encoding="utf-8")
                )
                if isinstance(loaded, dict):
                    data.update(loaded)
            except Exception:
                log.exception("خطا در خواندن فایل آمار")

        return data

    # --- ابزارهای داخلی ---

    def _day(self):

        key = datetime.now(TEHRAN).strftime("%Y-%m-%d")

        day = self.data["days"].setdefault(key, {
            "published": 0,
            "rejected": 0,
            "duplicates": 0,
            "failed": 0,
            "sources": {},
            "reasons": {},
        })

        return day

    def _source(self, source):

        return self._day()["sources"].setdefault(source, {
            "published": 0,
            "rejected": 0,
            "duplicates": 0,
            "failed": 0,
        })

    def _touch(self):
        self.data["last_run"] = iso(utcnow())

    # --- ثبت رویدادها ---

    def heartbeat(self):

        last = parse_iso(self.data.get("last_run"))

        if (
            last is None
            or utcnow() - last >= timedelta(minutes=HEARTBEAT_MINUTES)
        ):
            self._touch()

    def published(self, source):

        self._touch()
        self.data["last_publish"] = self.data["last_run"]

        self._day()["published"] += 1
        self._source(source)["published"] += 1

    def rejected(self, source, reason, detail, text):

        self._touch()

        day = self._day()
        day["rejected"] += 1
        day["reasons"][reason] = day["reasons"].get(reason, 0) + 1
        self._source(source)["rejected"] += 1

        snippet = re.sub(r"\s+", " ", text or "").strip()[:100]

        self.data["recent_rejected"].append({
            "t": self.data["last_run"],
            "source": source,
            "reason": reason,
            "detail": detail,
            "snippet": snippet,
        })

        self.data["recent_rejected"] = (
            self.data["recent_rejected"][-STATS_KEEP_REJECTED:]
        )

    def duplicate(self, source):

        self._touch()
        self._day()["duplicates"] += 1
        self._source(source)["duplicates"] += 1

    def failed(self, source):

        self._touch()
        self._day()["failed"] += 1
        self._source(source)["failed"] += 1

    def error(self, scope, message):

        message = str(message)[:200]
        errors = self.data["errors"]

        # خطای تکراریِ یکسان در ۶ ساعت اخیر دوباره ثبت نمی‌شود.
        if errors:
            last = errors[-1]
            last_time = parse_iso(last.get("t"))

            if (
                last.get("scope") == scope
                and last.get("msg") == message
                and last_time
                and utcnow() - last_time < timedelta(hours=6)
            ):
                return

        self._touch()

        errors.append({
            "t": self.data["last_run"],
            "scope": scope,
            "msg": message,
        })

        self.data["errors"] = errors[-STATS_KEEP_ERRORS:]

    def save(self):

        days = self.data["days"]

        for key in sorted(days)[:-STATS_KEEP_DAYS]:
            del days[key]

        write_json_atomic(STATS_FILE, self.data, indent=1)


# =====================================================
# هشدار خطا برای Owner
# =====================================================

def send_owner_message(text):

    if not ADMIN_BOT_TOKEN:
        log.warning("ADMIN_BOT_TOKEN تنظیم نشده؛ هشدار ارسال نمی‌شود.")
        return False

    body = urllib.parse.urlencode({
        "chat_id": OWNER_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage",
        data=body,
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
        return True

    except Exception as error:
        # عمداً جزئیات خطا چاپ نمی‌شود تا توکن در لاگ نیاید.
        log.warning("ارسال هشدار به Owner ناموفق بود: %s", type(error).__name__)
        return False


def esc(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def alert(state, key, text, cooldown_hours=ALERT_COOLDOWN_HOURS):

    alerts = state.setdefault("alerts", {})
    now = utcnow()

    # هشدارهای قدیمی پاک می‌شوند.
    for old_key in list(alerts):
        old = parse_iso(alerts[old_key])
        if old is None or now - old > timedelta(days=3):
            del alerts[old_key]

    last = parse_iso(alerts.get(key))

    if last and now - last < timedelta(hours=cooldown_hours):
        return

    if await asyncio.to_thread(send_owner_message, text):
        alerts[key] = iso(now)


LAST_ERROR = {"text": ""}


def remember_error(error):

    if isinstance(error, BaseException):
        LAST_ERROR["text"] = f"{type(error).__name__}: {error}"[:200]
    else:
        LAST_ERROR["text"] = str(error)[:200]


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

def clean_text(text):

    text = text or ""

    text = URL_PATTERN.sub("", text)
    text = USERNAME_PATTERN.sub("", text)

    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def prepare_text(text):

    text = clean_text(text)

    if text:
        text += "\n\n"

    text += FOOTER

    return text


def split_text(text, limit=TEXT_LIMIT):

    if len(text) <= limit:
        return [text]

    parts = []
    current = ""

    for line in text.split("\n"):

        while len(line) > limit:

            if current:
                parts.append(current)
                current = ""

            parts.append(line[:limit])
            line = line[limit:]

        candidate = f"{current}\n{line}" if current else line

        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate

    if current:
        parts.append(current)

    return parts


def has_media(message):
    """فقط عکس و فایل قابل دانلود؛ پیش‌نمایش لینک، نظرسنجی و ... متن حساب می‌شوند."""

    if not (message.photo or message.document):
        return False

    size = getattr(getattr(message, "file", None), "size", None)

    if size and size > MAX_MEDIA_BYTES:
        return False

    return True


class Runtime:

    def __init__(self, client, destination, state, stats, filters, block_words):
        self.client = client
        self.destination = destination
        self.state = state
        self.stats = stats
        self.filters = filters
        self.block_words = block_words


async def send_long_text(rt, text):

    for part in split_text(text):

        await rt.client.send_message(
            rt.destination,
            part,
            link_preview=False
        )


# =====================================================
# انتشار پیام تکی
# =====================================================

async def publish_single(rt, source, message):

    original_text = message.message or ""

    rejected, reason, detail = is_advertisement(
        original_text,
        rt.filters,
        rt.block_words
    )

    if rejected:

        log.info(
            "پیام %s رد شد: %s",
            message.id,
            reason
        )

        rt.stats.rejected(source, reason, detail, original_text)

        return True

    if is_duplicate(message, rt.state):

        log.info(
            "پیام تکراری رد شد: %s",
            message.id
        )

        rt.stats.duplicate(source)

        return True

    final_text = prepare_text(original_text)
    with_media = has_media(message)

    if not with_media and not clean_text(original_text):

        log.info(
            "پیام %s متن یا رسانه‌ی قابل انتشار ندارد",
            message.id
        )

        return True

    try:

        if with_media:

            with tempfile.TemporaryDirectory() as temp_dir:

                media_path = await rt.client.download_media(
                    message,
                    file=temp_dir
                )

                if not media_path:

                    log.warning(
                        "دانلود رسانه پیام %s ناموفق بود",
                        message.id
                    )

                    remember_error("دانلود رسانه ناموفق بود")

                    return False

                if len(final_text) <= CAPTION_LIMIT:

                    await rt.client.send_file(
                        rt.destination,
                        media_path,
                        caption=final_text
                    )

                else:
                    # کپشن‌های طولانی از حد تلگرام بیشترند؛ متن جدا ارسال می‌شود.
                    await rt.client.send_file(
                        rt.destination,
                        media_path
                    )

                    await send_long_text(rt, final_text)

        else:

            await send_long_text(rt, final_text)

        remember_message(message, rt.state)

        rt.stats.published(source)

        log.info(
            "پیام %s منتشر شد",
            message.id
        )

        return True

    except FloodWaitError:
        raise

    except Exception as error:

        remember_error(error)

        log.exception(
            "خطا در انتشار پیام %s",
            message.id
        )

        return False


# =====================================================
# انتشار آلبوم
# =====================================================

async def publish_album(rt, source, album):

    # اگر یکی از اعضای آلبوم تبلیغاتی باشد،
    # کل آلبوم رد می‌شود.

    for message in album:

        rejected, reason, detail = is_advertisement(
            message.message or "",
            rt.filters,
            rt.block_words
        )

        if rejected:

            log.info(
                "کل آلبوم رد شد: %s",
                reason
            )

            rt.stats.rejected(
                source,
                reason,
                detail,
                message.message or ""
            )

            return True

    if any(
        is_duplicate(message, rt.state)
        for message in album
    ):

        log.info("آلبوم تکراری رد شد")

        rt.stats.duplicate(source)

        return True

    members = [message for message in album if has_media(message)]

    if not members:
        return True

    # کپشن آلبوم: اولین کپشنی که در بین اعضا وجود دارد
    caption_source = next(
        (message.message for message in album if message.message),
        ""
    )

    final_caption = prepare_text(caption_source)

    try:

        with tempfile.TemporaryDirectory() as temp_dir:

            media_paths = []

            for message in members:

                media_path = await rt.client.download_media(
                    message,
                    file=temp_dir
                )

                if not media_path:

                    log.warning(
                        "دانلود بخشی از آلبوم ناموفق بود"
                    )

                    remember_error("دانلود بخشی از آلبوم ناموفق بود")

                    return False

                media_paths.append(media_path)

            if len(final_caption) <= CAPTION_LIMIT:

                await rt.client.send_file(
                    rt.destination,
                    media_paths,
                    caption=[final_caption] + [None] * (len(media_paths) - 1)
                )

            else:

                await rt.client.send_file(
                    rt.destination,
                    media_paths
                )

                await send_long_text(rt, final_caption)

        for message in album:
            remember_message(message, rt.state)

        rt.stats.published(source)

        log.info(
            "آلبوم با %s فایل منتشر شد",
            len(media_paths)
        )

        return True

    except FloodWaitError:
        raise

    except Exception as error:

        remember_error(error)

        log.exception("خطا در انتشار آلبوم")

        return False


# =====================================================
# پیدا کردن کانال منبع (با ذخیره‌ی شناسه برای جلوگیری از FloodWait)
# =====================================================

async def resolve_source(rt, source, use_cache=True):

    key = source.lower()
    cached = rt.state["entities"].get(key) if use_cache else None

    if cached:
        return InputPeerChannel(
            channel_id=cached["id"],
            access_hash=cached["hash"]
        )

    entity = await rt.client.get_entity(source)

    peer = get_input_peer(entity)

    if isinstance(peer, InputPeerChannel):
        rt.state["entities"][key] = {
            "id": peer.channel_id,
            "hash": peer.access_hash,
        }

    return entity


async def read_latest(rt, source):

    key = source.lower()
    from_cache = key in rt.state["entities"]

    for attempt in (1, 2):

        try:

            entity = await resolve_source(
                rt,
                source,
                use_cache=(attempt == 1)
            )

            messages = []

            async for message in rt.client.iter_messages(
                entity,
                limit=MAX_MESSAGES
            ):

                if message and not message.action:
                    messages.append(message)

            return messages

        except FloodWaitError:
            raise

        except (RPCError, ValueError):

            # شناسه‌ی ذخیره‌شده ممکن است قدیمی شده باشد؛ یک بار از نو پیدا می‌شود.
            if attempt == 1 and from_cache:
                rt.state["entities"].pop(key, None)
                continue

            raise


# =====================================================
# بررسی هر کانال
# =====================================================

async def process_source(rt, source):

    state = rt.state
    source_key = source.lower()

    log.info(
        "در حال بررسی کانال: %s",
        source
    )

    latest_messages = await read_latest(rt, source)

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

        attempt_key = f"{source_key}:{unit_last_id}"
        attempts = int(state["attempts"].get(attempt_key, 0))

        # پیامی که چند بار پشت‌سرهم شکست خورده، رد می‌شود تا منبع گیر نکند.
        if attempts >= MAX_ATTEMPTS:

            reason = LAST_ERROR["text"] or "نامشخص"

            log.error(
                "پیام %s از %s بعد از %s تلاش رد شد: %s",
                unit_last_id,
                source,
                attempts,
                reason
            )

            rt.stats.failed(source)
            rt.stats.error(source, f"پیام {unit_last_id} رد شد: {reason}")

            await alert(
                state,
                f"skip:{attempt_key}",
                "⚠️ <b>پیامی منتشر نشد</b>\n\n"
                f"📡 منبع: {esc(source)}\n"
                f"🔢 شناسه پیام: {unit_last_id}\n"
                f"🧾 علت: <code>{esc(reason)}</code>\n\n"
                f"بعد از {attempts} تلاش ناموفق این پیام رد شد "
                "و بررسی منبع ادامه پیدا کرد.",
                cooldown_hours=24
            )

            state["attempts"].pop(attempt_key, None)
            state["last_ids"][source_key] = unit_last_id
            save_state(state)

            continue

        # قبل از تلاش ثبت می‌شود تا اگر برنامه وسط کار متوقف شد هم شمرده شود.
        state["attempts"][attempt_key] = attempts + 1
        save_state(state)

        try:

            if len(items) > 1 or items[0].grouped_id:

                success = await publish_album(rt, source, items)

            else:

                success = await publish_single(rt, source, items[0])

        except FloodWaitError:

            # محدودیت تلگرام شکست پیام نیست.
            state["attempts"][attempt_key] = attempts
            raise

        if not success:

            log.warning(
                "پردازش %s متوقف شد؛ پیام ناموفق دوباره بررسی می‌شود.",
                source
            )

            break

        state["attempts"].pop(attempt_key, None)
        state["last_ids"][source_key] = unit_last_id

        save_state(state)
        rt.stats.save()


# =====================================================
# اجرای اصلی
# =====================================================

async def report_source_error(rt, source, error):

    message = f"{type(error).__name__}: {error}"[:200]

    rt.stats.error(source, message)

    await alert(
        rt.state,
        f"source:{source.lower()}",
        "⚠️ <b>خطا در یک منبع خبری</b>\n\n"
        f"📡 منبع: {esc(source)}\n"
        f"🧾 خطا: <code>{esc(message)}</code>\n\n"
        "اگر کانال حذف یا خصوصی شده، از پنل حذفش کن.",
        cooldown_hours=12
    )


async def main():

    stats = Stats()
    state = load_state()

    stats.heartbeat()

    # تنظیمات در هر اجرای برنامه تازه دریافت می‌شوند.
    config = load_config()

    if config.get("paused", False):

        log.info(
            "انتشار توسط پنل مدیریت متوقف شده است."
        )

        state["paused"] = True

        save_state(state)
        stats.save()

        return

    if state.get("paused"):

        # بعد از ادامه‌ی انتشار، اخبار زمان توقف منتشر نمی‌شوند.
        state["paused"] = False
        state["last_ids"] = {}

        log.info(
            "انتشار ادامه پیدا کرد؛ از همین لحظه شروع می‌شود."
        )

    enabled_sources = config["enabled_sources"]

    destination_name = config["destination"]
    filters = config["filters"]
    block_words = config["block_words"]

    # منبعی که خاموش یا حذف شده فراموش می‌شود؛
    # در صورت روشن شدن دوباره، اخبار قدیمی منتشر نمی‌شود.
    enabled_keys = {source.lower() for source in enabled_sources}

    for key in list(state["last_ids"]):
        if key not in enabled_keys:
            del state["last_ids"][key]

    known_keys = {source.lower() for source in config["sources"]}

    for key in list(state["entities"]):
        if key not in known_keys:
            del state["entities"][key]

    if not enabled_sources:

        log.info(
            "هیچ منبع خبری فعالی وجود ندارد."
        )

        save_state(state)
        stats.save()

        return

    client = TelegramClient(
        StringSession(TG_SESSION),
        API_ID,
        API_HASH
    )

    log.info("اتصال به تلگرام...")

    authorized = False

    try:
        await client.connect()
        authorized = await client.is_user_authorized()

    except Exception:
        log.exception("اتصال به تلگرام ناموفق بود")

    if not authorized:

        log.error("سشن تلگرام معتبر نیست یا اتصال برقرار نشد.")

        stats.error("system", "سشن تلگرام نامعتبر است یا اتصال برقرار نشد")

        await alert(
            state,
            "session",
            "🚨 <b>هشدار ایران آلارم</b>\n\n"
            "اتصال ناشر به تلگرام برقرار نشد و انتشار انجام نمی‌شود.\n"
            "احتمالاً سشن (TG_SESSION) منقضی یا باطل شده؛ "
            "یک سشن جدید بساز و در Secrets گیت‌هاب جایگزین کن."
        )

        save_state(state)
        stats.save()

        try:
            await client.disconnect()
        except Exception:
            pass

        return

    try:

        try:
            destination = await client.get_entity(
                destination_name
            )

        except Exception as error:

            log.exception("پیدا کردن کانال مقصد ناموفق بود")

            stats.error("system", f"کانال مقصد: {type(error).__name__}")

            await alert(
                state,
                "destination",
                "🚨 <b>هشدار ایران آلارم</b>\n\n"
                f"کانال مقصد ({esc(destination_name)}) پیدا نشد "
                "یا حساب ناشر به آن دسترسی ندارد.\n"
                f"🧾 <code>{esc(type(error).__name__)}</code>"
            )

            return

        rt = Runtime(
            client,
            destination,
            state,
            stats,
            filters,
            block_words
        )

        log.info(
            "اتصال موفق؛ شروع بررسی منابع"
        )

        for source in enabled_sources:

            try:

                await process_source(rt, source)

            except FloodWaitError as error:

                log.warning(
                    "تلگرام درخواست انتظار %s ثانیه‌ای داد",
                    error.seconds
                )

                if error.seconds <= 60:

                    await asyncio.sleep(
                        error.seconds + 1
                    )

                else:

                    stats.error(
                        "system",
                        f"محدودیت تلگرام (FloodWait) {error.seconds} ثانیه"
                    )

                    await alert(
                        state,
                        "flood",
                        "⏳ <b>محدودیت موقت تلگرام</b>\n\n"
                        f"تلگرام درخواست انتظار {error.seconds} ثانیه‌ای داد؛ "
                        "این دور بررسی متوقف شد و دور بعد دوباره تلاش می‌شود.",
                        cooldown_hours=2
                    )

                    break

            except (RPCError, ValueError) as error:

                log.exception(
                    "خطا در منبع: %s",
                    source
                )

                await report_source_error(rt, source, error)

            except Exception as error:

                log.exception(
                    "خطای پیش‌بینی‌نشده در منبع: %s",
                    source
                )

                await report_source_error(rt, source, error)

    finally:

        save_state(state)
        stats.save()

        await client.disconnect()

        log.info(
            "پایان اجرای برنامه"
        )


if __name__ == "__main__":
    asyncio.run(main())
