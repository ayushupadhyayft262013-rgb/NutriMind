"""Nutrition Engine — orchestrates the Intelligent Approximation hierarchy."""

import json
import logging
from app.gemini_service import gemini_service
from app import database as db

logger = logging.getLogger(__name__)

# Try to import the LangChain agent (graceful fallback)
try:
    from app.agent import run_nutrition_agent
    AGENT_AVAILABLE = True
    logger.info("LangChain nutrition agent loaded")
except ImportError as e:
    AGENT_AVAILABLE = False
    logger.warning(f"LangChain agent not available, using direct Gemini: {e}")


async def process_food_input(
    telegram_user_id: int,
    input_type: str,  # "text", "image", "audio"
    text: str = "",
    image_bytes: bytes = b"",
    audio_bytes: bytes = b"",
    caption: str = "",
    mime_type: str = "image/jpeg",
) -> dict:
    """
    Process a food input through the Intelligent Approximation hierarchy:
    1. Load user preferences from SQLite.
    2. For TEXT: Use LangChain Agent (USDA RAG + Calculator + Gemini).
       For IMAGE/AUDIO: Use direct Gemini analysis.
    3. Check confidence levels → clarification or finalize.

    Returns:
        {
            "items": [...],
            "clarification_needed": bool,
            "clarification_question": str | None,
            "total_kcal": int,
            "total_protein": float,
            "total_carbs": float,
            "total_fats": float,
            "notes": str
        }
    """
    # Step 1: Load user preferences
    preferences = await db.get_user_preferences(telegram_user_id)

    # Step 2: Analyze based on input type
    if input_type == "text" and AGENT_AVAILABLE:
        # Use LangChain Agent (decomposes → USDA lookup → calculator)
        result = await run_nutrition_agent(text, preferences)
    elif input_type == "text":
        # Fallback: direct Gemini
        result = await gemini_service.analyze_text(text, preferences)
    elif input_type == "image":
        result = await gemini_service.analyze_image(image_bytes, caption, preferences, mime_type)
    elif input_type == "audio":
        result = await gemini_service.analyze_audio(audio_bytes, preferences)
    else:
        return {
            "items": [],
            "clarification_needed": True,
            "clarification_question": "Unsupported input type.",
            "total_kcal": 0,
            "total_protein": 0,
            "total_carbs": 0,
            "total_fats": 0,
            "notes": "",
        }

    items = result.get("items", [])

    # Step 3: Check if clarification is needed
    if result.get("clarification_needed", False):
        # Store partial result for later resolution
        await db.save_pending_clarification(
            telegram_user_id,
            partial_result=json.dumps(result),
            question=result.get("clarification_question", "Could you clarify?"),
            original_input=text or caption or "(image/audio)",
        )
        return result

    # Step 4: Calculate totals
    total_kcal = sum(item.get("kcal", 0) for item in items)
    total_protein = sum(item.get("protein_g", 0) for item in items)
    total_carbs = sum(item.get("carbs_g", 0) for item in items)
    total_fats = sum(item.get("fats_g", 0) for item in items)

    result["total_kcal"] = int(total_kcal)
    result["total_protein"] = round(total_protein, 1)
    result["total_carbs"] = round(total_carbs, 1)
    result["total_fats"] = round(total_fats, 1)

    return result


async def resolve_clarification(telegram_user_id: int, user_reply: str) -> dict | None:
    """
    Resolve a pending clarification with the user's reply.
    Re-sends to Gemini with additional context.
    """
    pending = await db.get_pending_clarification(telegram_user_id)
    if not pending:
        return None

    original_input = pending["original_input"]
    preferences = await db.get_user_preferences(telegram_user_id)

    # Re-analyze with clarification context
    combined_text = (
        f"Original food description: {original_input}\n"
        f"Clarification from user: {user_reply}\n"
        f"Please re-estimate the nutritional breakdown with this additional information."
    )

    result = await gemini_service.analyze_text(combined_text, preferences)
    await db.clear_pending_clarification(telegram_user_id)

    items = result.get("items", [])
    result["total_kcal"] = int(sum(item.get("kcal", 0) for item in items))
    result["total_protein"] = round(sum(item.get("protein_g", 0) for item in items), 1)
    result["total_carbs"] = round(sum(item.get("carbs_g", 0) for item in items), 1)
    result["total_fats"] = round(sum(item.get("fats_g", 0) for item in items), 1)

    return result
