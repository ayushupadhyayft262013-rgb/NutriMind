"""Notion Sync Agent — manages Daily Log pages and Meal Table rows."""

import logging
from datetime import date, datetime
from notion_client import AsyncClient
from app.config import settings

logger = logging.getLogger(__name__)


class NotionService:
    """Handles all Notion API interactions."""

    def __init__(self):
        self.client = AsyncClient(auth=settings.NOTION_API_KEY)
        self.db_id = settings.NOTION_DAILY_LOG_DB_ID

    async def create_daily_log_database(self, parent_page_id: str) -> str:
        """Create the Daily Log database in Notion. Returns the new DB ID."""
        response = await self.client.databases.create(
            parent={"type": "page_id", "page_id": parent_page_id},
            title=[{"type": "text", "text": {"content": "🍽️ NutriMind Daily Log"}}],
            properties={
                "Date": {"title": {}},
                "User ID": {"rich_text": {}},
                "User Name": {"rich_text": {}},
                "Total Kcal": {"number": {"format": "number"}},
                "Target Kcal": {"number": {"format": "number"}},
                "Total Protein": {"number": {"format": "number"}},
                "Total Carbs": {"number": {"format": "number"}},
                "Total Fats": {"number": {"format": "number"}},
                "Status": {
                    "formula": {
                        "expression": 'if(prop("Target Kcal") - prop("Total Kcal") > 0, "Under Limit ✅", "Over Target ⚠️")'
                    }
                },
                "Remaining Kcal": {
                    "formula": {
                        "expression": 'prop("Target Kcal") - prop("Total Kcal")'
                    }
                },
            },
        )
        db_id = response["id"]
        self.db_id = db_id
        logger.info(f"Created Notion Daily Log DB: {db_id}")
        return db_id

    async def migrate_add_user_properties(self) -> bool:
        """Add User ID and User Name properties to an existing database (one-time migration)."""
        try:
            await self.client.databases.update(
                database_id=self.db_id,
                properties={
                    "User ID": {"rich_text": {}},
                    "User Name": {"rich_text": {}},
                },
            )
            logger.info("✅ Migrated Notion DB: added User ID and User Name properties")
            return True
        except Exception as e:
            logger.error(f"Migration failed: {e}", exc_info=True)
            return False

    async def get_or_create_daily_page(
        self, day: date, user_id: int, user_name: str = "Unknown", target_kcal: int = 1800
    ) -> str:
        """Get today's page for a specific user, or create it. Returns page_id."""
        date_str = day.isoformat()
        user_id_str = str(user_id)

        # Search for existing page matching date AND user
        response = await self.client.databases.query(
            database_id=self.db_id,
            filter={
                "and": [
                    {"property": "Date", "title": {"equals": date_str}},
                    {"property": "User ID", "rich_text": {"equals": user_id_str}},
                ]
            },
        )

        if response["results"]:
            return response["results"][0]["id"]

        # Create new daily page for this user
        new_page = await self.client.pages.create(
            parent={"database_id": self.db_id},
            properties={
                "Date": {"title": [{"text": {"content": date_str}}]},
                "User ID": {"rich_text": [{"text": {"content": user_id_str}}]},
                "User Name": {"rich_text": [{"text": {"content": user_name}}]},
                "Total Kcal": {"number": 0},
                "Target Kcal": {"number": target_kcal},
                "Total Protein": {"number": 0},
                "Total Carbs": {"number": 0},
                "Total Fats": {"number": 0},
            },
        )

        page_id = new_page["id"]

        # Add a heading inside the page
        await self.client.blocks.children.append(
            block_id=page_id,
            children=[
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"type": "text", "text": {"content": f"🥗 Meals Logged — {user_name}"}}]
                    },
                },
                {
                    "object": "block",
                    "type": "table",
                    "table": {
                        "table_width": 6,
                        "has_column_header": True,
                        "has_row_header": False,
                        "children": [
                            {
                                "type": "table_row",
                                "table_row": {
                                    "cells": [
                                        [{"type": "text", "text": {"content": "Item"}}],
                                        [{"type": "text", "text": {"content": "Kcal"}}],
                                        [{"type": "text", "text": {"content": "Protein (g)"}}],
                                        [{"type": "text", "text": {"content": "Carbs (g)"}}],
                                        [{"type": "text", "text": {"content": "Fats (g)"}}],
                                        [{"type": "text", "text": {"content": "Source"}}],
                                    ]
                                },
                            }
                        ],
                    },
                },
            ],
        )

        logger.info(f"Created daily page for {user_name} ({user_id_str}) on {date_str}: {page_id}")
        return page_id

    async def _find_meal_table(self, page_id: str) -> str | None:
        """Find the meal table block inside a daily page."""
        blocks = await self.client.blocks.children.list(block_id=page_id)
        for block in blocks["results"]:
            if block["type"] == "table":
                return block["id"]
        return None

    # We've removed get_meals, update_meal, and delete_meal 
    # as those are now handled sequentially by the fast local SQLite DB.
    # We only use Notion for appending logging data for archival, or export.

    async def append_meal_rows(self, page_id: str, items: list[dict]) -> None:
        """Append meal item rows to the table inside the daily page."""
        table_id = await self._find_meal_table(page_id)

        if not table_id:
            logger.warning("No meal table found in page, creating one.")
            # Fallback: add rows as bullet points
            for item in items:
                await self.client.blocks.children.append(
                    block_id=page_id,
                    children=[
                        {
                            "object": "block",
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {
                                "rich_text": [
                                    {
                                        "type": "text",
                                        "text": {
                                            "content": (
                                                f"{item['name']} — {item.get('kcal', 0)} kcal | "
                                                f"P: {item.get('protein_g', 0)}g | "
                                                f"C: {item.get('carbs_g', 0)}g | "
                                                f"F: {item.get('fats_g', 0)}g | "
                                                f"({item.get('source', 'Estimated')})"
                                            )
                                        },
                                    }
                                ]
                            },
                        }
                    ],
                )
            return

        # Append rows to the existing table
        rows = []
        for item in items:
            rows.append(
                {
                    "type": "table_row",
                    "table_row": {
                        "cells": [
                            [{"type": "text", "text": {"content": str(item.get("name", "Unknown"))}}],
                            [{"type": "text", "text": {"content": str(item.get("kcal", 0))}}],
                            [{"type": "text", "text": {"content": str(item.get("protein_g", 0))}}],
                            [{"type": "text", "text": {"content": str(item.get("carbs_g", 0))}}],
                            [{"type": "text", "text": {"content": str(item.get("fats_g", 0))}}],
                            [{"type": "text", "text": {"content": str(item.get("source", "Estimated"))}}],
                        ]
                    },
                }
            )

        await self.client.blocks.children.append(
            block_id=table_id,
            children=rows,
        )

# Singleton
notion_service = NotionService()
