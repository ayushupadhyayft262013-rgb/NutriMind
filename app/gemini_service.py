"""Gemini 2.0 Flash integration for multimodal nutrition analysis."""

import json
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from loguru import logger

from app.config import settings

SYSTEM_INSTRUCTION = """You are NutriMind, an expert nutrition analyst AI. Your job is to analyze food inputs (text descriptions, images, or audio transcriptions) and return structured nutritional estimates.

RULES:
1. For every food item identified, estimate: name, calories (kcal), protein (g), carbs (g), fats (g).
2. Assign a confidence score (0.0 to 1.0) for each item.
3. Assign a source type: "Verified" (well-known standardized items), "Estimated" (your best approximation), or "User-Defined" (from user preferences).
4. For complex/homemade dishes, DECOMPOSE them into individual ingredients and sum up the macros.
5. Use Indian food portions and serving sizes as defaults when the user is from India.
6. Look for scale references in images (plates, hands, cutlery, bottles) to estimate portion sizes.
7. If confidence is below 0.7 for any item, set clarification_needed to true and ask a clarification_question.
8. When user preferences are provided, use them to override defaults (e.g., if the user's "bowl" is 300ml, use that).
9. GROUP identical items into a single entry with summed macros (e.g., "5 boiled eggs" -> one item "5 Boiled Eggs" with 5x calories, NOT 5 separate items).

ALWAYS respond matching the requested JSON schema.
"""

class FoodItemSchema(BaseModel):
    name: str = Field(description="Name of the food item")
    kcal: int = Field(description="Total calories in kcal")
    protein_g: float = Field(description="Total protein in grams")
    carbs_g: float = Field(description="Total carbs in grams")
    fats_g: float = Field(description="Total fats in grams")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0")
    source: str = Field(description="One of: Verified, Estimated, User-Defined")

class NutritionAnalysisSchema(BaseModel):
    items: list[FoodItemSchema]
    clarification_needed: bool = Field(description="True if confidence is low and more info is needed")
    clarification_question: str | None = Field(description="Question to ask if clarification is needed, else null")
    notes: str | None = Field(description="Optional reasoning or summary notes")

class IntentSchema(BaseModel):
    action: str = Field(description="Action to perform: 'DELETE' or 'LOG'")
    target: str | None = Field(description="Target to delete if action is DELETE")

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

        logger.info(f"Gemini analyze_text called with text length: {len(text)}")
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.3,
                response_mime_type="application/json",
                response_schema=NutritionAnalysisSchema,
            ),
        )
        
        # Log token usage
        if response.usage_metadata:
            logger.info(f"Tokens - prompt: {response.usage_metadata.prompt_token_count}, candidates: {response.usage_metadata.candidates_token_count}, total: {response.usage_metadata.total_token_count}")
            
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

        logger.info(f"Gemini analyze_image called. Image size: {len(image_bytes)} bytes")
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
                response_schema=NutritionAnalysisSchema,
            ),
        )
        
        # Log token usage
        if response.usage_metadata:
            logger.info(f"Tokens - prompt: {response.usage_metadata.prompt_token_count}, candidates: {response.usage_metadata.candidates_token_count}, total: {response.usage_metadata.total_token_count}")
            
        return self._parse_response(response.text)

    async def analyze_audio(self, audio_bytes: bytes, preferences: dict | None = None) -> dict:
        """Analyze a voice note describing food."""
        pref_context = self._build_preference_context(preferences or {})
        text_part = (
            f"{pref_context}\n\nThe user sent a voice note describing what they ate. "
            "Listen to it, extract the food items and quantities, and provide nutritional breakdown."
        )

        logger.info(f"Gemini analyze_audio called. Audio size: {len(audio_bytes)} bytes")
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
                response_schema=NutritionAnalysisSchema,
            ),
        )
        
        # Log token usage
        if response.usage_metadata:
            logger.info(f"Tokens - prompt: {response.usage_metadata.prompt_token_count}, candidates: {response.usage_metadata.candidates_token_count}, total: {response.usage_metadata.total_token_count}")
            
        return self._parse_response(response.text)


    async def detect_intent(self, text: str) -> dict:
        """Analyze if text is a DELETE request or regular logging."""
        prompt = (
            f"Analyze this user input: '{text}'\n"
            "Determine if the user wants to DELETE/REMOVE a previously logged meal, or usage is just logging food."
        )

        logger.info(f"Gemini detect_intent called text length: {len(text)}")
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=IntentSchema,
            ),
        )
        
        if response.usage_metadata:
            logger.info(f"Tokens - prompt: {response.usage_metadata.prompt_token_count}, candidates: {response.usage_metadata.candidates_token_count}, total: {response.usage_metadata.total_token_count}")
            
        return self._parse_response(response.text)


# Singleton
gemini_service = GeminiService()
