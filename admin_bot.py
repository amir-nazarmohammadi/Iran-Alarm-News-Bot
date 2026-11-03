import os
import json
import base64
import logging
import requests

from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

OWNER_ID = 96635648
BOT_TOKEN = os.environ["ADMIN_BOT_TOKEN"]
GH_TOKEN = os.environ["GH_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]

BRANCH = "main"
CONFIG_PATH = "config.json"

API_URL = (
    f"https://api.github.com/repos/{REPO}/contents/{CONFIG_PATH}"
)

HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

DEFAULT_CONFIG = {
    "destination": "@Iran_Alarm_News",
    "paused": False,
    "sources": [
        "@newscenter",
        "@Politicalnewschannell",
        "@km_ap",
        "@IranianMinds",
    ],
    "enabled_sources": [
        "@newscenter",
        "@Politicalnewschannell",
        "@km_ap",
        "@IranianMinds",
    ],
    "admins": [],
    "filters": {
        "ads": True,
        "gambling": True,
        "vpn_promotions": True,
    },
    "updated_at": None,
    "updated_by": None,
}


def load_config():
    response = requests.get(
        API_URL,
        headers=HEADERS,
        params={"ref": BRANCH},
        timeout=20,
    )

    if response.status_code == 404:
        return DEFAULT_CONFIG.copy(), None

    response.raise_for_status()

    result = response.json()
    content = base64.b64decode(result["content"]).decode("utf-8")

    config = DEFAULT_CONFIG.copy()
    config.update(json.loads(content))

    return config, result["sha"]


def save_config(config, sha, user_id, message):
    config["updated_at"] = datetime.now(timezone.utc).isoformat()
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
        API_URL,
        headers=HEADERS,
        json=payload,
        timeout=20,
    )

    response.raise_for_status()


def is_owner(user_id):
    return int(user_id) == OWNER_ID


def is_admin(user_id, config):
    admins = [int(x) for x in config.get("admins", [])]
    return is_owner(user_id) or int(user_id) in admins


def keyboard(user_id):
    rows = [
        [
            InlineKeyboardButton("📊 وضعیت ربات", callback_data="status"),
            InlineKeyboardButton("⏯ توقف / ادامه", callback_data="pause"),
        ],
        [
            InlineKeyboardButton("📡 منابع خبری", callback_data="sources"),
            InlineKeyboardButton("🧹 فیلترها", callback_data="filters"),
        ],
    ]

    if is_owner(user_id):
        rows.append([
            InlineKeyboardButton(
                "👥 مدیریت ادمین‌ها",
                callback_data="admins",
            )
        ])

    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    try:
        config, _ = load_config()
    except Exception:
        log.exception("Config loading failed")
        await update.message.reply_text("خطا در دریافت تنظیمات.")
        return

    if not is_admin(user_id, config):
        await update.message.reply_text(
            "⛔️ شما اجازه دسترسی به پنل را ندارید."
        )
        return

    role = "Owner 👑" if is_owner(user_id) else "Admin"

    await update.message.reply_text(
        f"به پنل ایران آلارم خوش آمدی.\n"
        f"سطح دسترسی: {role}",
        reply_markup=keyboard(user_id),
    )


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    try:
        config, sha = load_config()
    except Exception:
        log.exception("Config loading failed")
        await query.edit_message_text("خطا در دریافت تنظیمات.")
        return

    if not is_admin(user_id, config):
        await query.edit_message_text("⛔️ دسترسی غیرمجاز")
        return

    data = query.data

    if data == "home":
        await query.edit_message_text(
            "پنل مدیریت:",
            reply_markup=keyboard(user_id),
        )
        return

    if data == "status":
        await query.edit_message_text(
            "📊 وضعیت ایران آلارم\n\n"
            f"انتشار: {'⏸ متوقف' if config['paused'] else '🟢 فعال'}\n"
            f"کانال مقصد: {config['destination']}\n"
            f"منابع فعال: {len(config['enabled_sources'])}\n"
            f"ادمین‌ها: {len(config['admins'])}\n"
            f"آخرین تغییر: {config.get('updated_at') or 'ندارد'}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="home")]
            ]),
        )
        return

    if data == "pause":
        config["paused"] = not config["paused"]

        save_config(
            config,
            sha,
            user_id,
            "Toggle publishing status",
        )

        await query.edit_message_text(
            "⏸ انتشار متوقف شد." if config["paused"]
            else "🟢 انتشار فعال شد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="home")]
            ]),
        )
        return

    if data == "sources":
        active = set(config["enabled_sources"])

        rows = [
            [
                InlineKeyboardButton(
                    f"{'✅' if source in active else '❌'} {source}",
                    callback_data=f"source:{index}",
                )
            ]
            for index, source in enumerate(config["sources"])
        ]

        rows.append([
            InlineKeyboardButton("🔙 بازگشت", callback_data="home")
        ])

        await query.edit_message_text(
            "مدیریت منابع خبری:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("source:"):
        index = int(data.split(":")[1])

        if index < 0 or index >= len(config["sources"]):
            await query.edit_message_text("منبع نامعتبر است.")
            return

        source = config["sources"][index]
        active = set(config["enabled_sources"])

        if source in active:
            active.remove(source)
        else:
            active.add(source)

        config["enabled_sources"] = [
            item for item in config["sources"]
            if item in active
        ]

        save_config(
            config,
            sha,
            user_id,
            f"Toggle source {source}",
        )

        active = set(config["enabled_sources"])

        rows = [
            [
                InlineKeyboardButton(
                    f"{'✅' if item in active else '❌'} {item}",
                    callback_data=f"source:{i}",
                )
            ]
            for i, item in enumerate(config["sources"])
        ]

        rows.append([
            InlineKeyboardButton("🔙 بازگشت", callback_data="home")
        ])

        await query.edit_message_text(
            "منابع خبری (تغییر ذخیره شد):",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data == "filters":
        filters = config["filters"]

        options = [
            ("ads", "تبلیغات"),
            ("gambling", "قمار و شرط‌بندی"),
            ("vpn_promotions", "تبلیغات VPN و پروکسی"),
        ]

        rows = [
            [
                InlineKeyboardButton(
                    f"{'✅' if filters.get(key, True) else '❌'} {label}",
                    callback_data=f"filter:{key}",
                )
            ]
            for key, label in options
        ]

        rows.append([
            InlineKeyboardButton("🔙 بازگشت", callback_data="home")
        ])

        await query.edit_message_text(
            "مدیریت فیلترها:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("filter:"):
        key = data.split(":")[1]

        if key not in ("ads", "gambling", "vpn_promotions"):
            await query.edit_message_text("فیلتر نامعتبر است.")
            return

        config["filters"][key] = not config["filters"].get(key, True)

        save_config(
            config,
            sha,
            user_id,
            f"Toggle filter {key}",
        )

        filters = config["filters"]

        options = [
            ("ads", "تبلیغات"),
            ("gambling", "قمار و شرط‌بندی"),
            ("vpn_promotions", "تبلیغات VPN و پروکسی"),
        ]

        rows = [
            [
                InlineKeyboardButton(
                    f"{'✅' if filters.get(k, True) else '❌'} {label}",
                    callback_data=f"filter:{k}",
                )
            ]
            for k, label in options
        ]

        rows.append([
            InlineKeyboardButton("🔙 بازگشت", callback_data="home")
        ])

        await query.edit_message_text(
            "فیلترها (تغییر ذخیره شد):",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data == "admins":
        if not is_owner(user_id):
            await query.edit_message_text("⛔️ فقط Owner اجازه دارد.")
            return

        rows = [
            [
                InlineKeyboardButton(
                    f"❌ حذف {admin_id}",
                    callback_data=f"admin:remove:{admin_id}",
                )
            ]
            for admin_id in config["admins"]
        ]

        rows.append([
            InlineKeyboardButton(
                "➕ راهنمای افزودن ادمین",
                callback_data="admin:help",
            )
        ])

        rows.append([
            InlineKeyboardButton("🔙 بازگشت", callback_data="home")
        ])

        await query.edit_message_text(
            "مدیریت ادمین‌ها\n\n"
            "افزودن: /addadmin USER_ID\n"
            "حذف: از دکمه‌های زیر استفاده کن.",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data == "admin:help":
        if not is_owner(user_id):
            await query.edit_message_text("⛔️ فقط Owner اجازه دارد.")
            return

        await query.edit_message_text(
            "برای افزودن ادمین دستور زیر را بفرست:\n"
            "/addadmin 123456789"
        )
        return

    if data.startswith("admin:remove:"):
        if not is_owner(user_id):
            await query.edit_message_text("⛔️ فقط Owner اجازه دارد.")
            return

        target = int(data.rsplit(":", 1)[1])

        config["admins"] = [
            int(x) for x in config["admins"]
            if int(x) != target
        ]

        save_config(
            config,
            sha,
            user_id,
            f"Remove admin {target}",
        )

        await query.edit_message_text(
            f"ادمین {target} حذف شد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="admins")]
            ]),
        )
        return


async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("⛔️ فقط Owner اجازه افزودن ادمین دارد.")
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text(
            "فرمت صحیح:\n/addadmin 123456789"
        )
        return

    target = int(context.args[0])

    if target == OWNER_ID:
        await update.message.reply_text("Owner از قبل دسترسی کامل دارد.")
        return

    try:
        config, sha = load_config()

        if target not in [int(x) for x in config["admins"]]:
            config["admins"].append(target)

            save_config(
                config,
                sha,
                user_id,
                f"Add admin {target}",
            )

        await update.message.reply_text(
            f"✅ کاربر {target} به ادمین‌ها اضافه شد."
        )

    except Exception:
        log.exception("Adding admin failed")
        await update.message.reply_text("خطا در ذخیره ادمین.")


async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("⛔️ فقط Owner اجازه حذف ادمین دارد.")
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text(
            "فرمت صحیح:\n/removeadmin 123456789"
        )
        return

    target = int(context.args[0])

    try:
        config, sha = load_config()

        config["admins"] = [
            int(x) for x in config["admins"]
            if int(x) != target
        ]

        save_config(
            config,
            sha,
            user_id,
            f"Remove admin {target}",
        )

        await update.message.reply_text(
            f"✅ دسترسی ادمین {target} حذف شد."
        )

    except Exception:
        log.exception("Removing admin failed")
        await update.message.reply_text("خطا در ذخیره تنظیمات.")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("removeadmin", removeadmin))
    app.add_handler(CallbackQueryHandler(callback))

    log.info("Iran Alarm admin bot started")
    app.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
