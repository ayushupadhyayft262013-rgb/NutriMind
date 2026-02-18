"""
NutriMind API & Dashboard Service.
Bridging the gap between Telegram, Notion, and now a web-based "Organic Soft-Play" Dashboard.
"""

import json
import logging
import math
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db, get_all_users, get_user_profile, upsert_user_profile
from app.telegram_handler import handle_update

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─── Lifespan ────────────────────────────────────────────────────────────────


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

    yield

    # Shutdown
    logger.info("NutriMind shutting down")


# ─── App Setup ───────────────────────────────────────────────────────────────


app = FastAPI(
    title="NutriMind",
    description="AI-powered nutrition tracker bridging Telegram & Notion",
    version="2.0.0",
    lifespan=lifespan,
)

# Mount static files (if we had them, currently using CDN/Tailwind)
# app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# In-memory cache for insights
_insights_cache = {}


# ─── Core Routes ─────────────────────────────────────────────────────────────


@app.get("/")
async def root():
    """Redirect root to dashboard."""
    return RedirectResponse(url="/dashboard")


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


# ─── Dashboard Routes ────────────────────────────────────────────────────────


async def get_common_context(request: Request, user_id: int = None, date_str: str = None):
    """Helper to fetch common context data for all dashboard pages."""
    from app.notion_service import notion_service
    
    today = __import__("datetime").date.today()
    if date_str:
        try:
            selected_date = __import__("datetime").date.fromisoformat(date_str)
        except ValueError:
            selected_date = today
    else:
        selected_date = today

    prev_date = (selected_date - timedelta(days=1)).isoformat()
    next_date = (selected_date + timedelta(days=1)).isoformat()
    is_today = selected_date == today

    # User handling
    users = []
    try:
        users = await get_all_users()
    except Exception as e:
        logger.error(f"Error fetching users: {e}")

    if user_id is None and users:
        user_id = users[0]["telegram_user_id"]
    
    current_user_profile = await get_user_profile(user_id) if user_id else None

    # Default Stats
    stats = {
        "total_kcal": 0, "target_kcal": settings.DEFAULT_TARGET_KCAL,
        "total_protein": 0, "total_carbs": 0, "total_fats": 0,
        "meals": [], "page_id": ""
    }

    if user_id and settings.NOTION_DAILY_LOG_DB_ID:
        try:
            summary = await notion_service.get_daily_summary(selected_date, user_id=user_id)
            if summary:
                stats.update(summary)
                # Fetch meals if page exists
                if summary["page_id"]:
                    stats["meals"] = await notion_service.get_meals_from_page(summary["page_id"])
        except Exception as e:
            logger.error(f"Error fetching data: {e}")

    # Calculations
    remaining_kcal = stats["target_kcal"] - stats["total_kcal"]
    progress = min(stats["total_kcal"] / stats["target_kcal"], 1.0) if stats["target_kcal"] > 0 else 0
    
    # Ring offset (circumference = 2 * pi * 40 ~ 251.2)
    circumference = 2 * math.pi * 40
    ring_offset = circumference * (1 - progress)

    # Macro percentages (safe div)
    macros_sum = stats["total_protein"] + stats["total_carbs"] + stats["total_fats"]
    protein_pct = int((stats["total_protein"] / macros_sum) * 100) if macros_sum else 0
    carbs_pct = int((stats["total_carbs"] / macros_sum) * 100) if macros_sum else 0
    fats_pct = int((stats["total_fats"] / macros_sum) * 100) if macros_sum else 0

    return {
        "request": request,
        "users": users,
        "user": current_user_profile,
        "current_user_id": user_id,
        "date": selected_date.strftime("%A, %B %d"),
        "date_iso": selected_date.isoformat(),
        "prev_date": prev_date,
        "next_date": next_date,
        "is_today": is_today,
        "active_tab": "home", # default
        
        # Stats
        "total_kcal": stats["total_kcal"],
        "target_kcal": stats["target_kcal"],
        "remaining_kcal": remaining_kcal,
        "ring_offset": ring_offset,
        
        # Macros
        "total_protein": stats["total_protein"],
        "total_carbs": stats["total_carbs"],
        "total_fats": stats["total_fats"],
        "protein_pct": protein_pct,
        "carbs_pct": carbs_pct,
        "fats_pct": fats_pct,
        
        # Meals
        "meals": stats["meals"],
        "page_id": stats["page_id"],
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user_id: int = None, date: str = None):
    """Home / Daily Playground."""
    context = await get_common_context(request, user_id, date)
    context["active_tab"] = "home"
    return templates.TemplateResponse("home.html", context)


@app.get("/pantry", response_class=HTMLResponse)
async def pantry(request: Request, user_id: int = None, date: str = None):
    """The Pantry (Food Logger + Search)."""
    context = await get_common_context(request, user_id, date)
    context["active_tab"] = "pantry"
    return templates.TemplateResponse("pantry.html", context)


@app.get("/garden", response_class=HTMLResponse)
async def garden(request: Request, user_id: int = None, date: str = None):
    """The Garden (Progress & Weekly View)."""
    context = await get_common_context(request, user_id, date)
    context["active_tab"] = "garden"
    
    # Fetch weekly data for the chart
    from app.notion_service import notion_service
    import datetime as dt
    end_date = dt.date.fromisoformat(context["date_iso"])
    
    days_data = []
    try:
        summaries = await notion_service.get_weekly_summaries(end_date, user_id=context["current_user_id"])
        # Format for template
        for s in summaries:
            d_obj = dt.date.fromisoformat(s["date"])
            days_data.append({
                "weekday": d_obj.strftime("%a"),
                "kcal": s["total_kcal"],
                "target": s["target_kcal"],
                "protein": s["total_protein"],
                "is_today": s["date"] == context["date_iso"]
            })
    except Exception as e:
        logger.error(f"Garden weekly data error: {e}")

    context["days_data"] = days_data
    return templates.TemplateResponse("garden.html", context)


@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, user_id: int = None):
    """Profile Settings."""
    # Date doesn't matter much here, but we need user context
    context = await get_common_context(request, user_id)
    context["active_tab"] = "profile"
    return templates.TemplateResponse("profile.html", context)


# ─── API Endpoints ───────────────────────────────────────────────────────────


@app.get("/api/search_food")
async def search_food(q: str):
    """
    Search for food items using Nutrition Engine (USDA RAG + Gemini).
    Returns a list of plausible food items with macros.
    """
    if not q:
        return {"items": []}
    
    from app.nutrition_engine import process_food_input
    
    # We cheat a bit: we use the process_food_input with a dummy user_id (0)
    # because we just want the extraction/estimation logic, not the persistence.
    # In a real app, passing the real user_id is better for preferences.
    try:
        # Use "text" input type to trigger the RAG agent
        result = await process_food_input(
            telegram_user_id=0, 
            input_type="text", 
            text=q
        )
        return {"items": result.get("items", [])}
    except Exception as e:
        logger.error(f"Search error: {e}")
        return {"error": str(e), "items": []}


@app.post("/api/meals/add_from_search")
async def add_meal_from_search(request: Request):
    """Add a meal directly from search results."""
    from app.notion_service import notion_service
    
    try:
        data = await request.json()
        user_id = int(data.get("user_id"))
        date_iso = data.get("date")
        
        # 1. Ensure daily page exists
        date_obj = __import__("datetime").date.fromisoformat(date_iso)
        user_profile = await get_user_profile(user_id)
        user_name = user_profile["name"] if user_profile else "Unknown"
        target_kcal = user_profile["target_kcal"] if user_profile else 1800
        
        page_id = await notion_service.get_or_create_daily_page(
            date_obj, user_id, user_name, target_kcal
        )
        
        # 2. Append meal row
        items = [{
            "name": data.get("name"),
            "kcal": data.get("kcal"),
            "protein_g": data.get("protein"),
            "carbs_g": data.get("carbs"),
            "fats_g": data.get("fats")
        }]
        
        await notion_service.update_daily_totals(page_id, items)
        
        return {"ok": True}
    except Exception as e:
        logger.error(f"Add meal error: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/profile/update")
async def update_profile_stats(
    user_id: int = Form(...),
    weight: float = Form(...),
    target_kcal: int = Form(...),
    protein: int = Form(...),
    age: int = Form(...)
):
    """Update user stats from the Profile form."""
    try:
        await upsert_user_profile(
            user_id,
            weight_kg=weight,
            target_kcal=target_kcal,
            target_protein=protein,
            age=age
        )
        return RedirectResponse(f"/profile?user_id={user_id}", status_code=303)
    except Exception as e:
        logger.error(f"Profile update error: {e}")
        return HTMLResponse("Error updating profile", status_code=500)
    
@app.get("/status")
async def legacy_status_redirect(request: Request):
     target = "/dashboard"
     q = request.url.query
     if q:
         target += f"?{q}"
     return RedirectResponse(target)
