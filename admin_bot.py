import os
import re
import copy
import json
import time
import base64
import asyncio
import hashlib
import html
import logging
import requests

from datetime import datetime, timedelta, timezone

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters as tg_filters,
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


# =====================================================
# تنظیمات
# =====================================================

OWNER_ID = 96635648
BOT_TOKEN = os.environ["ADMIN_BOT_TOKEN"]
GH_TOKEN = os.environ["GH_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]

BRANCH = os.getenv("CONFIG_BRANCH", "main")
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json")
STATS_PATH = "data/stats.json"

DESTINATION = "@Iran_Alarm_News"

MAX_SOURCES = 30
MAX_WORDS = 300
WORDS_PER_PAGE = 10

# ایران: UTC+3:30 (ساعت تابستانی ندارد)
TEHRAN = timezone(timedelta(hours=3, minutes=30))

HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

DEFAULT_SOURCES = [
    "@newscenter",
    "@Politicalnewschannell",
    "@km_ap",
    "@IranianMinds",
]

DEFAULT_CONFIG = {
    "destination": DESTINATION,
    "paused": False,
    "sources": DEFAULT_SOURCES,
    "enabled_sources": DEFAULT_SOURCES,
    "admins": [],
    "block_words": [],
    "filters": {
        "ads": True,
        "gambling": True,
        "vpn_promotions": True,
    },
    "updated_at": None,
    "updated_by": None,
}

FILTERS = [
    ("ads", "تبلیغات", "کد تخفیف، فروش ویژه، لینک دعوت و ..."),
    ("gambling", "قمار و شرط‌بندی", "کازینو، سایت پیش‌بینی و ..."),
    ("vpn_promotions", "تبلیغات VPN و پروکسی", "فروش و تبلیغ کانفیگ و فیلترشکن"),
]


# =====================================================
# ابزارهای نمایش (ارقام فارسی، تاریخ شمسی، زمان)
# =====================================================

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

JALALI_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]


def fa(value):
    return str(value).translate(FA_DIGITS)


def esc(text):
    return html.escape(str(text), quote=False)


def normalize(text):
    text = (text or "").lower()
    text = text.replace("ي", "ی")
    text = text.replace("ك", "ک")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def to_jalali(gy, gm, gd):

    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]

    gy2 = gy + 1 if gm > 2 else gy

    days = (
        355666 + (365 * gy)
        + ((gy2 + 3) // 4)
        - ((gy2 + 99) // 100)
        + ((gy2 + 399) // 400)
        + gd + g_d_m[gm - 1]
    )

    jy = -1595 + (33 * (days // 12053))
    days %= 12053

    jy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365

    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30

    return jy, jm, jd


def jalali_label(date):
    _, month, day = to_jalali(date.year, date.month, date.day)
    return f"{fa(day)} {JALALI_MONTHS[month - 1]}"


def parse_iso(value):
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def now_utc():
    return datetime.now(timezone.utc)


def today_date():
    return datetime.now(TEHRAN).date()


def clock(value):
    dt = parse_iso(value)
    return fa(dt.astimezone(TEHRAN).strftime("%H:%M")) if dt else "—"


def when(value):
    """زمان کامل: امروز/دیروز یا تاریخ شمسی + ساعت"""

    dt = parse_iso(value)

    if not dt:
        return "—"

    local = dt.astimezone(TEHRAN)
    days_ago = (today_date() - local.date()).days

    if days_ago == 0:
        label = "امروز"
    elif days_ago == 1:
        label = "دیروز"
    else:
        label = jalali_label(local.date())

    return f"{label} {fa(local.strftime('%H:%M'))}"


def ago(value):

    dt = parse_iso(value)

    if not dt:
        return "—"

    seconds = int((now_utc() - dt).total_seconds())

    if seconds < 90:
        return "چند لحظه پیش"

    minutes = seconds // 60

    if minutes < 60:
        return f"{fa(minutes)} دقیقه پیش"

    hours = minutes // 60

    if hours < 48:
        return f"{fa(hours)} ساعت پیش"

    return f"{fa(hours // 24)} روز پیش"


# =====================================================
# ارتباط با GitHub (config.json و آمار)
# =====================================================

def contents_url(path):
    return f"https://api.github.com/repos/{REPO}/contents/{path}"


def github_read(path):

    response = requests.get(
        contents_url(path),
        headers=HEADERS,
        params={"ref": BRANCH},
        timeout=20,
    )

    if response.status_code == 404:
        return None, None

    response.raise_for_status()

    result = response.json()

    content = base64.b64decode(result["content"]).decode("utf-8")

    return json.loads(content), result["sha"]


def load_config():

    remote, sha = github_read(CONFIG_PATH)

    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(remote or {})

    filters = copy.deepcopy(DEFAULT_CONFIG["filters"])
    filters.update(config.get("filters") or {})
    config["filters"] = filters

    if not isinstance(config.get("sources"), list):
        config["sources"] = list(DEFAULT_SOURCES)

    if not isinstance(config.get("enabled_sources"), list):
        config["enabled_sources"] = list(config["sources"])

    if not isinstance(config.get("block_words"), list):
        config["block_words"] = []

    if not isinstance(config.get("admins"), list):
        config["admins"] = []

    return config, sha


def save_config(config, sha, user_id, message):

    config["updated_at"] = now_utc().isoformat()
    config["updated_by"] = int(user_id)

    content = json.dumps(
        config,
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    payload = {
        "message": message,
        "content": base64.b64encode(
            content.encode("utf-8")
        ).decode("ascii"),
        "branch": BRANCH,
    }

    if sha:
        payload["sha"] = sha

    response = requests.put(
        contents_url(CONFIG_PATH),
        headers=HEADERS,
        json=payload,
        timeout=20,
    )

    response.raise_for_status()


def update_config(user_id, message, mutator):
    """
    تنظیمات تازه را می‌خواند، mutator را روی آن اجرا می‌کند و ذخیره می‌کند.
    mutator باید (تغییر_کرد؟، نتیجه) برگرداند.
    اگر هم‌زمان کس دیگری فایل را عوض کرده باشد، دوباره تلاش می‌شود.
    """

    for attempt in range(4):

        config, sha = load_config()

        changed, result = mutator(config)

        if not changed:
            return config, result

        try:
            save_config(config, sha, user_id, message)
            return config, result

        except requests.HTTPError as error:

            status = error.response.status_code if error.response is not None else 0

            if status in (409, 422) and attempt < 3:
                time.sleep(0.7)
                continue

            raise

    raise RuntimeError("ذخیره‌ی تنظیمات ممکن نشد")


def load_stats():

    try:
        stats, _ = github_read(STATS_PATH)
    except Exception:
        log.exception("Stats loading failed")
        return {}

    return stats if isinstance(stats, dict) else {}


def check_channel(username):
    """بررسی وجود کانال عمومی با Bot API؛ (عنوان، خطا) برمی‌گرداند."""

    try:
        response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getChat",
            params={"chat_id": username},
            timeout=15,
        )

        data = response.json()

    except Exception as error:
        log.warning("Channel check failed: %s", type(error).__name__)
        return None, "ارتباط با تلگرام برقرار نشد؛ دوباره تلاش کن"

    if not data.get("ok"):
        return None, "کانال پیدا نشد (باید عمومی باشد)"

    chat = data.get("result") or {}

    if chat.get("type") != "channel":
        return None, "این یک کانال نیست"

    return chat.get("title") or username, None


async def aio(function, *args):
    return await asyncio.to_thread(function, *args)


# =====================================================
# دسترسی‌ها
# =====================================================

def is_owner(user_id):
    return int(user_id) == OWNER_ID


def is_admin(user_id, config):
    admins = [int(x) for x in config.get("admins", [])]
    return is_owner(user_id) or int(user_id) in admins


# =====================================================
# دکمه‌ها (رنگی: primary آبی، success سبز، danger قرمز)
# =====================================================

def btn(text, data, style=None):

    if style:
        return InlineKeyboardButton(
            text,
            callback_data=data,
            api_kwargs={"style": style},
        )

    return InlineKeyboardButton(text, callback_data=data)


def keyboard(*rows):
    return InlineKeyboardMarkup([list(row) for row in rows])


def home_button():
    return btn("🏠 صفحه اصلی", "home")


LINE = "━━━━━━━━━━━━━━"


# =====================================================
# ورودی‌ها: تشخیص منبع، کلمه و آیدی
# =====================================================

SOURCE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")

RESERVED_PATHS = {"joinchat", "addlist", "c", "share", "iv", "proxy", "socks"}


def parse_source(text):
    """@name، name، t.me/name و لینک‌های کانال را به @name تبدیل می‌کند."""

    value = (text or "").strip()

    value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(www\.)?(t|telegram)\.me/", "", value, flags=re.IGNORECASE)
    value = value.lstrip("@")

    if value.lower().startswith("s/"):
        value = value[2:]

    value = re.split(r"[/?#\s]", value)[0]

    if value.lower() in RESERVED_PATHS:
        return None

    if not SOURCE_RE.match(value):
        return None

    return "@" + value


def parse_words(text):

    words = []
    seen = set()
    rejected = []

    for part in re.split(r"[\n\r,،;؛]+", text or ""):

        word = part.strip()

        if not word:
            continue

        key = normalize(word)

        if len(key) < 2 or len(key) > 60:
            rejected.append(word)
            continue

        if key in seen:
            continue

        seen.add(key)
        words.append(word)

    return words, rejected


def word_hash(word):
    return hashlib.sha1(normalize(word).encode("utf-8")).hexdigest()[:8]


def source_key(source):
    return source.lower().lstrip("@")


def find_source(config, key):

    for source in config["sources"]:
        if source_key(source) == key:
            return source

    return None


def enabled_set(config):
    return {source_key(item) for item in config["enabled_sources"]}


# =====================================================
# صفحه‌ها (متن + دکمه)
# =====================================================

def today_stats(stats):
    return stats.get("days", {}).get(today_date().strftime("%Y-%m-%d"), {})


def last_days(stats, count):

    days = stats.get("days", {})
    result = []

    for offset in range(count):
        date = today_date() - timedelta(days=offset)
        result.append((date, days.get(date.strftime("%Y-%m-%d"), {})))

    return result


def recent_errors(stats, hours=24):

    limit = now_utc() - timedelta(hours=hours)
    result = []

    for item in stats.get("errors", []):
        moment = parse_iso(item.get("t"))
        if moment and moment >= limit:
            result.append(item)

    return result


def view_home(config, stats, user_id):

    day = today_stats(stats)
    paused = config["paused"]

    last_run = stats.get("last_run")
    last_dt = parse_iso(last_run)
    stale = bool(last_dt and now_utc() - last_dt > timedelta(minutes=100))

    errors = recent_errors(stats)

    lines = [
        "🛰 <b>پنل مدیریت ایران آلارم</b>",
        LINE,
        "⏸ انتشار: <b>متوقف</b>" if paused else "🟢 انتشار: <b>فعال</b>",
        f"📡 منابع فعال: <b>{fa(len(config['enabled_sources']))}</b>"
        f" از {fa(len(config['sources']))}",
        f"🚫 کلمات ممنوعه: <b>{fa(len(config['block_words']))}</b>",
        "",
        f"📊 امروز:  ✅ <b>{fa(day.get('published', 0))}</b> منتشر"
        f"  ·  🚫 <b>{fa(day.get('rejected', 0))}</b> رد",
        f"⏱ آخرین فعالیت ناشر: {ago(last_run)}",
    ]

    if errors:
        lines.append(f"⚠️ خطاهای ۲۴ ساعت اخیر: <b>{fa(len(errors))}</b>")

    if stale:
        lines += [
            "",
            "⚠️ ناشر مدتی است فعالیتی ثبت نکرده؛ "
            "بخش Actions در گیت‌هاب را بررسی کن.",
        ]

    lines += [
        "",
        "👑 دسترسی: Owner" if is_owner(user_id) else "🛡 دسترسی: Admin",
    ]

    rows = [
        [
            btn("▶️ ادامه انتشار", "pause", "success")
            if paused else
            btn("⏸ توقف انتشار", "pause", "danger"),
        ],
        [
            btn(f"📡 منابع خبری ({fa(len(config['sources']))})", "src", "primary"),
            btn("🧹 فیلترها", "flt"),
        ],
        [
            btn(f"🚫 کلمات ممنوعه ({fa(len(config['block_words']))})", "wd"),
            btn("📊 آمار و گزارش", "st", "primary"),
        ],
    ]

    last_row = [btn("🔄 بروزرسانی", "refresh")]

    if is_owner(user_id):
        last_row.append(btn("👥 ادمین‌ها", "adm"))

    rows.append(last_row)

    return "\n".join(lines), keyboard(*rows)


def view_sources(config, notice=None):

    enabled = enabled_set(config)

    on = sum(1 for source in config["sources"] if source_key(source) in enabled)
    off = len(config["sources"]) - on

    lines = []

    if notice:
        lines += [notice, "", LINE, ""]

    lines += [
        "📡 <b>منابع خبری</b>",
        LINE,
        "روی نام منبع بزن تا روشن/خاموش شود؛ 🗑 برای حذف.",
        "",
        f"✅ فعال: <b>{fa(on)}</b>   ⚪️ خاموش: <b>{fa(off)}</b>"
        f"   (حداکثر {fa(MAX_SOURCES)} منبع)",
    ]

    rows = []

    for source in config["sources"]:

        is_on = source_key(source) in enabled
        key = source_key(source)

        rows.append([
            btn(
                f"{'✅' if is_on else '⚪️'} {source}",
                f"s:t:{key}",
                "success" if is_on else None,
            ),
            btn("🗑", f"s:d:{key}", "danger"),
        ])

    rows.append([btn("➕ افزودن منبع جدید", "s:add", "primary")])
    rows.append([btn("🔙 بازگشت", "home")])

    return "\n".join(lines), keyboard(*rows)


def view_source_delete(source):

    text = (
        "🗑 <b>حذف منبع</b>\n"
        f"{LINE}\n"
        f"مطمئنی که {esc(source)} حذف شود؟"
    )

    return text, keyboard(
        [
            btn("✅ بله، حذف کن", f"s:y:{source_key(source)}", "danger"),
            btn("❌ انصراف", "src"),
        ]
    )


def view_filters(config):

    lines = [
        "🧹 <b>فیلترها</b>",
        LINE,
        "هر فیلتر را با دکمه روشن/خاموش کن:",
        "",
    ]

    rows = []

    for key, label, description in FILTERS:

        is_on = config["filters"].get(key, True)

        lines.append(f"• <b>{label}</b>: {description}")

        rows.append([
            btn(
                f"{'✅' if is_on else '❌'} {label}",
                f"f:t:{key}",
                "success" if is_on else "danger",
            )
        ])

    rows.append([btn("🔙 بازگشت", "home")])

    return "\n".join(lines), keyboard(*rows)


def view_words(config, notice=None):

    words = config["block_words"]

    lines = []

    if notice:
        lines += [notice, "", LINE, ""]

    lines += [
        "🚫 <b>کلمات ممنوعه</b>",
        LINE,
        "هر خبری که متنش شامل یکی از این کلمات باشد منتشر نمی‌شود.",
        "",
    ]

    if words:

        for index, word in enumerate(words[:40], 1):
            lines.append(f"{fa(index)}. {esc(word)}")

        if len(words) > 40:
            lines.append(f"… و {fa(len(words) - 40)} کلمه‌ی دیگر")

    else:
        lines.append("هنوز کلمه‌ای اضافه نشده.")

    rows = [[btn("➕ افزودن کلمه", "w:add", "primary")]]

    if words:
        rows[0].append(btn("🗑 حذف کلمه", "w:del:0", "danger"))

    rows.append([btn("🔙 بازگشت", "home")])

    return "\n".join(lines), keyboard(*rows)


def view_words_delete(config, page):

    words = config["block_words"]

    pages = max(1, (len(words) + WORDS_PER_PAGE - 1) // WORDS_PER_PAGE)
    page = max(0, min(page, pages - 1))

    chunk = words[page * WORDS_PER_PAGE:(page + 1) * WORDS_PER_PAGE]

    text = (
        "🗑 <b>حذف کلمه</b>\n"
        f"{LINE}\n"
        "روی کلمه بزن تا حذف شود.\n"
        f"صفحه {fa(page + 1)} از {fa(pages)}"
    )

    rows = []
    pair = []

    for word in chunk:

        pair.append(
            btn(f"🗑 {word[:18]}", f"w:d:{word_hash(word)}:{page}", "danger")
        )

        if len(pair) == 2:
            rows.append(pair)
            pair = []

    if pair:
        rows.append(pair)

    nav = []

    if page > 0:
        nav.append(btn("◀️ قبلی", f"w:del:{page - 1}"))

    if page < pages - 1:
        nav.append(btn("بعدی ▶️", f"w:del:{page + 1}"))

    if nav:
        rows.append(nav)

    rows.append([btn("✅ تمام", "wd")])

    return text, keyboard(*rows)


def view_admins(config):

    lines = [
        "👥 <b>مدیریت ادمین‌ها</b>",
        LINE,
        f"👑 Owner: <code>{OWNER_ID}</code>",
        "",
    ]

    rows = []

    admins = [int(x) for x in config["admins"]]

    if admins:

        lines.append("🛡 ادمین‌ها:")

        for admin_id in admins:

            lines.append(f"• <code>{admin_id}</code>")

            rows.append([
                btn(f"🗑 حذف {admin_id}", f"a:r:{admin_id}", "danger")
            ])

    else:
        lines.append("هنوز ادمینی اضافه نشده.")

    rows.append([btn("➕ افزودن ادمین", "a:add", "primary")])
    rows.append([btn("🔙 بازگشت", "home")])

    return "\n".join(lines), keyboard(*rows)


def view_prompt(title, body):

    text = f"{title}\n{LINE}\n{body}"

    return text, keyboard([btn("❌ انصراف", "cancel", "danger")])


# ---------- آمار ----------

NO_STATS = (
    "هنوز آماری ثبت نشده است.\n"
    "بعد از اولین اجرای نسخه‌ی جدید ناشر، اینجا پر می‌شود."
)


def stats_back():
    return keyboard([btn("🔙 آمار", "st"), home_button()])


def view_stats_menu(stats):

    text = f"📊 <b>آمار و گزارش</b>\n{LINE}\nیکی را انتخاب کن:"

    if not stats:
        text += f"\n\n{NO_STATS}"

    return text, keyboard(
        [btn("📅 امروز", "st:today", "primary"), btn("🗓 ۷ روز اخیر", "st:week", "primary")],
        [btn("📡 به تفکیک منبع", "st:src"), btn("🚫 ردشده‌ها", "st:rej")],
        [btn("⚠️ خطاها", "st:err")],
        [btn("🔙 بازگشت", "home")],
    )


def view_stats_today(stats):

    day = today_stats(stats)

    lines = [
        f"📅 <b>آمار امروز</b> ({jalali_label(today_date())})",
        LINE,
        f"✅ منتشرشده: <b>{fa(day.get('published', 0))}</b>",
        f"🚫 ردشده: <b>{fa(day.get('rejected', 0))}</b>",
        f"♻️ تکراری: <b>{fa(day.get('duplicates', 0))}</b>",
        f"❌ ناموفق: <b>{fa(day.get('failed', 0))}</b>",
    ]

    reasons = day.get("reasons", {})

    if reasons:

        lines += ["", "🏷 علت ردشدن‌ها:"]

        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            lines.append(f"• {esc(reason)}: {fa(count)}")

    lines += ["", f"⏱ آخرین انتشار: {when(stats.get('last_publish'))}"]

    if not stats:
        lines += ["", NO_STATS]

    return "\n".join(lines), stats_back()


def view_stats_week(stats):

    lines = ["🗓 <b>آمار ۷ روز اخیر</b>", LINE]

    total = [0, 0, 0]

    for index, (date, day) in enumerate(last_days(stats, 7)):

        published = day.get("published", 0)
        rejected = day.get("rejected", 0)
        duplicates = day.get("duplicates", 0)

        total[0] += published
        total[1] += rejected
        total[2] += duplicates

        label = "امروز" if index == 0 else (
            "دیروز" if index == 1 else jalali_label(date)
        )

        lines.append(
            f"📅 {label}: ✅ {fa(published)} · 🚫 {fa(rejected)} · ♻️ {fa(duplicates)}"
        )

    lines += [
        "",
        f"<b>جمع:</b> ✅ {fa(total[0])} · 🚫 {fa(total[1])} · ♻️ {fa(total[2])}",
    ]

    if not stats:
        lines += ["", NO_STATS]

    return "\n".join(lines), stats_back()


def view_stats_sources(stats, config):

    today = today_stats(stats).get("sources", {})

    week = {}

    for _, day in last_days(stats, 7):
        for source, values in day.get("sources", {}).items():
            bucket = week.setdefault(source, {"published": 0, "rejected": 0})
            bucket["published"] += values.get("published", 0)
            bucket["rejected"] += values.get("rejected", 0)

    names = list(config["sources"])

    for source in list(today) + list(week):
        if source not in names:
            names.append(source)

    lines = ["📡 <b>آمار به تفکیک منبع</b>", LINE]

    for source in names:

        t = today.get(source, {})
        w = week.get(source, {})

        lines += [
            f"📡 <b>{esc(source)}</b>",
            f"    امروز: ✅ {fa(t.get('published', 0))} · 🚫 {fa(t.get('rejected', 0))}"
            f"   |   ۷ روز: ✅ {fa(w.get('published', 0))} · 🚫 {fa(w.get('rejected', 0))}",
        ]

    if not stats:
        lines += ["", NO_STATS]

    return "\n".join(lines), stats_back()


def view_stats_rejected(stats):

    reasons = {}

    for _, day in last_days(stats, 7):
        for reason, count in day.get("reasons", {}).items():
            reasons[reason] = reasons.get(reason, 0) + count

    lines = ["🚫 <b>پیام‌های ردشده</b>", LINE]

    if reasons:

        lines.append("🏷 علت‌ها در ۷ روز اخیر:")

        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            lines.append(f"• {esc(reason)}: {fa(count)}")

        lines.append("")

    items = list(reversed(stats.get("recent_rejected", [])))[:8]

    if items:

        lines.append("آخرین‌ها:")

        for item in items:

            reason = item.get("reason", "")

            if item.get("detail"):
                reason += f" «{item['detail']}»"

            lines += [
                "",
                f"🕒 {when(item.get('t'))} · {esc(item.get('source', ''))}",
                f"🏷 {esc(reason)}",
                f"<i>{esc(item.get('snippet', ''))}</i>",
            ]

    else:
        lines.append("پیام ردشده‌ای ثبت نشده است.")

    return "\n".join(lines), stats_back()


def view_stats_errors(stats):

    items = list(reversed(stats.get("errors", [])))[:8]

    lines = ["⚠️ <b>آخرین خطاها</b>", LINE]

    if not items:
        lines.append("خطایی ثبت نشده است. 👌")

    for item in items:

        scope = item.get("scope", "")

        lines += [
            "",
            f"🕒 {when(item.get('t'))} · {esc('سیستم' if scope == 'system' else scope)}",
            f"<code>{esc(item.get('msg', ''))}</code>",
        ]

    return "\n".join(lines), stats_back()


# =====================================================
# اجرای دکمه‌ها
# =====================================================

DENIED = "⛔️ شما اجازه دسترسی به پنل را ندارید."


async def safe_edit(query, text, markup):

    try:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )

    except BadRequest as error:

        if "not modified" in str(error).lower():
            return

        raise


async def home_view(config, user_id):

    stats = await aio(load_stats)

    return view_home(config, stats, user_id)


async def route(data, user_id, config, context):
    """نتیجه: ((متن، دکمه‌ها)، پیام کوتاه بالای صفحه)"""

    parts = data.split(":")
    head = parts[0]

    if data in ("home", "refresh", "cancel"):

        return (
            await home_view(config, user_id),
            "🔄 بروزرسانی شد" if data == "refresh" else None,
        )

    if data == "pause":

        def mutator(cfg):
            cfg["paused"] = not cfg["paused"]
            return True, cfg["paused"]

        cfg, paused = await aio(update_config, user_id, "Toggle publishing status", mutator)

        return (
            await home_view(cfg, user_id),
            "⏸ انتشار متوقف شد" if paused else "▶️ انتشار ادامه پیدا کرد",
        )

    # ---------- منابع ----------

    if data == "src":
        return view_sources(config), None

    if head == "s" and len(parts) == 3 and parts[1] == "t":

        key = parts[2]

        def mutator(cfg):

            source = find_source(cfg, key)

            if not source:
                return False, None

            active = enabled_set(cfg)

            if key in active:
                active.discard(key)
            else:
                active.add(key)

            cfg["enabled_sources"] = [
                item for item in cfg["sources"]
                if source_key(item) in active
            ]

            return True, (source, key in active)

        cfg, result = await aio(update_config, user_id, f"Toggle source {key}", mutator)

        if not result:
            return view_sources(cfg), "منبع پیدا نشد"

        source, is_on = result

        return (
            view_sources(cfg),
            f"✅ {source} روشن شد" if is_on else f"⚪️ {source} خاموش شد",
        )

    if head == "s" and len(parts) == 3 and parts[1] == "d":

        source = find_source(config, parts[2])

        if not source:
            return view_sources(config), "منبع پیدا نشد"

        return view_source_delete(source), None

    if head == "s" and len(parts) == 3 and parts[1] == "y":

        key = parts[2]

        def mutator(cfg):

            source = find_source(cfg, key)

            if not source:
                return False, None

            cfg["sources"] = [s for s in cfg["sources"] if source_key(s) != key]
            cfg["enabled_sources"] = [
                s for s in cfg["enabled_sources"] if source_key(s) != key
            ]

            return True, source

        cfg, source = await aio(update_config, user_id, f"Remove source {key}", mutator)

        return view_sources(cfg), (f"🗑 {source} حذف شد" if source else "منبع پیدا نشد")

    if data == "s:add":

        context.user_data["await"] = "src_add"

        return view_prompt(
            "➕ <b>افزودن منبع جدید</b>",
            "آیدی یا لینک کانال <b>عمومی</b> را بفرست.\n"
            "چند کانال را هم می‌توانی با فاصله یا در چند خط بفرستی.\n\n"
            "مثال:\n"
            "<code>@newscenter</code>\n"
            "<code>https://t.me/newscenter</code>",
        ), None

    # ---------- فیلترها ----------

    if data == "flt":
        return view_filters(config), None

    if head == "f" and len(parts) == 3 and parts[1] == "t":

        key = parts[2]

        if key not in {item[0] for item in FILTERS}:
            return view_filters(config), "فیلتر نامعتبر است"

        def mutator(cfg):
            cfg["filters"][key] = not cfg["filters"].get(key, True)
            return True, cfg["filters"][key]

        cfg, is_on = await aio(update_config, user_id, f"Toggle filter {key}", mutator)

        label = next(item[1] for item in FILTERS if item[0] == key)

        return view_filters(cfg), (f"✅ {label} روشن شد" if is_on else f"❌ {label} خاموش شد")

    # ---------- کلمات ممنوعه ----------

    if data == "wd":
        return view_words(config), None

    if data == "w:add":

        context.user_data["await"] = "word_add"

        return view_prompt(
            "➕ <b>افزودن کلمه‌ی ممنوعه</b>",
            "کلمه یا عبارت را بفرست.\n"
            "برای چند کلمه، هر کدام را در یک خط بنویس یا با «،» جدا کن.\n\n"
            "هر خبری که شامل آن‌ها باشد منتشر نمی‌شود.",
        ), None

    if head == "w" and len(parts) == 3 and parts[1] == "del":

        page = int(parts[2]) if parts[2].isdigit() else 0

        return view_words_delete(config, page), None

    if head == "w" and len(parts) == 4 and parts[1] == "d":

        digest = parts[2]
        page = int(parts[3]) if parts[3].isdigit() else 0

        def mutator(cfg):

            for word in cfg["block_words"]:

                if word_hash(word) == digest:
                    cfg["block_words"] = [
                        w for w in cfg["block_words"] if w != word
                    ]
                    return True, word

            return False, None

        cfg, word = await aio(update_config, user_id, "Remove blocked word", mutator)

        if not cfg["block_words"]:
            return view_words(cfg), (f"🗑 «{word}» حذف شد" if word else None)

        return view_words_delete(cfg, page), (f"🗑 «{word}» حذف شد" if word else "کلمه پیدا نشد")

    # ---------- آمار ----------

    if head == "st":

        stats = await aio(load_stats)

        sub = parts[1] if len(parts) > 1 else "menu"

        if sub == "today":
            return view_stats_today(stats), None

        if sub == "week":
            return view_stats_week(stats), None

        if sub == "src":
            return view_stats_sources(stats, config), None

        if sub == "rej":
            return view_stats_rejected(stats), None

        if sub == "err":
            return view_stats_errors(stats), None

        return view_stats_menu(stats), None

    # ---------- ادمین‌ها (فقط Owner) ----------

    if head in ("adm", "a"):

        if not is_owner(user_id):
            return view_home(config, {}, user_id), "⛔️ فقط Owner اجازه دارد"

        if data == "adm":
            return view_admins(config), None

        if data == "a:add":

            context.user_data["await"] = "admin_add"

            return view_prompt(
                "➕ <b>افزودن ادمین</b>",
                "آیدی عددی کاربر را بفرست، یا یک پیام از او را برای من فوروارد کن.\n\n"
                "اگر آیدی را نمی‌دانی، از او بخواه ربات را /start کند؛ "
                "آیدی عددی‌اش نمایش داده می‌شود.",
            ), None

        if len(parts) == 3 and parts[1] == "r" and parts[2].isdigit():

            target = int(parts[2])

            def mutator(cfg):

                before = len(cfg["admins"])

                cfg["admins"] = [
                    int(x) for x in cfg["admins"] if int(x) != target
                ]

                return len(cfg["admins"]) != before, target

            cfg, _ = await aio(update_config, user_id, f"Remove admin {target}", mutator)

            return view_admins(cfg), f"🗑 ادمین {target} حذف شد"

    return await home_view(config, user_id), None


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    user_id = query.from_user.id

    toast = None
    alert = False

    try:

        config, _ = await aio(load_config)

        if not is_admin(user_id, config):

            toast, alert = "⛔️ دسترسی غیرمجاز", True

        else:

            # رفتن به هر صفحه، ورودی در انتظار را لغو می‌کند.
            context.user_data.pop("await", None)

            view, toast = await route(query.data, user_id, config, context)

            await safe_edit(query, *view)

    except Exception:

        log.exception("Callback failed")

        toast, alert = "⚠️ خطایی رخ داد؛ دوباره تلاش کن", True

    try:
        await query.answer(toast, show_alert=alert)
    except Exception:
        log.warning("Could not answer callback query")


# =====================================================
# پیام‌های متنی (ورودی منبع، کلمه، ادمین)
# =====================================================

async def reply(update, text, markup=None):

    await update.message.reply_text(
        text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )


async def input_sources(text, user_id, config):

    tokens = [t for t in re.split(r"[\s,،;؛]+", text.strip()) if t][:10]

    parsed = []
    invalid = []

    for token in tokens:

        source = parse_source(token)

        if source is None:
            invalid.append(token)

        elif source.lower() not in [p.lower() for p in parsed]:
            parsed.append(source)

    existing = {source.lower(): source for source in config["sources"]}

    accepted = []
    rejected = [(token, "آیدی یا لینک کانال نامعتبر است") for token in invalid]
    titles = {}

    for source in parsed:

        if source.lower() == DESTINATION.lower():
            rejected.append((source, "این کانال مقصد است"))
            continue

        if source.lower() in existing:
            accepted.append(existing[source.lower()])
            continue

        title, error = await aio(check_channel, source)

        if error:
            rejected.append((source, error))
        else:
            accepted.append(source)
            titles[source] = title

    added = []
    reenabled = []
    already = []
    over_limit = []

    cfg = config

    if accepted:

        def mutator(current):

            active = enabled_set(current)

            for source in accepted:

                key = source_key(source)
                found = find_source(current, key)

                if found is None:

                    if len(current["sources"]) >= MAX_SOURCES:
                        over_limit.append(source)
                        continue

                    current["sources"].append(source)
                    active.add(key)
                    added.append(source)

                elif key not in active:
                    active.add(key)
                    reenabled.append(found)

                else:
                    already.append(found)

            current["enabled_sources"] = [
                item for item in current["sources"]
                if source_key(item) in active
            ]

            return bool(added or reenabled), None

        # در صورت تلاش دوباره، لیست‌ها از نو پر شوند.
        def run_update():

            added.clear()
            reenabled.clear()
            already.clear()
            over_limit.clear()

            return update_config(user_id, "Add sources", mutator)

        cfg, _ = await aio(run_update)

    notice = []

    if added:
        notice.append("✅ <b>اضافه شد</b>")
        notice += [f"• {esc(s)} — «{esc(titles.get(s, ''))}»" for s in added]

    if reenabled:
        notice.append("♻️ <b>دوباره فعال شد</b>")
        notice += [f"• {esc(s)}" for s in reenabled]

    if already:
        notice.append("ℹ️ <b>از قبل فعال بود</b>")
        notice += [f"• {esc(s)}" for s in already]

    if over_limit:
        notice.append(f"⛔️ <b>سقف {fa(MAX_SOURCES)} منبع پر شده</b>")
        notice += [f"• {esc(s)}" for s in over_limit]

    if rejected:
        notice.append("❌ <b>اضافه نشد</b>")
        notice += [f"• {esc(s)} — {esc(why)}" for s, why in rejected]

    if added or reenabled:
        notice += [
            "",
            "ناشر در دور بعدی (حداکثر حدود ۵ دقیقه) بررسی‌شان می‌کند؛ "
            "اخبار قدیمی منتشر نمی‌شود و از همین لحظه شروع می‌شود.",
        ]

    # اگر هیچ منبع معتبری پیدا نشد، پنل منتظر ورودی بعدی می‌ماند.
    return "\n".join(notice), cfg, not accepted


async def input_words(text, user_id, config):

    words, too_short = parse_words(text)

    if not words:

        return (
            "❌ کلمه‌ی معتبری پیدا نشد.\n"
            "هر کلمه باید حداقل ۲ حرف باشد.",
            config,
            True,
        )

    added = []
    existing = []
    over_limit = []

    def mutator(cfg):

        added.clear()
        existing.clear()
        over_limit.clear()

        known = {normalize(w) for w in cfg["block_words"]}

        for word in words:

            key = normalize(word)

            if key in known:
                existing.append(word)
                continue

            if len(cfg["block_words"]) >= MAX_WORDS:
                over_limit.append(word)
                continue

            cfg["block_words"].append(word)
            known.add(key)
            added.append(word)

        return bool(added), None

    cfg, _ = await aio(update_config, user_id, "Add blocked words", mutator)

    notice = []

    if added:
        notice.append(f"✅ <b>{fa(len(added))} کلمه اضافه شد</b>")
        notice += [f"• {esc(w)}" for w in added]

    if existing:
        notice.append("ℹ️ از قبل بود: " + "، ".join(esc(w) for w in existing))

    if over_limit:
        notice.append(f"⛔️ سقف {fa(MAX_WORDS)} کلمه پر شده.")

    if too_short:
        notice.append("❌ نامعتبر (کوتاه یا بلند): " + "، ".join(esc(w) for w in too_short))

    return "\n".join(notice), cfg, False


async def input_admin(update, text, user_id, config):

    message = update.message
    origin = getattr(message, "forward_origin", None)
    sender = getattr(origin, "sender_user", None)

    if sender is not None:
        target = int(sender.id)

    elif (text or "").strip().isdigit():
        target = int((text or "").strip())

    else:

        return (
            "❌ آیدی عددی معتبر نیست.\n"
            "یک عدد بفرست، یا پیامی از آن کاربر را فوروارد کن "
            "(اگر کاربر فوروارد را مخفی کرده، باید آیدی را دستی بفرستی).",
            config,
            True,
        )

    if target == OWNER_ID:
        return "ℹ️ Owner از قبل دسترسی کامل دارد.", config, False

    def mutator(cfg):

        admins = [int(x) for x in cfg["admins"]]

        if target in admins:
            return False, "exists"

        cfg["admins"] = admins + [target]

        return True, "added"

    cfg, status = await aio(update_config, user_id, f"Add admin {target}", mutator)

    if status == "exists":
        return f"ℹ️ کاربر <code>{target}</code> از قبل ادمین است.", cfg, False

    return f"✅ کاربر <code>{target}</code> به ادمین‌ها اضافه شد.", cfg, False


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    try:

        config, _ = await aio(load_config)

        if not is_admin(user_id, config):

            await reply(
                update,
                f"{DENIED}\n\nآیدی عددی شما: <code>{user_id}</code>",
            )

            return

        mode = context.user_data.get("await")
        text = update.message.text or ""

        if not mode:

            # پیام آزاد: پنل را نشان بده.
            await start(update, context)

            return

        if mode == "src_add":
            notice, cfg, keep = await input_sources(text, user_id, config)

        elif mode == "word_add":
            notice, cfg, keep = await input_words(text, user_id, config)

        elif mode == "admin_add":

            if not is_owner(user_id):
                context.user_data.pop("await", None)
                return

            notice, cfg, keep = await input_admin(update, text, user_id, config)

        else:
            context.user_data.pop("await", None)
            return

        if keep:

            await reply(
                update,
                notice,
                keyboard([btn("❌ انصراف", "cancel", "danger")]),
            )

            return

        context.user_data.pop("await", None)

        if mode == "src_add":
            view = view_sources(cfg, notice)

        elif mode == "word_add":
            view = view_words(cfg, notice)

        else:
            notice_text, markup = view_admins(cfg)
            view = (f"{notice}\n\n{LINE}\n\n{notice_text}", markup)

        await reply(update, *view)

    except Exception:

        log.exception("Message handling failed")

        await reply(update, "⚠️ خطایی رخ داد؛ دوباره تلاش کن.")


# =====================================================
# دستورها
# =====================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    context.user_data.pop("await", None)

    try:
        config, _ = await aio(load_config)
    except Exception:
        log.exception("Config loading failed")
        await reply(update, "⚠️ خطا در دریافت تنظیمات.")
        return

    if not is_admin(user_id, config):

        await reply(
            update,
            f"{DENIED}\n\nآیدی عددی شما: <code>{user_id}</code>",
        )

        return

    await reply(update, *(await home_view(config, user_id)))


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.pop("await", None)

    await start(update, context)


async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if not is_owner(user_id):
        await reply(update, "⛔️ فقط Owner اجازه افزودن ادمین دارد.")
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await reply(update, "فرمت صحیح:\n<code>/addadmin 123456789</code>")
        return

    target = int(context.args[0])

    if target == OWNER_ID:
        await reply(update, "Owner از قبل دسترسی کامل دارد.")
        return

    def mutator(cfg):

        admins = [int(x) for x in cfg["admins"]]

        if target in admins:
            return False, None

        cfg["admins"] = admins + [target]

        return True, None

    try:
        await aio(update_config, user_id, f"Add admin {target}", mutator)
        await reply(update, f"✅ کاربر <code>{target}</code> به ادمین‌ها اضافه شد.")

    except Exception:
        log.exception("Adding admin failed")
        await reply(update, "⚠️ خطا در ذخیره ادمین.")


async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if not is_owner(user_id):
        await reply(update, "⛔️ فقط Owner اجازه حذف ادمین دارد.")
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await reply(update, "فرمت صحیح:\n<code>/removeadmin 123456789</code>")
        return

    target = int(context.args[0])

    def mutator(cfg):

        before = len(cfg["admins"])

        cfg["admins"] = [int(x) for x in cfg["admins"] if int(x) != target]

        return len(cfg["admins"]) != before, None

    try:
        await aio(update_config, user_id, f"Remove admin {target}", mutator)
        await reply(update, f"✅ دسترسی ادمین <code>{target}</code> حذف شد.")

    except Exception:
        log.exception("Removing admin failed")
        await reply(update, "⚠️ خطا در ذخیره تنظیمات.")


async def on_error(update, context):
    log.error("Unhandled error", exc_info=context.error)


async def post_init(application):

    try:
        await application.bot.set_my_commands([
            BotCommand("start", "باز کردن پنل مدیریت"),
            BotCommand("cancel", "انصراف از کار جاری"),
        ])
    except Exception:
        log.warning("Could not set bot commands")


def main():

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    private = tg_filters.ChatType.PRIVATE

    app.add_handler(CommandHandler("start", start, filters=private))
    app.add_handler(CommandHandler("panel", panel, filters=private))
    app.add_handler(CommandHandler("cancel", cancel, filters=private))
    app.add_handler(CommandHandler("addadmin", addadmin, filters=private))
    app.add_handler(CommandHandler("removeadmin", removeadmin, filters=private))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(
        MessageHandler(
            (tg_filters.TEXT | tg_filters.FORWARDED)
            & ~tg_filters.COMMAND
            & private,
            on_message,
        )
    )
    app.add_error_handler(on_error)

    log.info("Iran Alarm admin bot started")

    app.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
