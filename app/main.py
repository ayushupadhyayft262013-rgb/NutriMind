"""NutriMind — FastAPI application entry point."""

import logging
import math
import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db
from app.telegram_handler import handle_update
from app import telegram_client as tg

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


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request, user_id: int = None):
    """Mobile-optimized daily status dashboard."""
    from app.notion_service import notion_service

    today = date.today()
    total_kcal = 0
    target_kcal = settings.DEFAULT_TARGET_KCAL
    total_protein = 0
    total_carbs = 0
    total_fats = 0
    meals = []

    try:
        if settings.NOTION_DAILY_LOG_DB_ID:
            summary = await notion_service.get_daily_summary(today, user_id=user_id)
            if summary:
                total_kcal = summary["total_kcal"]
                target_kcal = summary["target_kcal"]
                total_protein = summary["total_protein"]
                total_carbs = summary["total_carbs"]
                total_fats = summary["total_fats"]
                # Fetch individual meal rows from the Notion page
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
            "date": today.strftime("%A, %B %d, %Y"),
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
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
