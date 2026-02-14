"""Gemini 2.0 Flash integration for multimodal nutrition analysis."""

import json
import logging
from google import genai
from google.genai import types
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are NutriMind, an expert nutrition analyst AI. Your job is to analyze food inputs (text descriptions, images, or audio transcriptions) and return structured nutritional estimates.

RULES:
1. For every food item identified, estimate: name, calories (kcal), protein (g), carbs (g), fats (g).
2. Assign a confidence score (0.0 to 1.0) for each item.
3. Assign a source type: "Verified" (well-known standardized items), "Estimated" (your best approximation), or "User-Defined" (from user preferences).
4. For complex/homemade dishes, DECOMPOSE them into individual ingredients and sum up the macros.
5. Use Indian food portions and serving sizes as defaults when the user is from India.
6. Look for scale references in images (plates, hands, cutlery, bottles) to estimate portion sizes.
7. If confidence is below 0.7 for any item, include a "clarification_question" asking the user for specifics.
8. When user preferences are provided, use them to override defaults (e.g., if the user's "bowl" is 300ml, use that).
9. GROUP identical items into a single entry with summed macros (e.g., "5 boiled eggs" -> one item "5 Boiled Eggs" with 5x calories, NOT 5 separate items).

ALWAYS respond in this exact JSON format and NOTHING else:
{
  "items": [
    {
      "name": "Item name",
      "kcal": 250,
      "protein_g": 15.0,
      "carbs_g": 30.0,
      "fats_g": 8.0,
      "confidence": 0.85,
      "source": "Estimated"
    }
  ],
  "clarification_needed": false,
  "clarification_question": null,
  "notes": "Optional reasoning or breakdown notes"
}
"""


class GeminiService:
    """Handles all Gemini API interactions for food analysis."""

    def __init__(self):
        self._client = None
        self.model = "gemini-2.0-flash"

    @property
    def client(self):
        """Lazy-init the Gemini client on first use."""
        if self._client is None:
            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        return self._client

    def _build_preference_context(self, preferences: dict) -> str:
        """Format user preferences as context for the prompt."""
        if not preferences:
            return ""
        lines = ["USER PREFERENCES (use these to override defaults):"]
        for key, value in preferences.items():
            lines.append(f"  - {key}: {value}")
        return "\n".join(lines)

    def _parse_response(self, text: str) -> dict:
        """Parse Gemini's JSON response, handling markdown code fences."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Remove markdown code fences
            lines = cleaned.split("\n")
            # Remove first line (```json or ```) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse Gemini response: {text[:200]}")
            return {
                "items": [],
                "clarification_needed": True,
                "clarification_question": "I couldn't understand that. Could you describe the food again?",
                "notes": "Parse error",
            }

    async def analyze_text(self, text: str, preferences: dict | None = None) -> dict:
        """Analyze a text-based food description."""
        pref_context = self._build_preference_context(preferences or {})
        prompt = f"{pref_context}\n\nUSER INPUT: {text}" if pref_context else f"USER INPUT: {text}"

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.3,
                response_mime_type="application/json",
            ),
        )
        return self._parse_response(response.text)

    async def analyze_image(
        self, image_bytes: bytes, caption: str = "", preferences: dict | None = None, mime_type: str = "image/jpeg"
    ) -> dict:
        """Analyze a food image using Gemini Vision."""
        pref_context = self._build_preference_context(preferences or {})
        text_part = (
            f"{pref_context}\n\nThe user sent a food image."
            + (f" Caption: {caption}" if caption else "")
            + "\nIdentify all food items, estimate portions using visible scale references (plates, hands, cutlery), and provide nutritional breakdown."
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                text_part,
            ],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.3,
                response_mime_type="application/json",
            ),
        )
        return self._parse_response(response.text)

    async def analyze_audio(self, audio_bytes: bytes, preferences: dict | None = None) -> dict:
        """Analyze a voice note describing food."""
        pref_context = self._build_preference_context(preferences or {})
        text_part = (
            f"{pref_context}\n\nThe user sent a voice note describing what they ate. "
            "Listen to it, extract the food items and quantities, and provide nutritional breakdown."
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg"),
                text_part,
            ],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.3,
                response_mime_type="application/json",
            ),
        )
        return self._parse_response(response.text)


    async def detect_intent(self, text: str) -> dict:
        """Analyze if text is a DELETE request or regular logging."""
        prompt = (
            f"Analyze this user input: '{text}'\n"
            "Determine if the user wants to DELETE/REMOVE a previously logged meal, or usage is just logging food.\n"
            "Respond in JSON:\n"
            "{\n"
            '  "action": "DELETE" or "LOG",\n'
            '  "target": "food name to delete" (only if action is DELETE)\n'
            "}"
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
        return self._parse_response(response.text)


# Singleton
gemini_service = GeminiService()
