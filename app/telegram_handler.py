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
from app.notion_service import notion_service

logger = logging.getLogger(__name__)

# In-memory state for /set_targets conversational flow
_target_edit_state: dict[int, bool] = {}

# In-memory state for /edit_meals conversational flow
# Maps user_id -> {"meals": [...], "page_id": str}
_meal_edit_state: dict[int, dict] = {}


async def handle_update(update: dict) -> None:
    """Process an incoming Telegram update."""
    message = update.get("message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "")
    logger.info(f"Raw text: '{text}' (len={len(text)})")
    photo = message.get("photo")
    voice = message.get("voice")
    caption = message.get("caption", "")

    # ─── Commands ─────────────────────────────────────────────────────
    if text.startswith("/"):
        await handle_command(user_id, chat_id, text.strip())
        return

    # ─── Meal edit flow (reply to /edit_meals) ────────────────────────
    if user_id in _meal_edit_state and not text.startswith("/"):
        state = _meal_edit_state.pop(user_id)
        meals = state["meals"]
        page_id = state["page_id"]
        parts = text.strip().split()

        try:
            idx = int(parts[0]) - 1
            if idx < 0 or idx >= len(meals):
                await tg.send_message(chat_id, f"Invalid number. Pick 1-{len(meals)}", parse_mode="")
                _meal_edit_state[user_id] = state  # restore state
                return

            meal = meals[idx]
            block_id = meal["block_id"]

            if len(parts) == 1 or parts[1].lower() in ("del", "delete", "d", "remove"):
                # Delete the meal
                await notion_service.delete_meal_row(block_id)
                totals = await notion_service.recalculate_daily_totals(page_id)
                await tg.send_message(
                    chat_id,
                    f"Deleted: {meal['name']}\n\nNew total: {totals['total_kcal']} / {totals['target_kcal']} kcal",
                    parse_mode="",
                )
            elif len(parts) >= 5:
                # Edit: <num> <kcal> <protein> <carbs> <fats>
                new_data = {
                    "name": meal["name"],  # keep original name
                    "kcal": float(parts[1]),
                    "protein": float(parts[2]),
                    "carbs": float(parts[3]),
                    "fats": float(parts[4]),
                    "source": "Edited",
                }
                # If a new name is provided (6+ parts), use it
                if len(parts) >= 6:
                    new_data["name"] = " ".join(parts[5:])
                await notion_service.update_meal_row(block_id, new_data)
                totals = await notion_service.recalculate_daily_totals(page_id)
                await tg.send_message(
                    chat_id,
                    f"Updated: {new_data['name']}\n"
                    f"{new_data['kcal']} kcal | P:{new_data['protein']}g | C:{new_data['carbs']}g | F:{new_data['fats']}g\n\n"
                    f"New total: {totals['total_kcal']} / {totals['target_kcal']} kcal",
                    parse_mode="",
                )
            else:
                await tg.send_message(
                    chat_id,
                    f"To delete: reply with just the number (e.g. 1)\n"
                    f"To edit: <num> <kcal> <protein> <carbs> <fats>\n"
                    f"Example: {idx+1} 350 25 30 10",
                    parse_mode="",
                )
                _meal_edit_state[user_id] = state  # restore state
        except ValueError:
            await tg.send_message(chat_id, "Reply with a meal number. Use /edit_meals to see the list.", parse_mode="")
        return

    # ─── Target edit flow (reply to /set_targets) ─────────────────────
    if user_id in _target_edit_state and not text.startswith("/"):
        del _target_edit_state[user_id]
        parts = text.strip().split()
        if len(parts) >= 4:
            try:
                kcal, protein, carbs, fats = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                await db.upsert_user_profile(user_id, target_kcal=kcal, target_protein=protein, target_carbs=carbs, target_fats=fats)
                await tg.send_message(chat_id, f"Targets updated!\n\nCalories: {kcal} kcal\nProtein: {protein}g\nCarbs: {carbs}g\nFats: {fats}g", parse_mode="")
            except ValueError:
                await tg.send_message(chat_id, "Please send 4 numbers: kcal protein carbs fats\nExample: 1800 150 200 60", parse_mode="")
        else:
            await tg.send_message(chat_id, "Please send 4 numbers: kcal protein carbs fats\nExample: 1800 150 200 60", parse_mode="")
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

    # ─── Check for natural language delete ───────────────────────────
    if text and any(k in text.lower() for k in ["delete", "remove", "undo", "cancel"]):
        from app.gemini_service import gemini_service
        await tg.send_typing_action(chat_id)
        intent = await gemini_service.detect_intent(text)
        
        if intent.get("action") == "DELETE" and intent.get("target"):
            target = intent["target"].lower()
            # Fetch today's meals
            try:
                today = date.today()
                summary = await notion_service.get_daily_summary(today, user_id=user_id)
                if not summary:
                    await tg.send_message(chat_id, "No meals found to delete today.")
                    return
                
                meals = await notion_service.get_meals_from_page(summary["page_id"])
                # Find match (simple containment check)
                matches = [m for m in meals if target in m["name"].lower()]
                
                if not matches:
                    await tg.send_message(chat_id, f"Couldn't find a meal matching '{target}'. Check /edit_meals.")
                    return
                
                # If multiple, take the last one (most recent)
                meal_to_delete = matches[-1]
                await notion_service.delete_meal_row(meal_to_delete["block_id"])
                totals = await notion_service.recalculate_daily_totals(summary["page_id"])
                
                await tg.send_message(
                    chat_id,
                    f"🗑️ Deleted: {meal_to_delete['name']}\n\n"
                    f"🔥 New total: {totals['total_kcal']} / {totals['target_kcal']} kcal"
                )
                return
            except Exception as e:
                logger.error(f"Delete error: {e}")
                await tg.send_message(chat_id, "❌ Something went wrong trying to delete that.")
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
        # Get or create today's Notion page for this user
        today = date.today()
        target_kcal = profile.get("target_kcal", settings.DEFAULT_TARGET_KCAL)
        user_name = profile.get("name", "Unknown")
        page_id = await notion_service.get_or_create_daily_page(
            today, user_id, user_name=user_name, target_kcal=target_kcal
        )

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
            "/set_targets — View or change daily targets\n"
            "/edit_meals — Edit or delete logged meals\n"
            "/start_tracking — Resume logging\n"
            "/stop_tracking — Pause logging\n"
            "/today — Today's nutrition summary\n"
            "/profile — View your profile\n"
            "/preferences — View saved preferences\n"
            "/setup_notion — Create Notion database\n"
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
            summary = await notion_service.get_daily_summary(date.today(), user_id=user_id)
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

    elif command.startswith("/set_targets"):
        profile = await db.get_user_profile(user_id)
        if not profile or not profile.get("onboarded"):
            await tg.send_message(chat_id, "Complete onboarding first with /start")
            return

        parts = command.split()
        logger.info(f"set_targets: parts={parts}, len={len(parts)}")

        if len(parts) >= 5:
            # Inline: /set_targets 2000 150 200 60
            try:
                kcal, protein, carbs, fats = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
                await db.upsert_user_profile(user_id, target_kcal=kcal, target_protein=protein, target_carbs=carbs, target_fats=fats)
                await tg.send_message(chat_id, f"Targets updated!\n\nCalories: {kcal} kcal\nProtein: {protein}g\nCarbs: {carbs}g\nFats: {fats}g", parse_mode="")
            except ValueError:
                await tg.send_message(chat_id, "All values must be numbers.\nUsage: /set_targets 2000 150 200 60", parse_mode="")
        else:
            # Show current targets and prompt for new values
            _target_edit_state[user_id] = True
            await tg.send_message(
                chat_id,
                f"Your Current Targets:\n\n"
                f"  Calories: {profile['target_kcal']} kcal\n"
                f"  Protein: {profile['target_protein']}g\n"
                f"  Carbs: {profile['target_carbs']}g\n"
                f"  Fats: {profile['target_fats']}g\n\n"
                f"Reply with 4 numbers to update:\n"
                f"kcal protein carbs fats\n\n"
                f"Example: 1800 150 200 60",
                parse_mode="",
            )

    elif command == "/edit_meals":
        profile = await db.get_user_profile(user_id)
        if not profile or not profile.get("onboarded"):
            await tg.send_message(chat_id, "Complete onboarding first with /start")
            return

        try:
            summary = await notion_service.get_daily_summary(date.today(), user_id=user_id)
            if not summary:
                await tg.send_message(chat_id, "No meals logged today.", parse_mode="")
                return

            meals = await notion_service.get_meals_from_page(summary["page_id"])
            if not meals:
                await tg.send_message(chat_id, "No meals logged today.", parse_mode="")
                return

            _meal_edit_state[user_id] = {"meals": meals, "page_id": summary["page_id"]}

            lines = ["Today's meals:\n"]
            for i, m in enumerate(meals, 1):
                lines.append(f"{i}. {m['name']} — {m['kcal']} kcal")
                lines.append(f"   P:{m['protein']}g  C:{m['carbs']}g  F:{m['fats']}g")
            lines.append("\nTo delete: reply with the number")
            lines.append("To edit: <num> <kcal> <protein> <carbs> <fats>")
            lines.append("Example: 1 350 25 30 10")

            await tg.send_message(chat_id, "\n".join(lines), parse_mode="")
        except Exception as e:
            logger.error(f"edit_meals error: {e}", exc_info=True)
            await tg.send_message(chat_id, f"Error loading meals: {str(e)[:100]}", parse_mode="")

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

    elif command == "/migrate_notion":
        try:
            success = await notion_service.migrate_add_user_properties()
            if success:
                await tg.send_message(chat_id, "✅ Notion database migrated! User ID and User Name columns added.")
            else:
                await tg.send_message(chat_id, "❌ Migration failed. Check server logs.", parse_mode="")
        except Exception as e:
            logger.error(f"Migration error: {e}", exc_info=True)
            await tg.send_message(chat_id, f"❌ Migration error: {str(e)[:200]}", parse_mode="")

    else:
        await tg.send_message(chat_id, "Unknown command. Use /help to see available commands.")
