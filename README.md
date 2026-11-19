# 🇮🇷 Iran Alarm News Bot

<p align="center">
  <strong>An automated, open-source Telegram news aggregation and publishing system</strong>
</p>

<p align="center">
  <i>Collect. Filter. Detect. Publish.</i>
</p>

---

## 🌐 About

**Iran Alarm News Bot** is an open-source automation project built to streamline the collection, filtering, and publication of news from multiple Telegram channels into a centralized news destination.

The project is designed to reduce repetitive manual work, improve the speed of news distribution, and maintain a cleaner publishing environment by filtering unwanted content, detecting duplicate posts, and managing source channels through an automated workflow.

Built with Python and powered by GitHub Actions, the system can operate without requiring a dedicated VPS or continuously running personal computer.

The project is developed with a focus on reliability, automation, modular management, and accessibility.

---

## ✨ Key Features

- **Automated News Collection** — Monitors multiple Telegram source channels and collects new posts.
- **Automatic Publishing** — Publishes eligible content to the destination channel without manual intervention.
- **Content Filtering** — Filters unwanted advertising, gambling-related content, and VPN/proxy promotions.
- **Duplicate Detection** — Prevents repetitive content from being published using stored fingerprints and similarity checks.
- **Dynamic Source Management** — Designed to allow source channels to be added, removed, enabled, or disabled.
- **Media Support** — Handles news posts containing text, photos, videos, and media content.
- **Custom Keyword Filtering** — Supports configurable blocked words and phrases.
- **Persistent State** — Maintains publication history and processing data between workflow executions.
- **Pause & Emergency Controls** — Includes controls for temporarily pausing the system or stopping publication.
- **Automated Execution** — Uses GitHub Actions to run scheduled background tasks.
- **Serverless Operation** — No traditional VPS is required for the scheduled automation.
- **Administrative Interface** — Provides a Telegram-based management interface for system configuration and monitoring.

---

## ⚙️ How It Works

The automation follows a simple pipeline:

```text
Telegram Sources
      │
      ▼
Collect New Posts
      │
      ▼
Content & Advertisement Filters
      │
      ▼
Duplicate Detection
      │
      ▼
Content Processing
      │
      ▼
Automatic Publishing
      │
      ▼
Iran Alarm News Channel
```

The system continuously processes new content during its active workflow, evaluates posts against configured filtering rules, and publishes content that passes the processing pipeline.

Previously processed content is tracked to minimize repeated publications.

---

## 🧰 Technology Stack

| Technology | Purpose |
|---|---|
| Python | Core automation and processing logic |
| Telethon | Telegram client and channel interaction |
| Telegram Bot API | Administrative interface |
| GitHub Actions | Scheduled execution and automation |
| GitHub Repository | Source code, configuration and workflow management |
| JSON | Configuration and persistent state storage |

---

## 📁 Project Structure

```text
Iran-Alarm-News-Bot/
│
├── main.py
│   └── News collection, filtering, processing and publishing
│
├── admin_bot.py
│   └── Telegram administration and system management
│
├── config.json
│   └── Sources, settings, filters and admin configuration
│
├── data/
│   └── state.json
│       └── Persistent state and processed-content tracking
│
├── .github/
│   └── workflows/
│       ├── publish.yml
│       └── admin.yml
│
└── requirements.txt
    └── Python dependencies
```

---

## 🛡️ Security & Reliability

Security and reliability are important aspects of the project.

- Sensitive credentials are intended to be stored using GitHub Actions Secrets rather than hardcoded in source files.
- Telegram session credentials and API tokens must remain private.
- Automated workflows are designed to minimize unnecessary manual intervention.
- Persistent state helps prevent repeated processing of previously handled content.
- Configuration-based controls allow the automation to be managed without repeatedly modifying core source code.

Contributions toward improving security, fault tolerance, and automation reliability are welcome.

---

## 🚀 Deployment

The project is designed to run through GitHub Actions.

### 1. Fork or clone the repository

```bash
git clone https://github.com/amir-nazarmohammadi/Iran-Alarm-News-Bot.git
```

### 2. Configure repository secrets

Add the required credentials to your repository's GitHub Actions secrets.

Typical credentials include:

- `API_ID`
- `API_HASH`
- `TG_SESSION`
- `ADMIN_BOT_TOKEN`
- `GH_TOKEN` (when required for configuration updates)

Never expose Telegram session strings, bot tokens, or private API credentials in public commits, issues, screenshots, or logs.

### 3. Configure the bot

Modify `config.json` to customize the destination channel, sources, enabled sources, filters, and administrative settings.

### 4. Enable GitHub Actions

Ensure workflows are enabled in the repository and configure the required permissions and secrets.

### 5. Start the automation

Use the GitHub Actions workflow controls to initiate or manage the automated process.

---

## 🎯 Project Goals

Iran Alarm News Bot aims to provide a practical and maintainable automation framework for Telegram-based news distribution.

The long-term development direction includes:

- Smarter contextual content classification
- Improved advertisement and spam detection
- More reliable duplicate and similarity detection
- Better source health monitoring
- Enhanced administrative controls
- Improved error handling and recovery
- AI-assisted moderation and content analysis
- More efficient resource usage

These improvements are intended to make the system more reliable, scalable, and easier to maintain.

---

## 🤝 Contributions

Contributions, bug reports, suggestions, and ideas are welcome.

If you find an issue or have an idea for improving the system, feel free to open an issue or submit a pull request.

Whether it's improving filtering logic, strengthening security, optimizing workflows, or adding new features, contributions can help make the project better.

---

## 📜 License

This project is open source. Please check the repository for licensing information and applicable terms before reusing or redistributing the code.

---

## 📡 Project

**Iran Alarm News Bot**

Developed and maintained by **Amir Nazarmohammadi**

GitHub: [@amir-nazarmohammadi](https://github.com/amir-nazarmohammadi)

Repository: [Iran-Alarm-News-Bot](https://github.com/amir-nazarmohammadi/Iran-Alarm-News-Bot)

---

<p align="center">
  <b>Built for automation. Designed for reliability. Open for improvement.</b>
</p>

---

# 🇮🇷 ربات ایران آلارم نیوز

<p align="center">
  <strong>سامانه متن‌باز جمع‌آوری، پالایش و انتشار خودکار اخبار تلگرامی</strong>
</p>

<p align="center">
  <i>جمع‌آوری • پالایش • تشخیص • انتشار</i>
</p>

---

## 📌 درباره پروژه

**Iran Alarm News Bot** یک پروژه متن‌باز با هدف خودکارسازی فرآیند جمع‌آوری، بررسی، پالایش و انتشار اخبار از چندین کانال تلگرامی در یک کانال مرکزی است.

این پروژه با هدف کاهش فعالیت‌های دستی و تکراری، افزایش سرعت انتشار اخبار، جلوگیری از انتشار محتوای تکراری و حذف تبلیغات و مطالب نامرتبط طراحی شده است.

این سیستم با استفاده از پایتون و GitHub Actions اجرا می‌شود و برای فعالیت‌های زمان‌بندی‌شده نیازی به سرور اختصاصی یا روشن بودن دائمی رایانه شخصی ندارد.

تمرکز اصلی پروژه بر خودکارسازی، پایداری، مدیریت آسان، توسعه‌پذیری و ساده‌سازی فرآیند انتشار اخبار است.

---

## ✨ امکانات

- **جمع‌آوری خودکار اخبار:** بررسی و دریافت مطالب جدید از کانال‌های منبع.
- **انتشار خودکار:** انتشار مطالب واجد شرایط بدون نیاز به دخالت دستی.
- **فیلتر محتوا:** شناسایی و حذف تبلیغات، محتوای قمار و تبلیغات VPN و پروکسی.
- **تشخیص مطالب تکراری:** جلوگیری از انتشار مجدد اخبار با استفاده از اثر انگشت محتوا و بررسی شباهت.
- **مدیریت منابع پویا:** امکان مدیریت کانال‌های منبع، فعال‌سازی و غیرفعال‌سازی منابع.
- **پشتیبانی از رسانه:** پردازش مطالب متنی و رسانه‌ای شامل عکس و ویدئو.
- **فیلتر کلمات سفارشی:** مدیریت کلمات و عبارت‌های ممنوعه.
- **ذخیره وضعیت:** حفظ سوابق پردازش و انتشار برای جلوگیری از تکرار.
- **کنترل توقف و وضعیت اضطراری:** امکان توقف موقت یا قطع فوری فرآیند انتشار.
- **اجرای خودکار:** فعالیت از طریق گردش‌کارهای GitHub Actions.
- **بدون نیاز به سرور اختصاصی:** استفاده از زیرساخت اتوماسیون GitHub.
- **پنل مدیریتی تلگرام:** مدیریت تنظیمات و کنترل سیستم از طریق رابط مدیریتی ربات.

---

## ⚙️ نحوه عملکرد

```text
کانال‌های منبع تلگرام
          │
          ▼
 دریافت مطالب جدید
          │
          ▼
  فیلتر محتوا و تبلیغات
          │
          ▼
  تشخیص مطالب تکراری
          │
          ▼
   پردازش و آماده‌سازی
          │
          ▼
     انتشار خودکار
          │
          ▼
     کانال ایران آلارم نیوز
```

ربات مطالب جدید را دریافت می‌کند، آن‌ها را بر اساس قوانین تعریف‌شده بررسی می‌کند، موارد نامناسب یا تکراری را کنار می‌گذارد و محتوای واجد شرایط را در کانال مقصد منتشر می‌کند.

---

## 🧰 تکنولوژی‌های استفاده‌شده

| تکنولوژی | کاربرد |
|---|---|
| Python | منطق اصلی ربات و پردازش محتوا |
| Telethon | ارتباط با تلگرام و کانال‌ها |
| Telegram Bot API | رابط مدیریتی |
| GitHub Actions | اجرای خودکار و زمان‌بندی |
| GitHub | میزبانی کد و مدیریت پروژه |
| JSON | ذخیره تنظیمات و وضعیت سیستم |

---

## 🎯 اهداف توسعه

هدف این پروژه ارائه یک ابزار اتوماسیون کاربردی، قابل نگهداری و توسعه‌پذیر برای انتشار اخبار در تلگرام است.

برنامه‌های توسعه آینده شامل موارد زیر هستند:

- طبقه‌بندی هوشمندتر اخبار
- تشخیص دقیق‌تر تبلیغات و اسپم
- بهبود تشخیص اخبار مشابه و تکراری
- نظارت بهتر بر سلامت کانال‌های منبع
- توسعه قابلیت‌های مدیریتی
- بهبود مدیریت خطا و بازیابی خودکار
- استفاده از هوش مصنوعی برای تحلیل و پالایش محتوا
- بهینه‌سازی مصرف منابع و پایداری اجرا

---

## 🤝 مشارکت

از گزارش مشکلات، پیشنهادها، ایده‌های جدید و مشارکت در توسعه پروژه استقبال می‌شود.

اگر ایده‌ای برای بهبود سیستم دارید یا با مشکلی مواجه شده‌اید، می‌توانید Issue ثبت کنید یا Pull Request ارسال کنید.

---

## 👨‍💻 توسعه‌دهنده

**Amir Nazarmohammadi**

GitHub: [@amir-nazarmohammadi](https://github.com/amir-nazarmohammadi)

Repository: [Iran-Alarm-News-Bot](https://github.com/amir-nazarmohammadi/Iran-Alarm-News-Bot)

---

<p align="center">
  <b>متن‌باز برای توسعه، خودکارسازی برای سرعت، و تلاش برای انتشار خبری بهتر.</b>
</p>
