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
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.config import settings
from app.usda_rag import usda_service

logger = logging.getLogger(__name__)


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
- Break EVERY dish into its base ingredients with weights in grams.
- Even "simple" items need decomposition: "2 boiled eggs" → "egg, whole, cooked, hard-boiled, 100g" (2 eggs × 50g each).
- Complex dishes MUST be broken down: "paneer butter masala" → paneer (100g), butter (15g), tomato (100g), cream (30g), onion (50g), oil (10g), spices (5g).
- "2 rotis" → wheat flour (60g × 2 = 120g cooked weight).

STEP 2 — USDA LOOKUP for each ingredient:
- Call usda_lookup for EACH individual ingredient.
- Use simple ingredient names like: "egg", "butter", "rice, white, cooked", "wheat flour", "chicken breast".
- DO NOT skip this step. Even if you know the answer, call usda_lookup first.

STEP 3 — CALCULATE actual portions using calculator:
- USDA data is ALWAYS per 100g. You MUST use the calculator tool to adjust.
- Example: 2 eggs = 100g total. USDA says 155 kcal/100g → calculator("155 * 1.0") = 155 kcal.
- Example: 15g butter. USDA says 717 kcal/100g → calculator("717 * 0.15") = 107.55 kcal.
- Do this for ALL macros (kcal, protein, carbs, fats).

STEP 4 — FALLBACK for unmatched items:
- If usda_lookup returns no match, estimate using your own knowledge.
- Mark these items with source: "Estimated".
- Items with USDA data get source: "Verified".

STEP 5 — GROUP and RESPOND:
- Group identical ingredients into one item with summed macros.
- Set confidence to 0.95 for USDA-verified items, 0.75-0.85 for estimated items.

IMPORTANT DEFAULTS:
- 1 boiled egg ≈ 50g (without shell)
- 1 roti ≈ 60g cooked (30g dry wheat flour)
- 1 bowl rice ≈ 150g cooked
- 1 glass milk ≈ 250ml ≈ 258g
- Use Indian portion sizes when region is unclear.

YOUR FINAL RESPONSE must be ONLY this JSON (no other text):
{
  "items": [
    {
      "name": "2 Boiled Eggs (100g)",
      "kcal": 155,
      "protein_g": 12.56,
      "carbs_g": 1.12,
      "fats_g": 10.58,
      "confidence": 0.95,
      "source": "Verified"
    }
  ],
  "clarification_needed": false,
  "clarification_question": null,
  "notes": "Decomposition: 2 eggs × 50g = 100g. USDA: 155 kcal/100g."
}"""


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
    """Extract JSON from agent's final response."""
    # Try to find JSON in the text
    # First, try direct parse
    cleaned = text.strip()

    # Remove markdown code fences if present
    if "```" in cleaned:
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    # Try to find JSON object
    json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    logger.error(f"Could not parse agent output: {text[:300]}")
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

        # Agent loop (max 8 iterations to prevent infinite loops)
        for i in range(8):
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
                final_text = response.content
                return _parse_agent_output(final_text)

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

    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        # Fallback to direct Gemini if agent fails
        logger.info("Falling back to direct Gemini analysis...")
        from app.gemini_service import gemini_service
        return await gemini_service.analyze_text(text, preferences)
