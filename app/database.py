"""SQLModel database for user profile, preferences, meals and state management."""

import logging
from datetime import datetime
from typing import Optional, List

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlmodel import Field, SQLModel, select, asc

from app.config import settings

logger = logging.getLogger(__name__)

# Convert standard sqlite path to async
sqlite_url = f"sqlite+aiosqlite:///{settings.SQLITE_DB_PATH}"

# Create async engine
engine = create_async_engine(sqlite_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# ─── Models ──────────────────────────────────────────────────────────────────

class UserProfile(SQLModel, table=True):
    __tablename__ = "user_profile"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_user_id: int = Field(unique=True, index=True)
    name: str = Field(default="")
    weight_kg: float = Field(default=0.0)
    height_cm: float = Field(default=0.0)
    age: int = Field(default=0)
    activity_level: str = Field(default="moderate")
    goal: str = Field(default="maintain")
    target_kcal: int = Field(default=1800)
    target_protein: int = Field(default=130)
    target_carbs: int = Field(default=0)
    target_fats: int = Field(default=0)
    onboarded: int = Field(default=0)  # Boolean modeled as int
    created_at: datetime = Field(default_factory=datetime.utcnow)

class UserPreference(SQLModel, table=True):
    __tablename__ = "user_preferences"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_user_id: int = Field(index=True)
    pref_key: str
    pref_value: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class TrackingState(SQLModel, table=True):
    __tablename__ = "tracking_state"
    
    telegram_user_id: int = Field(primary_key=True)
    is_active: int = Field(default=1)

class PendingClarification(SQLModel, table=True):
    __tablename__ = "pending_clarification"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_user_id: int = Field(index=True)
    partial_result: str = Field(default="")
    question: str = Field(default="")
    original_input: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Meal(SQLModel, table=True):
    __tablename__ = "meals"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_user_id: int = Field(index=True)
    date: str = Field(index=True)  # YYYY-MM-DD format
    name: str
    kcal: int = Field(default=0)
    protein_g: float = Field(default=0.0)
    carbs_g: float = Field(default=0.0)
    fats_g: float = Field(default=0.0)
    source: str = Field(default="Estimated")
    notion_block_id: Optional[str] = Field(default=None) # Track Notion ID if synced
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Database Dependency ─────────────────────────────────────────────────────

async def init_db():
    """Initialize the database and create tables."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

async def get_session() -> AsyncSession:
    """Dependency for retrieving the async DB session."""
    async with AsyncSessionLocal() as session:
        yield session


# ─── User Profile ────────────────────────────────────────────────────────────

async def get_user_profile(telegram_user_id: int) -> dict | None:
    """Fetch user profile by Telegram user ID."""
    async with AsyncSessionLocal() as session:
        stmt = select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        return user.model_dump() if user else None

async def upsert_user_profile(telegram_user_id: int, **kwargs) -> None:
    """Create or update user profile fields."""
    async with AsyncSessionLocal() as session:
        stmt = select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if user:
            for key, value in kwargs.items():
                setattr(user, key, value)
        else:
            user = UserProfile(telegram_user_id=telegram_user_id, **kwargs)
            session.add(user)
            
        await session.commit()

async def get_all_users() -> list[dict]:
    """Fetch all user profiles (id, name, telegram_user_id) for the dashboard."""
    async with AsyncSessionLocal() as session:
        stmt = select(UserProfile.telegram_user_id, UserProfile.name).order_by(asc(UserProfile.name))
        result = await session.execute(stmt)
        return [{"telegram_user_id": row.telegram_user_id, "name": row.name} for row in result.all()]


# ─── Tracking State ──────────────────────────────────────────────────────────

async def is_tracking_active(telegram_user_id: int) -> bool:
    """Check if tracking is active for a user."""
    async with AsyncSessionLocal() as session:
        stmt = select(TrackingState).where(TrackingState.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        state = result.scalar_one_or_none()
        return bool(state.is_active) if state else True  # default: active

async def set_tracking_state(telegram_user_id: int, active: bool) -> None:
    """Toggle tracking state."""
    async with AsyncSessionLocal() as session:
        stmt = select(TrackingState).where(TrackingState.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        state = result.scalar_one_or_none()
        
        if state:
            state.is_active = int(active)
        else:
            state = TrackingState(telegram_user_id=telegram_user_id, is_active=int(active))
            session.add(state)
            
        await session.commit()


# ─── User Preferences ────────────────────────────────────────────────────────

async def get_user_preferences(telegram_user_id: int) -> dict:
    """Fetch all preferences for a user as a dict."""
    async with AsyncSessionLocal() as session:
        stmt = select(UserPreference).where(UserPreference.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        return {pref.pref_key: pref.pref_value for pref in result.scalars().all()}

async def set_user_preference(telegram_user_id: int, key: str, value: str) -> None:
    """Set or update a user preference."""
    async with AsyncSessionLocal() as session:
        stmt = select(UserPreference).where(
            UserPreference.telegram_user_id == telegram_user_id,
            UserPreference.pref_key == key
        )
        result = await session.execute(stmt)
        pref = result.scalar_one_or_none()
        
        if pref:
            pref.pref_value = value
        else:
            pref = UserPreference(telegram_user_id=telegram_user_id, pref_key=key, pref_value=value)
            session.add(pref)
            
        await session.commit()


# ─── Pending Clarifications ──────────────────────────────────────────────────

async def save_pending_clarification(
    telegram_user_id: int, partial_result: str, question: str, original_input: str
) -> None:
    """Store a pending clarification for a user."""
    async with AsyncSessionLocal() as session:
        # Delete existing
        stmt = select(PendingClarification).where(PendingClarification.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            await session.delete(existing)
            
        # Insert new
        clarification = PendingClarification(
            telegram_user_id=telegram_user_id,
            partial_result=partial_result,
            question=question,
            original_input=original_input
        )
        session.add(clarification)
        await session.commit()

async def get_pending_clarification(telegram_user_id: int) -> dict | None:
    """Fetch the pending clarification for a user."""
    async with AsyncSessionLocal() as session:
        stmt = select(PendingClarification).where(PendingClarification.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        clarification = result.scalar_one_or_none()
        return clarification.model_dump() if clarification else None

async def clear_pending_clarification(telegram_user_id: int) -> None:
    """Clear pending clarification after resolution."""
    async with AsyncSessionLocal() as session:
        stmt = select(PendingClarification).where(PendingClarification.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        clarification = result.scalar_one_or_none()
        if clarification:
            await session.delete(clarification)
            await session.commit()

# ─── Meals ───────────────────────────────────────────────────────────────────

async def add_meal(telegram_user_id: int, date: str, name: str, kcal: int, protein_g: float, carbs_g: float, fats_g: float, source: str = "Estimated", notion_block_id: Optional[str] = None):
    """Add a new meal entry."""
    async with AsyncSessionLocal() as session:
        meal = Meal(
            telegram_user_id=telegram_user_id,
            date=date,
            name=name,
            kcal=kcal,
            protein_g=protein_g,
            carbs_g=carbs_g,
            fats_g=fats_g,
            source=source,
            notion_block_id=notion_block_id
        )
        session.add(meal)
        await session.commit()
        await session.refresh(meal)
        return meal.model_dump()

async def get_meals_by_date(telegram_user_id: int, date: str) -> list[dict]:
    """Get all meals for a specific date and user."""
    async with AsyncSessionLocal() as session:
        stmt = select(Meal).where(
            Meal.telegram_user_id == telegram_user_id,
            Meal.date == date
        )
        result = await session.execute(stmt)
        return [meal.model_dump() for meal in result.scalars().all()]
        
async def update_meal(meal_id: int, **kwargs):
    """Update a specific meal."""
    async with AsyncSessionLocal() as session:
        stmt = select(Meal).where(Meal.id == meal_id)
        result = await session.execute(stmt)
        meal = result.scalar_one_or_none()
        if meal:
            for k, v in kwargs.items():
                if hasattr(meal, k):
                    setattr(meal, k, v)
            await session.commit()
            return True
        return False

async def delete_meal(meal_id: int):
    """Delete a specific meal."""
    async with AsyncSessionLocal() as session:
        stmt = select(Meal).where(Meal.id == meal_id)
        result = await session.execute(stmt)
        meal = result.scalar_one_or_none()
        if meal:
            await session.delete(meal)
            await session.commit()
            return True
        return False
