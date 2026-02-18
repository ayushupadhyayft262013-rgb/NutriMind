"""NutriMind — FastAPI application entry point."""

import logging
import math
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db, get_all_users
from app.telegram_handler import handle_update
from app import telegram_client as tg

try:
    from google.genai import types
except ImportError:
    types = None  # Gemini SDK not required for core functionality

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("nutrimind")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup & shutdown hooks."""
    logger.info("NutriMind starting up...")

    # Validate config
    missing = settings.validate()
    if missing:
        logger.warning(f"Missing config keys: {', '.join(missing)}")
        logger.warning("Some features may not work. Set them in .env")

    # Initialize database
    await init_db()
    logger.info("SQLite database initialized")

    # Note: Telegram webhook is registered by entrypoint.sh using curl
    # (httpx multipart cert upload was unreliable)

    yield

    # Shutdown
    logger.info("NutriMind shutting down")


app = FastAPI(
    title="NutriMind",
    description="AI-powered nutrition tracker bridging Telegram & Notion",
    version="1.0.0",
    lifespan=lifespan,
)

templates = Jinja2Templates(directory="app/templates")


# --- Routes ---------------------------------------------------------------


@app.get("/health")
async def health_check():
    """Simple health check endpoint."""
    return {"status": "ok", "service": "nutrimind"}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Receive Telegram webhook updates."""
    try:
        update = await request.json()
        logger.info(f"Telegram update: {update.get('update_id', 'unknown')}")
        await handle_update(update)
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
    return JSONResponse({"ok": True})


@app.delete("/api/meals/{block_id}")
async def delete_meal(block_id: str, page_id: str):
    """Delete a meal and recalculate daily totals."""
    from app.notion_service import notion_service
    try:
        await notion_service.delete_meal_row(block_id)
        totals = await notion_service.recalculate_daily_totals(page_id)
        return JSONResponse({"ok": True, "totals": totals})
    except Exception as e:
        logger.error(f"Delete meal error: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.put("/api/meals/{block_id}")
async def update_meal(block_id: str, request: Request):
    """Update a meal's values and recalculate daily totals."""
    from app.notion_service import notion_service
    try:
        data = await request.json()
        page_id = data.pop("page_id")
        await notion_service.update_meal_row(block_id, data)
        totals = await notion_service.recalculate_daily_totals(page_id)
        return JSONResponse({"ok": True, "totals": totals})
    except Exception as e:
        logger.error(f"Update meal error: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# In-memory cache for AI insights {date_iso: {"text": ..., "ts": ...}}
_insights_cache: dict[str, dict] = {}


@app.get("/api/weekly-data")
async def weekly_data(date: str = None, user_id: int = None):
    """Return 7 days of calorie/macro data ending on the given date."""
    from app.notion_service import notion_service
    import datetime as dt

    end_date = dt.date.fromisoformat(date) if date else dt.date.today()
    try:
        summaries = await notion_service.get_weekly_summaries(end_date, user_id=user_id)
        return JSONResponse({"ok": True, "days": summaries})
    except Exception as e:
        logger.error(f"Weekly data error: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/insights")
async def ai_insights(date: str = None, user_id: int = None):
    """Generate AI-powered weekly nutrition insights using Gemini."""
    from app.notion_service import notion_service
    from app.gemini_service import gemini_service
    import datetime as dt
    import time

    end_date = dt.date.fromisoformat(date) if date else dt.date.today()
    cache_key = f"{end_date.isoformat()}_{user_id or 'all'}"

    # Return cached insights if less than 1 hour old
    if cache_key in _insights_cache:
        cached = _insights_cache[cache_key]
        if time.time() - cached["ts"] < 3600:
            return JSONResponse({"ok": True, "insights": cached["text"]})

    try:
        summaries = await notion_service.get_weekly_summaries(end_date, user_id=user_id)

        # Build a data summary for Gemini
        lines = []
        for s in summaries:
            lines.append(
                f"{s['date']}: {s['total_kcal']} kcal "
                f"(target {s['target_kcal']}), "
                f"P:{s['total_protein']}g, C:{s['total_carbs']}g, F:{s['total_fats']}g"
            )
        data_text = "\n".join(lines)

        prompt = (
            "You are a friendly nutritionist AI. Analyze this user's past 7 days of nutrition data "
            "and provide 3-4 short, actionable insights. Be specific, reference the actual numbers, "
            "and suggest concrete food swaps or habits. Keep it concise (max 150 words). "
            "Use emoji for visual appeal. Do NOT use markdown formatting.\n\n"
            f"WEEKLY DATA:\n{data_text}"
        )

        response = gemini_service.client.models.generate_content(
            model=gemini_service.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
            ),
        )

        insights_text = response.text.strip()
        _insights_cache[cache_key] = {"text": insights_text, "ts": time.time()}
        return JSONResponse({"ok": True, "insights": insights_text})
    except Exception as e:
        logger.error(f"Insights error: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request, user_id: int = None, date: str = None):
    """Mobile-optimized daily status dashboard with date navigation."""
    from app.notion_service import notion_service

    today = __import__("datetime").date.today()
    if date:
        try:
            selected_date = __import__("datetime").date.fromisoformat(date)
        except ValueError:
            selected_date = today
    else:
        selected_date = today

    prev_date = (selected_date - timedelta(days=1)).isoformat()
    next_date = (selected_date + timedelta(days=1)).isoformat()
    is_today = selected_date == today

    total_kcal = 0
    target_kcal = settings.DEFAULT_TARGET_KCAL
    total_protein = 0
    total_carbs = 0
    total_fats = 0
    meals = []
    page_id = ""

    users = []
    try:
        users = await get_all_users()
    except Exception as e:
        logger.error(f"Error fetching users: {e}")

    # Default to first user if none selected and users exist
    if user_id is None and users:
        user_id = users[0]["telegram_user_id"]
    
    # If still no user_id (and no users), we might be in a fresh state
    # user_id remains None, and dashboard will show empty/default data

    try:
        if settings.NOTION_DAILY_LOG_DB_ID:
            summary = await notion_service.get_daily_summary(selected_date, user_id=user_id)
            if summary:
                total_kcal = summary["total_kcal"]
                target_kcal = summary["target_kcal"]
                total_protein = summary["total_protein"]
                total_carbs = summary["total_carbs"]
                total_fats = summary["total_fats"]
                page_id = summary["page_id"]
                try:
                    meals = await notion_service.get_meals_from_page(summary["page_id"])
                except Exception as e:
                    logger.error(f"Error fetching meals: {e}")
    except Exception as e:
        logger.error(f"Error fetching Notion data for dashboard: {e}")

    remaining_kcal = target_kcal - total_kcal

    # Calculate ring offset (circumference = 2 * pi * 80 ~ 502)
    circumference = 2 * math.pi * 80
    progress = min(total_kcal / target_kcal, 1.0) if target_kcal > 0 else 0
    ring_offset = circumference * (1 - progress)

    # Calculate macro percentages (against rough daily targets)
    target_protein = settings.DEFAULT_TARGET_PROTEIN
    target_carbs = 200  # approx
    target_fats = 60  # approx

    protein_pct = min(int((total_protein / target_protein) * 100), 100) if target_protein else 0
    carbs_pct = min(int((total_carbs / target_carbs) * 100), 100) if target_carbs else 0
    fats_pct = min(int((total_fats / target_fats) * 100), 100) if target_fats else 0

    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "date": selected_date.strftime("%A, %B %d, %Y"),
            "date_iso": selected_date.isoformat(),
            "prev_date": prev_date,
            "next_date": next_date,
            "is_today": is_today,
            "total_kcal": total_kcal,
            "target_kcal": target_kcal,
            "remaining_kcal": remaining_kcal,
            "total_protein": total_protein,
            "total_carbs": total_carbs,
            "total_fats": total_fats,
            "protein_pct": protein_pct,
            "carbs_pct": carbs_pct,
            "fats_pct": fats_pct,
            "ring_offset": ring_offset,
            "meals": meals,
            "page_id": page_id,
            "users": users,
            "current_user_id": user_id,
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
