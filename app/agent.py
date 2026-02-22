"""
LangChain Agent for Nutrition Analysis.

Uses Gemini 2.0 Flash as the reasoning LLM with two tools:
1. usda_lookup — search USDA vector database for verified nutrition data
2. calculator  — evaluate math expressions for exact macro totals

The agent decomposes complex dishes into ingredients, looks up each in USDA,
falls back to its own knowledge if no match, and calculates totals precisely.
"""

import json
import logging
import re
import asyncio

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from app.config import settings
from app.usda_rag import usda_service
from app.gemini_service import NutritionAnalysisSchema


# ─── Tools ────────────────────────────────────────────────────────────────────

@tool
def usda_lookup(food_name: str) -> str:
    """Look up verified USDA nutrition data for a single ingredient (per 100g).
    
    Args:
        food_name: Name of a single, simple ingredient (e.g., 'egg', 'rice', 'butter').
    
    Returns:
        JSON with kcal, protein, carbs, fats per 100g if found, or a message to estimate.
    """
    return usda_service.lookup_as_text(food_name)


@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression and return the result.
    
    Use this for computing total calories and macros.
    Only supports numbers and basic operators: + - * / ( )
    
    Args:
        expression: Math expression like '78*5 + 150 + 40' or '265 * 1.5'
    """
    # Sanitize: only allow numbers, operators, spaces, dots, parens
    cleaned = re.sub(r"[^0-9+\-*/().\s]", "", expression)
    if not cleaned.strip():
        return "Error: invalid expression"
    try:
        result = eval(cleaned)
        return str(round(float(result), 2))
    except Exception as e:
        return f"Error: {e}"


# ─── Agent Setup ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are NutriMind, an expert nutrition analyst.
Your job is to analyze food inputs and return ACCURATE nutritional data.

MANDATORY WORKFLOW (follow every step):

STEP 1 — DECOMPOSE into simple ingredients:
- Break EVERY dish into its base ingredients.
- Even "simple" items need decomposition: "2 boiled eggs" → 2× whole egg.
- Complex dishes MUST be broken down: "paneer butter masala" → paneer, butter, tomato, cream, onion, oil, spices.
- Beverages: "milk tea" → milk portion + water + tea leaves (NOT pure milk! Tea is mostly water).

STEP 2 — USDA LOOKUP for each ingredient:
- Call usda_lookup for EACH individual ingredient.
- Use simple ingredient names: "egg white", "butter", "rice, white, cooked", "wheat flour", "chicken breast", "milk, whole".
- DO NOT skip this step. Even if you know the answer, call usda_lookup first.
- The tool returns nutrition data per 100g AND standard_portions (e.g., "1 large = 33g, 1 cup = 243g").

STEP 3 — DETERMINE WEIGHT using standard_portions:
- Read the "standard_portions" field from the usda_lookup response.
- Match the user's description to the correct standard portion:
  * "5 egg whites" → standard_portions shows "1 large = 33g" → 5 × 33g = 165g
  * "1 cup rice" → standard_portions shows "1 cup = 186g" → use 186g
  * "2 tbsp butter" → standard_portions shows "1 tbsp = 14.2g" → 2 × 14.2g = 28.4g
- If no standard_portions match the user's description, estimate sensibly.
- ALWAYS prefer USDA portion weights over your own guesses.

STEP 4 — CALCULATE actual nutrition using calculator:
- USDA data is per 100g. You MUST scale to the actual weight.
- Formula: actual_value = value_per_100g × (weight_in_grams / 100)
- Example: 165g egg white, USDA 52 kcal/100g → calculator("52 * 1.65") = 85.8 kcal
- Do this for ALL macros (kcal, protein, carbs, fats).
- EFFICIENCY: Calculate all macros for one ingredient in a SINGLE calculator call:
  calculator("52 * 1.65") for kcal, then calculator("10.9 * 1.65") for protein, etc.
  Or combine: calculator("52 * 1.65") → 85.8 kcal. You can also do mental math for simple cases.

STEP 5 — FALLBACK for unmatched items:
- If usda_lookup returns no match, estimate using your knowledge.
- Mark these items with source: "Estimated".
- Items with USDA data get source: "Verified".

STEP 6 — SELF-CHECK before responding:
- Verify your calculations make sense:
  * A cup of tea/coffee should be 15-50 kcal (mostly water!)
  * A single egg white ≈ 17 kcal (from USDA)
  * Pure water, tea leaves = 0 kcal
- If something seems unreasonable, recalculate.

BEVERAGE RULES (do NOT hardcode amounts — think about what the beverage actually is):
- Tea/chai: Mostly water + a small amount of milk. A standard Indian cup is 100-150ml total.
- Coffee: Mostly water/milk based on type.
- Lassi: Mostly yogurt + water + sugar.
- Juice: Look up the specific juice, not the fruit.
- NEVER treat a mixed beverage as 100% of any single ingredient.
- Always log sugar as a SEPARATE item if added.

REGIONAL DEFAULTS:
- Use Indian portion sizes when region is unclear.

YOUR FINAL RESPONSE must be ONLY this JSON (no other text):
{
  "items": [
    {
      "name": "5 Egg Whites (165g)",
      "kcal": 85.8,
      "protein_g": 17.99,
      "carbs_g": 1.2,
      "fats_g": 0.28,
      "confidence": 0.95,
      "source": "Verified"
    }
  ],
  "clarification_needed": false,
  "clarification_question": null,
  "notes": "Used USDA portion: 1 large egg white = 33g. 5 × 33g = 165g."
}"""


# ─── Sanity Validation ────────────────────────────────────────────────────────

BEVERAGE_KEYWORDS = {"tea", "chai", "coffee", "water", "juice", "nimbu", "sharbat", "chaas", "buttermilk"}

def _validate_result(result: dict) -> list[str]:
    """
    Validate agent output for unreasonable values.
    Returns list of error messages (empty = valid).
    """
    errors = []
    items = result.get("items", [])

    for item in items:
        name = item.get("name", "").lower()
        kcal = item.get("kcal", 0)
        protein = item.get("protein_g", 0)

        # Check if it's a beverage
        is_beverage = any(kw in name for kw in BEVERAGE_KEYWORDS)

        # Beverage sanity: a single serving shouldn't exceed ~150 kcal
        # (unless it's lassi/milkshake which can be higher)
        if is_beverage and kcal > 150 and "lassi" not in name and "milkshake" not in name and "shake" not in name:
            errors.append(
                f"'{item['name']}' has {kcal} kcal — beverages like tea/coffee should typically be under 100 kcal per cup. "
                f"Check: is the milk portion too large? Is the cup size realistic?"
            )

        # Single item sanity: > 1500 kcal for one item is suspicious
        if kcal > 1500:
            errors.append(
                f"'{item['name']}' has {kcal} kcal — this is unusually high for a single item. "
                f"Verify the portion weight and per-100g values."
            )

        # Protein sanity: > 100g protein in a single item is suspicious
        if protein > 100:
            errors.append(
                f"'{item['name']}' has {protein}g protein — this is unusually high. Verify calculations."
            )

        # Zero-calorie food check (except water/tea/spices)
        non_zero_exceptions = {"water", "tea", "spice", "salt", "pepper"}
        if kcal == 0 and not any(ex in name for ex in non_zero_exceptions):
            errors.append(
                f"'{item['name']}' shows 0 kcal — is this correct? Most foods have calories."
            )

    return errors


def _build_agent():
    """Build the LangChain agent executor."""
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0.2,
    )

    tools = [usda_lookup, calculator]
    llm_with_tools = llm.bind_tools(tools)

    return llm_with_tools, tools


def _parse_agent_output(text: str) -> dict:
    """Extract JSON from agent's final response (Fallback if structured output fails)."""
    cleaned = text.strip()
    if "```" in cleaned:
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    logger.error(f"Could not parse agent output manually: {text[:300]}")
    return {
        "items": [],
        "clarification_needed": True,
        "clarification_question": "I had trouble analyzing that. Could you describe the food again?",
        "notes": "Agent parse error",
    }


async def run_nutrition_agent(text: str, preferences: dict | None = None) -> dict:
    """
    Run the LangChain agent to analyze food input.

    This is the main entry point called by nutrition_engine.py.
    It handles the full agent loop: reasoning → tool calls → final answer.
    Includes sanity validation with one retry on failure.
    """
    try:
        llm_with_tools, tools = _build_agent()
        tool_map = {t.name: t for t in tools}

        # Build preference context
        pref_text = ""
        if preferences:
            pref_lines = [f"  - {k}: {v}" for k, v in preferences.items()]
            pref_text = "\nUSER PREFERENCES:\n" + "\n".join(pref_lines)

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"Analyze this food: {text}{pref_text}")
        ]

        # Run agent loop (with optional validation retry)
        for attempt in range(2):  # max 2 attempts (initial + 1 retry)
            result = await _run_agent_loop(llm_with_tools, tool_map, messages)

            # Validate the result
            errors = _validate_result(result)
            if not errors:
                return result

            if attempt == 0:
                # First failure: retry with error feedback
                error_msg = "VALIDATION ERRORS in your response:\n" + "\n".join(f"- {e}" for e in errors)
                error_msg += "\n\nPlease recalculate and fix these issues. Return corrected JSON."
                logger.warning(f"Agent validation failed, retrying: {errors}")
                messages.append(HumanMessage(content=error_msg))
            else:
                # Second attempt also failed — return anyway with a warning
                logger.warning(f"Agent validation failed after retry: {errors}")
                result.setdefault("notes", "")
                result["notes"] += f" [Warning: values may be inaccurate]"
                return result

        return result

    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        # Fallback to direct Gemini if agent fails
        logger.info("Falling back to direct Gemini analysis...")
        from app.gemini_service import gemini_service
        return await gemini_service.analyze_text(text, preferences)


async def _run_agent_loop(llm_with_tools, tool_map: dict, messages: list) -> dict:
    """Execute the agent tool-calling loop until it produces a final answer."""
    for i in range(25):  # max 25 iterations (complex dishes need many tool calls)
        response = await asyncio.to_thread(llm_with_tools.invoke, messages)
        messages.append(response)

        # Check if agent wants to call tools
        if response.tool_calls:
            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["args"]
                logger.info(f"Agent tool call [{i}]: {tool_name}({tool_args})")

                if tool_name in tool_map:
                    result = tool_map[tool_name].invoke(tool_args)
                else:
                    result = f"Unknown tool: {tool_name}"

                # Add tool result as a ToolMessage
                from langchain_core.messages import ToolMessage
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
        else:
            # No more tool calls — agent is done
            
            # Use structured output parsing
            structured_llm = llm_with_tools.with_structured_output(NutritionAnalysisSchema)
            try:
                # Ask the LLM to format the previous messages into the final schema
                final_response = await asyncio.to_thread(structured_llm.invoke, messages)
                if hasattr(final_response, "model_dump"):
                    return final_response.model_dump()
                return final_response
            except Exception as e:
                logger.error(f"Structured output parsing failed: {e}. Falling back to manual parse.")
                return _parse_agent_output(response.content)

    # If we hit max iterations, try to parse whatever we have
    logger.warning("Agent hit max iterations")
    if messages and hasattr(messages[-1], "content"):
        return _parse_agent_output(messages[-1].content)

    return {
        "items": [],
        "clarification_needed": True,
        "clarification_question": "I couldn't complete the analysis. Please try again.",
        "notes": "Max iterations reached",
    }
