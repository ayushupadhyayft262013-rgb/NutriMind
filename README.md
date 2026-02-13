<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/Gemini_2.0_Flash-4285F4?style=for-the-badge&logo=google&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
  <img src="https://img.shields.io/badge/DigitalOcean-0080FF?style=for-the-badge&logo=digitalocean&logoColor=white" />
</p>

# 🧠 NutriMind — AI-Powered Nutrition Tracker

**NutriMind** is a production-grade, AI-powered nutrition tracking system that lets users log meals through **Telegram** (text, photos, or voice notes), leverages **Google Gemini 2.0 Flash** to analyze food and estimate macronutrients, and syncs everything to a **Notion** database — with a beautiful real-time **web dashboard** for at-a-glance daily summaries.

> Built end-to-end: AI integration → backend API → Telegram bot → web dashboard → containerized deployment → CI/CD pipeline.

---

## ✨ Key Features

| Feature | Description |
|---|---|
| 🤖 **AI Food Analysis** | Send text ("had 2 eggs and toast"), a photo, or a voice note — Gemini 2.0 Flash identifies foods & estimates calories, protein, carbs, and fats |
| 💬 **Telegram Bot Interface** | Full conversational bot with onboarding, meal logging, target setting, and meal editing |
| 📊 **Live Web Dashboard** | Mobile-optimized status page with calorie ring chart, macro breakdowns, and interactive meal editing |
| 📝 **Notion Sync** | Every meal is logged to a personal Notion database with daily pages, tables, and running totals |
| 🎯 **Customizable Targets** | Set daily calorie and macro targets via Telegram (`/set_targets`) |
| ✏️ **Meal Editing** | Edit or delete meals from both the web dashboard and Telegram (`/edit_meals`) |
| 🧠 **Preference Learning** | Say "Remember: my bowl is 300ml" and the bot learns your personal food preferences |
| 👥 **Multi-User Support** | Each Telegram user gets their own profile, targets, and Notion pages |
| 🐳 **Dockerized Deployment** | One-command deploy with Docker Compose, persistent data, SSL certificates |
| 🔄 **CI/CD Pipeline** | Push to `main` → GitHub Actions auto-deploys to DigitalOcean, including webhook re-registration |

---

## 🏗️ Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Telegram   │────▶│   FastAPI Server  │────▶│  Google Gemini   │
│  Bot Client  │◀────│   (Webhook)       │◀────│  2.0 Flash API   │
└──────────────┘     │                   │     └─────────────────┘
                     │   ┌───────────┐   │
┌──────────────┐     │   │  SQLite   │   │     ┌─────────────────┐
│ Web Dashboard│◀────│   │  (Users,  │   │────▶│   Notion API     │
│  (Browser)   │────▶│   │  Prefs)   │   │◀────│  (Daily Logs)    │
└──────────────┘     │   └───────────┘   │     └─────────────────┘
                     └──────────────────┘
                            │
                     ┌──────┴──────┐
                     │   Docker    │
                     │  Container  │
                     │ (DigitalOcean) │
                     └─────────────┘
```

**Data Flow:**
1. User sends a meal (text/photo/voice) via Telegram
2. FastAPI webhook receives the update
3. Gemini 2.0 Flash analyzes the food and estimates nutrition
4. Results are stored in Notion (daily page + table rows) and SQLite (user profiles)
5. Web dashboard reads from Notion and displays real-time stats

---

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **AI / ML** | Google Gemini 2.0 Flash | Food recognition, nutrition estimation, voice transcription |
| **Backend** | Python 3.11+, FastAPI, Uvicorn | Async REST API, webhook handling, business logic |
| **Database** | SQLite (aiosqlite) | User profiles, preferences, onboarding state |
| **Data Store** | Notion API | Daily nutrition logs, meal tables, running totals |
| **Bot Platform** | Telegram Bot API | User interface — text, photo, and voice input |
| **Frontend** | HTML5, CSS3, JavaScript, Jinja2 | Real-time dashboard with interactive meal editing |
| **Containerization** | Docker, Docker Compose | Reproducible builds, volume persistence, SSL mounting |
| **CI/CD** | GitHub Actions | Auto-deploy on push to `main` via SSH |
| **Infrastructure** | DigitalOcean Droplet | Cloud hosting with self-signed SSL certificates |
| **HTTP Client** | httpx (async) | API calls to Telegram, Gemini, and Notion |

---

## 📂 Project Structure

```
Project_Nutrition/
├── app/
│   ├── main.py              # FastAPI app, routes, status dashboard
│   ├── telegram_handler.py  # Command routing, meal logging, edit flows
│   ├── telegram_client.py   # Thin async Telegram Bot API client
│   ├── gemini_service.py    # Gemini 2.0 Flash integration (text/image/audio)
│   ├── nutrition_engine.py  # Food analysis pipeline & clarification handling
│   ├── notion_service.py    # Notion database CRUD: pages, tables, totals
│   ├── database.py          # SQLite: user profiles, preferences, state
│   ├── onboarding.py        # Multi-step conversational onboarding flow
│   ├── preferences.py       # User preference learning ("Remember: ...")
│   ├── config.py            # Environment-based configuration
│   └── templates/
│       └── status.html      # Dashboard: calorie ring, macros, meal editing
├── .github/
│   └── workflows/
│       └── deploy.yml       # CI/CD: auto-deploy to DigitalOcean on push
├── Dockerfile               # Multi-stage build with curl for webhook setup
├── docker-compose.yml       # Service definition with volumes & SSL
├── entrypoint.sh            # Container startup: server + webhook registration
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template
└── .gitattributes           # Enforce LF line endings for shell scripts
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- A [Telegram Bot Token](https://core.telegram.org/bots#creating-a-new-bot) (from BotFather)
- A [Google AI API Key](https://aistudio.google.com/apikey) (for Gemini 2.0 Flash)
- A [Notion Integration](https://www.notion.so/my-integrations) API key + a parent page ID

### 1. Clone & Configure

```bash
git clone https://github.com/ayft262013-rgb/NutriMind.git
cd NutriMind

cp .env.example .env
# Edit .env with your API keys and configuration
```

### 2. Run Locally

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

For local Telegram testing, use [ngrok](https://ngrok.com/) to expose your local server:
```bash
ngrok http 8000
# Then set the webhook: https://api.telegram.org/bot<TOKEN>/setWebhook?url=<NGROK_URL>/webhook/telegram
```

### 3. Deploy with Docker

```bash
# Generate self-signed SSL certificates (required for Telegram webhooks on IP addresses)
mkdir certs
openssl req -newkey rsa:2048 -sha256 -nodes \
  -keyout certs/key.pem -x509 -days 365 -out certs/cert.pem \
  -subj "/CN=YOUR_SERVER_IP"

# Build and run
docker compose up --build -d
```

### 4. CI/CD (GitHub Actions)

Every push to `main` automatically:
1. SSH into the DigitalOcean droplet
2. Pulls the latest code
3. Rebuilds the Docker image
4. Restarts the container
5. Re-registers the Telegram webhook with the SSL certificate

Required GitHub Secrets: `DROPLET_IP`, `DROPLET_USER`, `DROPLET_SSH_KEY`, `TELEGRAM_BOT_TOKEN`

---

## 💬 Bot Commands

| Command | Description |
|---|---|
| `/start` | Begin onboarding (name, weight, height, age, activity, goal) |
| `/set_targets` | View or change daily nutrition targets |
| `/edit_meals` | List, edit, or delete today's logged meals |
| `/today` | View today's nutrition summary |
| `/profile` | View your profile and targets |
| `/preferences` | View learned food preferences |
| `/start_tracking` / `/stop_tracking` | Toggle meal logging on/off |
| `/setup_notion` | Initialize your personal Notion database |
| `/help` | Show all available commands |

**Logging meals is as simple as:**
- 📝 Text: *"Had chicken biryani and raita for lunch"*
- 📸 Photo: Send a picture of your plate
- 🎤 Voice: Record a voice note describing your meal

---

## 📊 Web Dashboard

The `/status` endpoint serves a mobile-optimized dashboard featuring:

- **Calorie Ring** — animated SVG progress ring with gradient colors
- **Macro Cards** — protein, carbs, and fats with progress bars
- **Meal List** — every logged meal with inline **edit** and **delete** buttons
- **Real-time Updates** — editing a meal instantly recalculates all totals via REST API
- **Dark Theme** — premium dark glassmorphism design

---

## 🧠 AI Integration Details

NutriMind uses **Google Gemini 2.0 Flash** for:

| Capability | How It Works |
|---|---|
| **Text Analysis** | User describes food → Gemini extracts items, portions, and estimates kcal/protein/carbs/fats |
| **Image Recognition** | User sends a meal photo → Gemini identifies foods from the image |
| **Voice Transcription** | User sends a voice note → audio is sent to Gemini for transcription → then analyzed as text |
| **Clarification** | If Gemini needs more detail ("How big was the bowl?"), it asks follow-up questions |
| **Preference-Aware** | User preferences (e.g., "my roti is 80g") are injected into prompts for accuracy |

---

## 🔧 Environment Variables

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
WEBHOOK_BASE_URL=https://your-server-ip:8443

# Google AI
GOOGLE_API_KEY=your_gemini_api_key

# Notion
NOTION_API_KEY=your_notion_integration_token
NOTION_PARENT_PAGE_ID=your_notion_page_id

# Defaults
DEFAULT_TARGET_KCAL=2000
DEFAULT_TARGET_PROTEIN=150

# SSL (for production)
SSL_CERTFILE=/app/certs/cert.pem
SSL_KEYFILE=/app/certs/key.pem
```

---

## 🔑 Skills Demonstrated

- **AI/ML Integration** — Prompt engineering with Gemini 2.0 Flash for multi-modal food analysis (text, image, audio)
- **Full-Stack Development** — Python backend + interactive JavaScript frontend with real-time API-driven updates
- **API Design** — RESTful endpoints with FastAPI, async/await patterns, proper error handling
- **Bot Development** — Stateful conversational flows, multi-step onboarding, command handling
- **Third-Party Integration** — Telegram Bot API, Notion API, Google Generative AI SDK
- **DevOps & Deployment** — Docker containerization, Docker Compose, GitHub Actions CI/CD, SSL certificates
- **Cloud Infrastructure** — DigitalOcean droplet provisioning, SSH-based deployments, self-signed HTTPS
- **Database Design** — SQLite for user state, Notion for structured nutrition data, dual-storage architecture
- **UI/UX Design** — Mobile-first dark theme dashboard with animations, glassmorphism, and micro-interactions

---

## 📄 License

This project is for educational and portfolio purposes.

---

<p align="center">
  Built with ❤️ using AI-first development practices
</p>
