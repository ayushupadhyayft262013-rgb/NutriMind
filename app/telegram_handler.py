"""Telegram webhook handler — routes messages to the appropriate processor."""

import logging
from datetime import date

from app import database as db
from app import telegram_client as tg
from app.nutrition_engine import process_food_input, resolve_clarification
from app.notion_service import notion_service
from app.onboarding import is_onboarding, start_onboarding, handle_onboarding_message
from app.preferences import learn_from_correction
from app.config import settings

logger = logging.getLogger(__name__)


async def handle_update(update: dict) -> None:
    """Process an incoming Telegram update."""
    message = update.get("message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "")
    photo = message.get("photo")
    voice = message.get("voice")
    caption = message.get("caption", "")

    # ─── Commands ─────────────────────────────────────────────────────
    if text.startswith("/"):
        await handle_command(user_id, chat_id, text.strip())
        return

    # ─── Onboarding flow ─────────────────────────────────────────────
    if is_onboarding(user_id):
        await handle_onboarding_message(user_id, text)
        return

    # ─── Check if user is onboarded ──────────────────────────────────
    profile = await db.get_user_profile(user_id)
    if not profile or not profile.get("onboarded"):
        await start_onboarding(user_id, chat_id)
        return

    # ─── Check tracking state ────────────────────────────────────────
    if not await db.is_tracking_active(user_id):
        return  # silently ignore when tracking is off

    # ─── Check for pending clarification ─────────────────────────────
    pending = await db.get_pending_clarification(user_id)
    if pending and text:
        await tg.send_typing_action(chat_id)
        result = await resolve_clarification(user_id, text)
        if result:
            await _log_and_respond(user_id, chat_id, result, profile)
        return

    # ─── Check for preference/correction commands ────────────────────
    if text.lower().startswith("remember:") or text.lower().startswith("my "):
        await tg.send_typing_action(chat_id)
        response = await learn_from_correction(user_id, text)
        await tg.send_message(chat_id, response)
        return

    # ─── Process food input ──────────────────────────────────────────
    await tg.send_typing_action(chat_id)

    if photo:
        # Get largest photo
        largest = max(photo, key=lambda p: p.get("file_size", 0))
        file_info = await tg.get_file(largest["file_id"])
        file_path = file_info.get("file_path", "")
        image_bytes = await tg.download_file(file_path)

        # Determine mime type
        mime_type = "image/jpeg"
        if file_path.endswith(".png"):
            mime_type = "image/png"
        elif file_path.endswith(".webp"):
            mime_type = "image/webp"

        result = await process_food_input(
            user_id, "image", image_bytes=image_bytes, caption=caption, mime_type=mime_type
        )
    elif voice:
        file_info = await tg.get_file(voice["file_id"])
        file_path = file_info.get("file_path", "")
        audio_bytes = await tg.download_file(file_path)
        result = await process_food_input(user_id, "audio", audio_bytes=audio_bytes)
    elif text:
        result = await process_food_input(user_id, "text", text=text)
    else:
        return

    # ─── Handle result ───────────────────────────────────────────────
    if result.get("clarification_needed"):
        question = result.get("clarification_question", "Could you clarify?")
        await tg.send_message(chat_id, f"🤔 {question}")
        return

    await _log_and_respond(user_id, chat_id, result, profile)


async def _log_and_respond(user_id: int, chat_id: int, result: dict, profile: dict) -> None:
    """Log items to Notion and send summary to user."""
    items = result.get("items", [])
    if not items:
        await tg.send_message(chat_id, "I couldn't identify any food items. Try again?")
        return

    try:
        # Get or create today's Notion page
        today = date.today()
        target_kcal = profile.get("target_kcal", settings.DEFAULT_TARGET_KCAL)
        page_id = await notion_service.get_or_create_daily_page(today, target_kcal)

        # Append meal rows and update totals
        await notion_service.append_meal_rows(page_id, items)
        daily = await notion_service.update_daily_totals(page_id, items)

        # Build response message
        lines = ["✅ *Logged to Notion!*\n"]
        for item in items:
            source_emoji = "✓" if item.get("source") == "Verified" else "≈"
            lines.append(
                f"  {source_emoji} {item['name']} — {item.get('kcal', 0)} kcal "
                f"(P:{item.get('protein_g', 0)}g | C:{item.get('carbs_g', 0)}g | F:{item.get('fats_g', 0)}g)"
            )

        lines.append(f"\n📊 *Today's Progress:*")
        lines.append(f"  🔥 {daily['total_kcal']} / {daily['target_kcal']} kcal")
        remaining = daily["remaining_kcal"]
        if remaining > 0:
            lines.append(f"  ✅ {remaining} kcal remaining")
        else:
            lines.append(f"  ⚠️ Over by {abs(remaining)} kcal")
        lines.append(f"  🥩 Protein: {daily['total_protein']}g")

        if result.get("notes"):
            lines.append(f"\n_💡 {result['notes']}_")

        await tg.send_message(chat_id, "\n".join(lines))

    except Exception as e:
        logger.error(f"Error logging to Notion: {e}", exc_info=True)
        # Still show the estimate even if Notion fails
        lines = ["⚠️ *Couldn't sync to Notion, but here's the estimate:*\n"]
        for item in items:
            lines.append(
                f"  • {item['name']} — {item.get('kcal', 0)} kcal "
                f"(P:{item.get('protein_g', 0)}g)"
            )
        lines.append(f"\n_Error: {str(e)[:100]}_")
        await tg.send_message(chat_id, "\n".join(lines))


async def handle_command(user_id: int, chat_id: int, text: str) -> None:
    """Handle bot commands."""
    command = text.split()[0].lower().replace("@", " ").split(" ")[0]

    if command == "/start":
        profile = await db.get_user_profile(user_id)
        if profile and profile.get("onboarded"):
            await tg.send_message(
                chat_id,
                "👋 *Welcome back to NutriMind!*\n\n"
                "Just send me what you eat — text, photo, or voice — and I'll track it.\n\n"
                "Use /help to see all commands.",
            )
        else:
            await start_onboarding(user_id, chat_id)

    elif command == "/help":
        await tg.send_message(
            chat_id,
            "*🤖 NutriMind Commands:*\n\n"
            "/start — Setup or restart\n"
            "/start\\_tracking — Resume logging\n"
            "/stop\\_tracking — Pause logging\n"
            "/today — Today's nutrition summary\n"
            "/profile — View your profile\n"
            "/preferences — View saved preferences\n"
            "/setup\\_notion — Create Notion database\n"
            "/help — Show this message\n\n"
            "*How to log:*\n"
            "📝 Send text: \"Had 2 eggs and toast\"\n"
            "📸 Send a photo of your meal\n"
            "🎤 Send a voice note\n\n"
            "*Teach me:*\n"
            "Say \"Remember: my bowl is 300ml\" to save preferences",
        )

    elif command == "/start_tracking":
        await db.set_tracking_state(user_id, True)
        await tg.send_message(chat_id, "▶️ Tracking *enabled*. Send me what you eat!")

    elif command == "/stop_tracking":
        await db.set_tracking_state(user_id, False)
        await tg.send_message(chat_id, "⏸️ Tracking *paused*. Use /start\\_tracking to resume.")

    elif command == "/today":
        try:
            summary = await notion_service.get_daily_summary(date.today())
            if summary:
                remaining = summary["remaining_kcal"]
                status = "✅ Under Limit" if remaining > 0 else "⚠️ Over Target"
                await tg.send_message(
                    chat_id,
                    f"📊 *Today's Summary ({summary['date']}):*\n\n"
                    f"🔥 Calories: {summary['total_kcal']} / {summary['target_kcal']} kcal\n"
                    f"🥩 Protein: {summary['total_protein']}g\n"
                    f"🍞 Carbs: {summary['total_carbs']}g\n"
                    f"🧈 Fats: {summary['total_fats']}g\n\n"
                    f"📍 Status: {status} ({abs(remaining)} kcal {'left' if remaining > 0 else 'over'})",
                )
            else:
                await tg.send_message(chat_id, "📭 No meals logged today yet. Send me what you ate!")
        except Exception as e:
            await tg.send_message(chat_id, f"❌ Couldn't fetch summary: {str(e)[:100]}")

    elif command == "/profile":
        profile = await db.get_user_profile(user_id)
        if profile and profile.get("onboarded"):
            await tg.send_message(
                chat_id,
                f"👤 *Your Profile:*\n\n"
                f"Name: {profile['name']}\n"
                f"Weight: {profile['weight_kg']}kg\n"
                f"Height: {profile['height_cm']}cm\n"
                f"Age: {profile['age']}\n"
                f"Activity: {profile['activity_level']}\n"
                f"Goal: {profile['goal']}\n\n"
                f"🎯 *Daily Targets:*\n"
                f"Calories: {profile['target_kcal']} kcal\n"
                f"Protein: {profile['target_protein']}g\n"
                f"Carbs: {profile['target_carbs']}g\n"
                f"Fats: {profile['target_fats']}g",
            )
        else:
            await tg.send_message(chat_id, "You haven't set up your profile yet. Use /start")

    elif command == "/preferences":
        prefs = await db.get_user_preferences(user_id)
        if prefs:
            lines = ["🧠 *Your Saved Preferences:*\n"]
            for k, v in prefs.items():
                lines.append(f"  • {k}: {v}")
            await tg.send_message(chat_id, "\n".join(lines))
        else:
            await tg.send_message(
                chat_id,
                "No preferences saved yet.\n\nSay \"Remember: my bowl is 300ml\" to teach me!",
            )

    elif command == "/setup_notion":
        if not settings.NOTION_PARENT_PAGE_ID:
            await tg.send_message(
                chat_id,
                "❌ Set `NOTION_PARENT_PAGE_ID` in your .env file first.\n"
                "This is the ID of the Notion page where I'll create the database.",
            )
            return
        try:
            db_id = await notion_service.create_daily_log_database(settings.NOTION_PARENT_PAGE_ID)
            await tg.send_message(
                chat_id,
                f"✅ *Notion database created!*\n\n"
                f"Database ID: `{db_id}`\n\n"
                f"Add this to your .env as `NOTION_DAILY_LOG_DB_ID`",
            )
        except Exception as e:
            logger.error(f"Failed to create Notion DB: {e}", exc_info=True)
            await tg.send_message(chat_id, f"❌ Failed to create Notion DB: {str(e)[:200]}", parse_mode="")

    else:
        await tg.send_message(chat_id, "Unknown command. Use /help to see available commands.")
