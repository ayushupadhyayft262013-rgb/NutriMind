"""Async SQLite database for user profile, preferences, and state management."""

import aiosqlite
from app.config import settings

DB_PATH = settings.SQLITE_DB_PATH

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER UNIQUE NOT NULL,
    name TEXT DEFAULT '',
    weight_kg REAL DEFAULT 0,
    height_cm REAL DEFAULT 0,
    age INTEGER DEFAULT 0,
    activity_level TEXT DEFAULT 'moderate',
    goal TEXT DEFAULT 'maintain',
    target_kcal INTEGER DEFAULT 1800,
    target_protein INTEGER DEFAULT 130,
    target_carbs INTEGER DEFAULT 0,
    target_fats INTEGER DEFAULT 0,
    onboarded INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    pref_key TEXT NOT NULL,
    pref_value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(telegram_user_id, pref_key)
);

CREATE TABLE IF NOT EXISTS tracking_state (
    telegram_user_id INTEGER PRIMARY KEY,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pending_clarification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    partial_result TEXT DEFAULT '',
    question TEXT DEFAULT '',
    original_input TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db():
    """Initialize the database and create tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


async def get_db() -> aiosqlite.Connection:
    """Get a database connection."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


# ─── User Profile ────────────────────────────────────────────────────────────


async def get_user_profile(telegram_user_id: int) -> dict | None:
    """Fetch user profile by Telegram user ID."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM user_profile WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def upsert_user_profile(telegram_user_id: int, **kwargs) -> None:
    """Create or update user profile fields."""
    db = await get_db()
    try:
        existing = await get_user_profile(telegram_user_id)
        if existing:
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            vals = list(kwargs.values()) + [telegram_user_id]
            await db.execute(
                f"UPDATE user_profile SET {sets} WHERE telegram_user_id = ?", vals
            )
        else:
            kwargs["telegram_user_id"] = telegram_user_id
            cols = ", ".join(kwargs.keys())
            placeholders = ", ".join("?" for _ in kwargs)
            await db.execute(
                f"INSERT INTO user_profile ({cols}) VALUES ({placeholders})",
                list(kwargs.values()),
                f"INSERT INTO user_profile ({cols}) VALUES ({placeholders})",
                list(kwargs.values()),
            )
        await db.commit()
    finally:
        await db.close()


async def get_all_users() -> list[dict]:
    """Fetch all user profiles (id, name, telegram_user_id) for the dashboard."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT telegram_user_id, name FROM user_profile ORDER BY name ASC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


# ─── Tracking State ──────────────────────────────────────────────────────────


async def is_tracking_active(telegram_user_id: int) -> bool:
    """Check if tracking is active for a user."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT is_active FROM tracking_state WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        return bool(row["is_active"]) if row else True  # default: active
    finally:
        await db.close()


async def set_tracking_state(telegram_user_id: int, active: bool) -> None:
    """Toggle tracking state."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO tracking_state (telegram_user_id, is_active) VALUES (?, ?) "
            "ON CONFLICT(telegram_user_id) DO UPDATE SET is_active = ?",
            (telegram_user_id, int(active), int(active)),
        )
        await db.commit()
    finally:
        await db.close()


# ─── User Preferences ────────────────────────────────────────────────────────


async def get_user_preferences(telegram_user_id: int) -> dict:
    """Fetch all preferences for a user as a dict."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT pref_key, pref_value FROM user_preferences WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        rows = await cursor.fetchall()
        return {row["pref_key"]: row["pref_value"] for row in rows}
    finally:
        await db.close()


async def set_user_preference(telegram_user_id: int, key: str, value: str) -> None:
    """Set or update a user preference."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO user_preferences (telegram_user_id, pref_key, pref_value) VALUES (?, ?, ?) "
            "ON CONFLICT(telegram_user_id, pref_key) DO UPDATE SET pref_value = ?",
            (telegram_user_id, key, value, value),
        )
        await db.commit()
    finally:
        await db.close()


# ─── Pending Clarifications ──────────────────────────────────────────────────


async def save_pending_clarification(
    telegram_user_id: int, partial_result: str, question: str, original_input: str
) -> None:
    """Store a pending clarification for a user."""
    db = await get_db()
    try:
        # Remove any existing pending clarification for this user
        await db.execute(
            "DELETE FROM pending_clarification WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        await db.execute(
            "INSERT INTO pending_clarification (telegram_user_id, partial_result, question, original_input) "
            "VALUES (?, ?, ?, ?)",
            (telegram_user_id, partial_result, question, original_input),
        )
        await db.commit()
    finally:
        await db.close()


async def get_pending_clarification(telegram_user_id: int) -> dict | None:
    """Fetch the pending clarification for a user."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM pending_clarification WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def clear_pending_clarification(telegram_user_id: int) -> None:
    """Clear pending clarification after resolution."""
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM pending_clarification WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        await db.commit()
    finally:
        await db.close()
