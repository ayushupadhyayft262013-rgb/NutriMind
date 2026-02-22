import asyncio
import logging
import os
import sys
from datetime import datetime

# Add app to path so we can import modules
sys.path.insert(0, os.path.abspath('.'))

from app.notion_service import notion_service
from app.database import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def migrate_data():
    """Migrate historical data from Notion to the local SQLite database."""
    logger.info("Starting Notion to Local DB Migration...")
    
    # 1. Query all pages from the Notion Daily Log Database
    # We query 100 at a time (Notion default limit) handling pagination if needed
    has_more = True
    next_cursor = None
    total_pages = 0
    total_meals_migrated = 0
    unique_users = set()

    while has_more:
        try:
            kwargs = {"database_id": notion_service.db_id}
            if next_cursor:
                kwargs["start_cursor"] = next_cursor
                
            response = await notion_service.client.databases.query(**kwargs)
            
            for page in response.get("results", []):
                total_pages += 1
                try:
                    props = page["properties"]
                    
                    # Extract User Data
                    date_str = props.get("Date", {}).get("title", [{}])[0].get("plain_text", "")
                    
                    # Handle User ID (could be rich_text or number depending on schema history)
                    user_id_prop = props.get("User ID", {})
                    if "rich_text" in user_id_prop and user_id_prop["rich_text"]:
                        telegram_user_id = int(user_id_prop["rich_text"][0]["plain_text"])
                    elif "number" in user_id_prop and user_id_prop["number"]:
                        telegram_user_id = int(user_id_prop["number"])
                    else:
                        logger.warning(f"Skipping page {page['id']} - No Telegram User ID found.")
                        continue
                        
                    user_name_prop = props.get("User Name", {})
                    user_name = "Unknown"
                    if "rich_text" in user_name_prop and user_name_prop["rich_text"]:
                        user_name = user_name_prop["rich_text"][0]["plain_text"]
                        
                    target_kcal = props.get("Target Kcal", {}).get("number", 1800) or 1800
                    
                    # Ensure User exists in local DB
                    if telegram_user_id not in unique_users:
                        existing_user = await db.get_user_profile(telegram_user_id)
                        if not existing_user:
                            await db.upsert_user_profile(
                                telegram_id=telegram_user_id,
                                name=user_name,
                                target_kcal=target_kcal,
                                onboarded=1
                            )
                            logger.info(f"Created user profile for {user_name} ({telegram_user_id})")
                        unique_users.add(telegram_user_id)

                    # We need the `get_meals_from_page` logic which we removed from `notion_service`.
                    # Let's recreate it purely for migration.
                    meals = await fetch_meals_from_page(page["id"])
                    
                    # Insert meals to DB
                    for m in meals:
                        # Ensure we don't duplicate meals if script is run twice
                        existing_meals = await db.get_meals_by_date(telegram_user_id, date_str)
                        # Check by name and kcal to avoid duplicates roughly
                        is_duplicate = any(em["name"] == m["name"] and em["kcal"] == m["kcal"] for em in existing_meals)
                        
                        if not is_duplicate:
                            await db.add_meal(
                                telegram_user_id=telegram_user_id,
                                date=date_str,
                                name=m["name"],
                                kcal=m.get("kcal", 0),
                                protein_g=m.get("protein", 0),
                                carbs_g=m.get("carbs", 0),
                                fats_g=m.get("fats", 0),
                                source=m.get("source", "Notion Migration")
                            )
                            total_meals_migrated += 1
                        
                except Exception as e:
                    logger.error(f"Error parsing Notion page {page['id']}: {e}")

            has_more = response.get("has_more", False)
            next_cursor = response.get("next_cursor", None)
            
        except Exception as e:
            logger.error(f"Notion API Error: {e}")
            break

    logger.info("=========================================")
    logger.info(f"Migration Complete!")
    logger.info(f"Processed {total_pages} Notion daily logs.")
    logger.info(f"Migrated {len(unique_users)} unique users.")
    logger.info(f"Migrated {total_meals_migrated} meals seamlessly.")
    logger.info("=========================================")

async def fetch_meals_from_page(page_id: str) -> list[dict]:
    """Helper to read meal tables from historical Notion pages."""
    try:
        # 1. Find the table block
        blocks_response = await notion_service.client.blocks.children.list(block_id=page_id)
        table_id = None
        for block in blocks_response["results"]:
            if block["type"] == "table":
                table_id = block["id"]
                break
                
        if not table_id:
            return []

        # 2. Extract rows
        rows = await notion_service.client.blocks.children.list(block_id=table_id)
        meals = []
        for row in rows["results"]:
            if row["type"] != "table_row":
                continue
                
            cells = row["table_row"]["cells"]
            if not cells or not cells[0]:
                continue
                
            name = cells[0][0]["text"]["content"] if cells[0] else ""
            if name.lower() in ("food item", "item", "food"):
                continue  # skip header row

            def safe_num(cell_list, default=0):
                try:
                    return float(cell_list[0]["text"]["content"]) if cell_list else default
                except (ValueError, IndexError, KeyError):
                    return default

            meals.append({
                "name": name,
                "kcal": safe_num(cells[1] if len(cells) > 1 else []),
                "protein": safe_num(cells[2] if len(cells) > 2 else []),
                "carbs": safe_num(cells[3] if len(cells) > 3 else []),
                "fats": safe_num(cells[4] if len(cells) > 4 else []),
                "source": cells[5][0]["text"]["content"] if len(cells) > 5 and cells[5] else "Notion Migration",
            })
        return meals
        
    except Exception as e:
        logger.error(f"Error fetching meals from page {page_id}: {e}")
        return []

if __name__ == "__main__":
    asyncio.run(migrate_data())
