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

    async def get_or_create_daily_page(self, day: date, target_kcal: int = 1800) -> str:
        """Get today's page or create it. Returns page_id."""
        date_str = day.isoformat()

        # Search for existing page
        response = await self.client.databases.query(
            database_id=self.db_id,
            filter={
                "property": "Date",
                "title": {"equals": date_str},
            },
        )

        if response["results"]:
            return response["results"][0]["id"]

        # Create new daily page
        new_page = await self.client.pages.create(
            parent={"database_id": self.db_id},
            properties={
                "Date": {"title": [{"text": {"content": date_str}}]},
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
                        "rich_text": [{"type": "text", "text": {"content": "🥗 Meals Logged"}}]
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

        logger.info(f"Created daily page for {date_str}: {page_id}")
        return page_id

    async def _find_meal_table(self, page_id: str) -> str | None:
        """Find the meal table block inside a daily page."""
        blocks = await self.client.blocks.children.list(block_id=page_id)
        for block in blocks["results"]:
            if block["type"] == "table":
                return block["id"]
        return None

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

    async def update_daily_totals(self, page_id: str, items: list[dict]) -> dict:
        """Update the daily page totals by fetching current values and adding new items."""
        # Get current page properties
        page = await self.client.pages.retrieve(page_id=page_id)
        current_kcal = page["properties"]["Total Kcal"]["number"] or 0
        current_protein = page["properties"]["Total Protein"]["number"] or 0
        current_carbs = page["properties"]["Total Carbs"]["number"] or 0
        current_fats = page["properties"]["Total Fats"]["number"] or 0

        # Add new items
        new_kcal = current_kcal + sum(item.get("kcal", 0) for item in items)
        new_protein = current_protein + sum(item.get("protein_g", 0) for item in items)
        new_carbs = current_carbs + sum(item.get("carbs_g", 0) for item in items)
        new_fats = current_fats + sum(item.get("fats_g", 0) for item in items)

        await self.client.pages.update(
            page_id=page_id,
            properties={
                "Total Kcal": {"number": int(new_kcal)},
                "Total Protein": {"number": round(new_protein, 1)},
                "Total Carbs": {"number": round(new_carbs, 1)},
                "Total Fats": {"number": round(new_fats, 1)},
            },
        )

        target_kcal = page["properties"]["Target Kcal"]["number"] or 1800
        return {
            "total_kcal": int(new_kcal),
            "target_kcal": int(target_kcal),
            "remaining_kcal": int(target_kcal - new_kcal),
            "total_protein": round(new_protein, 1),
            "total_carbs": round(new_carbs, 1),
            "total_fats": round(new_fats, 1),
        }

    async def get_daily_summary(self, day: date) -> dict | None:
        """Fetch today's summary from Notion."""
        date_str = day.isoformat()
        response = await self.client.databases.query(
            database_id=self.db_id,
            filter={"property": "Date", "title": {"equals": date_str}},
        )

        if not response["results"]:
            return None

        page = response["results"][0]
        props = page["properties"]
        target_kcal = props.get("Target Kcal", {}).get("number", 0) or 0
        total_kcal = props.get("Total Kcal", {}).get("number", 0) or 0

        return {
            "date": date_str,
            "total_kcal": int(total_kcal),
            "target_kcal": int(target_kcal),
            "remaining_kcal": int(target_kcal - total_kcal),
            "total_protein": props.get("Total Protein", {}).get("number", 0) or 0,
            "total_carbs": props.get("Total Carbs", {}).get("number", 0) or 0,
            "total_fats": props.get("Total Fats", {}).get("number", 0) or 0,
            "page_id": page["id"],
        }


# Singleton
notion_service = NotionService()
