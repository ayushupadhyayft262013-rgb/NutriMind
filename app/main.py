"""
NutriMind API & Dashboard Service.
Bridging the gap between Telegram, Notion, and now a web-based "Organic Soft-Play" Dashboard.
"""

import json
import logging
import math
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db, get_all_users, get_user_profile, upsert_user_profile, get_session, add_meal, get_meals_by_date, update_meal, delete_meal
from app.telegram_handler import handle_update
from app.auth import get_current_user_from_cookie, create_access_token

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

# Optional: Add CORS if needed later
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# In-memory cache for insights
_insights_cache = {}


# ─── Core Routes ─────────────────────────────────────────────────────────────


@app.get("/")
async def root(request: Request):
    """Redirect root to dashboard if logged in, otherwise login."""
    user_id = await get_current_user_from_cookie(request)
    if user_id:
        return RedirectResponse(url="/dashboard")
    return RedirectResponse(url="/login")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Simple login page."""
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login_submit(telegram_id: int = Form(...)):
    """Mock login for prototype: just submit Telegram ID."""
    try:
        user = await get_user_profile(telegram_id)
        if not user:
             # Basic auto-provisioning for testing
             from app.database import upsert_user_profile
             await upsert_user_profile(telegram_id, name=f"User {telegram_id}")
             
        access_token = create_access_token(data={"sub": telegram_id})
        response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(key="session_token", value=access_token, httponly=True, max_age=86400 * 7)
        return response
    except Exception as e:
        logger.error(f"Login error: {e}")
        return HTMLResponse("Login failed", status_code=500)

@app.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("session_token")
    return response

@app.get("/api/switch_user")
async def switch_user(user_id: int):
    """Switch user active session."""
    try:
        user = await get_user_profile(user_id)
        if not user:
             return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
             
        access_token = create_access_token(data={"sub": user_id})
        response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(key="session_token", value=access_token, httponly=True, max_age=86400 * 7)
        return response
    except Exception as e:
        logger.error(f"Switch user error: {e}")
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

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

    # Fetch DB Meals instead of Notion Meals
    if user_id:
        try:
            db_meals = await get_meals_by_date(user_id, selected_date.isoformat())
            stats["meals"] = db_meals
            
            # Calculate totals from DB
            stats["total_kcal"] = sum(m.get("kcal", 0) for m in db_meals)
            stats["total_protein"] = sum(m.get("protein_g", 0) for m in db_meals)
            stats["total_carbs"] = sum(m.get("carbs_g", 0) for m in db_meals)
            stats["total_fats"] = sum(m.get("fats_g", 0) for m in db_meals)
            stats["page_id"] = "" # No longer strictly tied to Notion page ID for reading
            
        except Exception as e:
            logger.error(f"Error fetching data from DB: {e}")

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
async def dashboard(request: Request, date: str = None):
    """Home / Daily Playground."""
    user_id = await get_current_user_from_cookie(request)
    if not user_id:
        return RedirectResponse(url="/login")
        
    context = await get_common_context(request, user_id, date)
    context["active_tab"] = "home"
    return templates.TemplateResponse("home.html", context)


@app.get("/pantry", response_class=HTMLResponse)
async def pantry(request: Request, date: str = None):
    """The Pantry (Food Logger + Search)."""
    user_id = await get_current_user_from_cookie(request)
    if not user_id:
        return RedirectResponse(url="/login")
        
    context = await get_common_context(request, user_id, date)
    context["active_tab"] = "pantry"
    return templates.TemplateResponse("pantry.html", context)


@app.get("/garden", response_class=HTMLResponse)
async def garden(request: Request, date: str = None):
    """The Garden (Progress & Weekly View)."""
    user_id = await get_current_user_from_cookie(request)
    if not user_id:
        return RedirectResponse(url="/login")
        
    context = await get_common_context(request, user_id, date)
    context["active_tab"] = "garden"
    
    # Fetch weekly data from local DB
    import datetime as dt
    end_date = dt.date.fromisoformat(context["date_iso"])
    
    days_data = []
    try:
        for i in range(6, -1, -1):
            day = end_date - dt.timedelta(days=i)
            day_iso = day.isoformat()
            
            db_meals = await get_meals_by_date(user_id, day_iso)
            day_kcal = sum(m.get("kcal", 0) for m in db_meals)
            day_protein = sum(m.get("protein_g", 0) for m in db_meals)
            
            days_data.append({
                "weekday": day.strftime("%a"),
                "kcal": day_kcal,
                "target": context["target_kcal"],
                "protein": day_protein,
                "is_today": day_iso == context["date_iso"]
            })
    except Exception as e:
        logger.error(f"Garden weekly data error: {e}")

    context["days_data"] = days_data
    return templates.TemplateResponse("garden.html", context)


@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request):
    """Profile Settings."""
    user_id = await get_current_user_from_cookie(request)
    if not user_id:
         return RedirectResponse(url="/login")
         
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
        
        # 2. Add to Local DB First
        await add_meal(
            telegram_user_id=user_id,
            date=date_iso,
            name=data.get("name"),
            kcal=data.get("kcal"),
            protein_g=data.get("protein"),
            carbs_g=data.get("carbs"),
            fats_g=data.get("fats"),
            source="Search"
        )
        
        # 3. Fire-and-forget Notion Sync (for Phase 2 async logic. For now, we still sync synchronously or skip entirely if preferred. We'll leave synchronous for now, but background tasks are better)
        # Note: Removing synchronous notion write as requested by transition roadmap (Phase 1/2 decoupling Notion)
        # We will ONLY write to DB here for instantaneous UX. 
        
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


@app.put("/api/meals/{block_id}")
async def update_meal_api(block_id: str, request: Request):
    """Update an existing meal row."""
    # Using 'block_id' as parameter, but it's actually the local sqlite DB 'id' now 
    # since we swapped Notion for local DB.
    try:
        user_id = await get_current_user_from_cookie(request)
        if not user_id:
             return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
             
        data = await request.json()
        meal_id = int(block_id)
        
        await update_meal(
            meal_id=meal_id,
            name=data.get("name"),
            kcal=data.get("kcal"),
            protein_g=data.get("protein_g"),
            carbs_g=data.get("carbs_g"),
            fats_g=data.get("fats_g")
        )
        
        # Notion Sync decoupled
        return {"ok": True}
    except Exception as e:
        logger.error(f"Update meal error: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.delete("/api/meals/{block_id}")
async def delete_meal_api(block_id: str, request: Request):
    """Delete a meal row."""
    try:
        user_id = await get_current_user_from_cookie(request)
        if not user_id:
             return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
             
        meal_id = int(block_id)
        
        await delete_meal(meal_id)
        
        # Notion Sync decoupled
        return {"ok": True}
    except Exception as e:
        logger.error(f"Delete meal error: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    
@app.get("/status")
async def legacy_status_redirect(request: Request):
     target = "/dashboard"
     q = request.url.query
     if q:
         target += f"?{q}"
     return RedirectResponse(target)
