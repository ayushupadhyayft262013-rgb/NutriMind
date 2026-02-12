"""Onboarding conversation flow for new users."""

from app import database as db
from app import telegram_client as tg

# Onboarding steps
STEP_NAME = "name"
STEP_WEIGHT = "weight"
STEP_HEIGHT = "height"
STEP_AGE = "age"
STEP_ACTIVITY = "activity"
STEP_GOAL = "goal"
STEP_CONFIRM = "confirm"

ACTIVITY_MULTIPLIERS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}

GOAL_ADJUSTMENTS = {
    "cut": -500,
    "maintain": 0,
    "bulk": 300,
}

# Track which step each user is on during onboarding
_onboarding_state: dict[int, dict] = {}


def calculate_bmr(weight_kg: float, height_cm: float, age: int) -> float:
    """Mifflin-St Jeor equation (male)."""
    return 10 * weight_kg + 6.25 * height_cm - 5 * age + 5


def calculate_tdee(bmr: float, activity_level: str) -> float:
    multiplier = ACTIVITY_MULTIPLIERS.get(activity_level, 1.55)
    return bmr * multiplier


def calculate_macros(target_kcal: int, goal: str) -> dict:
    """Calculate macro targets based on goal."""
    if goal == "cut":
        protein_pct, carb_pct, fat_pct = 0.35, 0.40, 0.25
    elif goal == "bulk":
        protein_pct, carb_pct, fat_pct = 0.30, 0.45, 0.25
    else:  # maintain
        protein_pct, carb_pct, fat_pct = 0.30, 0.40, 0.30

    return {
        "target_protein": int((target_kcal * protein_pct) / 4),
        "target_carbs": int((target_kcal * carb_pct) / 4),
        "target_fats": int((target_kcal * fat_pct) / 9),
    }


def is_onboarding(user_id: int) -> bool:
    """Check if a user is currently in the onboarding flow."""
    return user_id in _onboarding_state


async def start_onboarding(user_id: int, chat_id: int) -> None:
    """Begin the onboarding conversation."""
    _onboarding_state[user_id] = {"step": STEP_NAME, "chat_id": chat_id, "data": {}}
    await tg.send_message(
        chat_id,
        "👋 *Welcome to NutriMind!*\n\n"
        "I'm your AI nutrition tracker. Let me set up your profile.\n\n"
        "What's your name?",
    )


async def handle_onboarding_message(user_id: int, text: str) -> None:
    """Process an onboarding step reply."""
    state = _onboarding_state.get(user_id)
    if not state:
        return

    chat_id = state["chat_id"]
    step = state["step"]
    data = state["data"]

    if step == STEP_NAME:
        data["name"] = text.strip()
        state["step"] = STEP_WEIGHT
        await tg.send_message(chat_id, f"Nice to meet you, {data['name']}! 💪\n\nWhat's your current weight in *kg*?")

    elif step == STEP_WEIGHT:
        try:
            data["weight_kg"] = float(text.strip())
        except ValueError:
            await tg.send_message(chat_id, "Please enter a valid number for weight (e.g., 75.5).")
            return
        state["step"] = STEP_HEIGHT
        await tg.send_message(chat_id, "Got it. What's your height in *cm*?")

    elif step == STEP_HEIGHT:
        try:
            data["height_cm"] = float(text.strip())
        except ValueError:
            await tg.send_message(chat_id, "Please enter a valid number for height (e.g., 175).")
            return
        state["step"] = STEP_AGE
        await tg.send_message(chat_id, "And your age?")

    elif step == STEP_AGE:
        try:
            data["age"] = int(text.strip())
        except ValueError:
            await tg.send_message(chat_id, "Please enter a valid number for age.")
            return
        state["step"] = STEP_ACTIVITY
        await tg.send_message(
            chat_id,
            "What's your activity level?\n\n"
            "• `sedentary` — desk job, little exercise\n"
            "• `light` — 1-3 days/week\n"
            "• `moderate` — 3-5 days/week\n"
            "• `active` — 6-7 days/week\n"
            "• `very_active` — athlete / physical job",
        )

    elif step == STEP_ACTIVITY:
        level = text.strip().lower().replace(" ", "_")
        if level not in ACTIVITY_MULTIPLIERS:
            await tg.send_message(chat_id, "Please choose one of: sedentary, light, moderate, active, very\\_active")
            return
        data["activity_level"] = level
        state["step"] = STEP_GOAL
        await tg.send_message(
            chat_id,
            "What's your current goal?\n\n"
            "• `cut` — lose fat\n"
            "• `maintain` — stay the same\n"
            "• `bulk` — gain muscle",
        )

    elif step == STEP_GOAL:
        goal = text.strip().lower()
        if goal not in GOAL_ADJUSTMENTS:
            await tg.send_message(chat_id, "Please choose one of: cut, maintain, bulk")
            return
        data["goal"] = goal

        # Calculate targets
        bmr = calculate_bmr(data["weight_kg"], data["height_cm"], data["age"])
        tdee = calculate_tdee(bmr, data["activity_level"])
        target_kcal = int(tdee + GOAL_ADJUSTMENTS[goal])
        macros = calculate_macros(target_kcal, goal)

        data["target_kcal"] = target_kcal
        data.update(macros)

        state["step"] = STEP_CONFIRM
        await tg.send_message(
            chat_id,
            f"📊 *Your Profile Summary:*\n\n"
            f"• BMR: {int(bmr)} kcal\n"
            f"• TDEE: {int(tdee)} kcal\n"
            f"• Target: *{target_kcal} kcal/day*\n"
            f"• Protein: {macros['target_protein']}g\n"
            f"• Carbs: {macros['target_carbs']}g\n"
            f"• Fats: {macros['target_fats']}g\n\n"
            f"Send `yes` to confirm or `no` to start over.",
        )

    elif step == STEP_CONFIRM:
        if text.strip().lower() in ("yes", "y", "confirm"):
            await db.upsert_user_profile(
                user_id,
                name=data["name"],
                weight_kg=data["weight_kg"],
                height_cm=data["height_cm"],
                age=data["age"],
                activity_level=data["activity_level"],
                goal=data["goal"],
                target_kcal=data["target_kcal"],
                target_protein=data["target_protein"],
                target_carbs=data["target_carbs"],
                target_fats=data["target_fats"],
                onboarded=1,
            )
            del _onboarding_state[user_id]
            await tg.send_message(
                chat_id,
                "✅ *Profile saved!*\n\n"
                "You're all set. Just send me what you eat — text, photo, or voice note — "
                "and I'll track it for you.\n\n"
                "Use /help to see all commands.",
            )
        elif text.strip().lower() in ("no", "n"):
            del _onboarding_state[user_id]
            await start_onboarding(user_id, chat_id)
        else:
            await tg.send_message(chat_id, "Please reply `yes` or `no`.")
