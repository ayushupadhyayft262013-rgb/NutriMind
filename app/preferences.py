"""Preference learning — save and apply user corrections."""

import logging
from app import database as db
from app.gemini_service import gemini_service

logger = logging.getLogger(__name__)


async def learn_from_correction(telegram_user_id: int, correction_text: str) -> str:
    """
    Parse a user correction and save it as a preference.

    Examples:
    - "My bowl is always 300ml" → pref_key: "bowl_size", pref_value: "300ml"
    - "I use skim milk, not whole" → pref_key: "default_milk", pref_value: "skim milk"
    """
    # Use Gemini to extract the preference
    prompt = (
        f"The user is correcting a food log or stating a personal preference. "
        f"Extract a key-value pair from this statement.\n\n"
        f"User said: \"{correction_text}\"\n\n"
        f"Respond in JSON: {{\"pref_key\": \"short_snake_case_key\", \"pref_value\": \"value\", \"response\": \"acknowledgment message\"}}"
    )

    from google.genai import types

    response = gemini_service.client.models.generate_content(
        model=gemini_service.model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )

    try:
        import json
        result = json.loads(response.text)
        pref_key = result.get("pref_key", "")
        pref_value = result.get("pref_value", "")
        ack = result.get("response", "Got it, I'll remember that!")

        if pref_key and pref_value:
            await db.set_user_preference(telegram_user_id, pref_key, pref_value)
            logger.info(f"Learned preference for {telegram_user_id}: {pref_key} = {pref_value}")
            return f"✅ {ack}\n\n_Saved: {pref_key} = {pref_value}_"
        else:
            return "I couldn't extract a clear preference from that. Try something like: \"My bowl is always 300ml\""
    except Exception as e:
        logger.error(f"Failed to parse preference: {e}")
        return "Sorry, I couldn't understand that correction. Try again?"
