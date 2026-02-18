"""
USDA FoodData Central → Numpy Vector Store Ingestion.

Downloads the USDA SR Legacy dataset, extracts nutrition data,
embeds food descriptions using Google gemini-embedding-001,
and stores as numpy arrays + JSON metadata.
Also parses food_portion.csv for standard serving sizes.

Usage:
    python scripts/ingest_usda.py          # skip if already exists
    python scripts/ingest_usda.py --force  # re-ingest from scratch
"""

import csv
import io
import json
import os
import sys
import zipfile
import logging
import time

import httpx
import numpy as np

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# USDA FoodData Central CSV URL (SR Legacy — largest free dataset)
USDA_CSV_URL = "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip"

# Nutrients we care about (nutrient_id → key name)
NUTRIENT_IDS = {
    "1008": "kcal",      # Energy (kcal)
    "1003": "protein",   # Protein (g)
    "1005": "carbs",     # Carbohydrate (g)
    "1004": "fats",      # Total fat (g)
}


def download_and_extract(url: str, extract_dir: str) -> str:
    """Download USDA zip and extract CSV files."""
    logger.info(f"Downloading USDA dataset from {url}...")

    response = httpx.get(url, follow_redirects=True, timeout=120)
    response.raise_for_status()

    logger.info(f"Downloaded {len(response.content) / 1024 / 1024:.1f} MB")

    os.makedirs(extract_dir, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        zf.extractall(extract_dir)
        logger.info(f"Extracted to {extract_dir}")

    return extract_dir


def parse_foods(extract_dir: str) -> dict[str, dict]:
    """Parse food.csv to get fdc_id → description mapping."""
    foods = {}

    # Find food.csv (may be in a subdirectory)
    food_csv = None
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f.lower() == "food.csv":
                food_csv = os.path.join(root, f)
                break

    if not food_csv:
        raise FileNotFoundError("food.csv not found in extracted files")

    logger.info(f"Parsing {food_csv}...")

    with open(food_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fdc_id = row.get("fdc_id", "")
            description = row.get("description", "").strip()
            if fdc_id and description:
                foods[fdc_id] = {
                    "fdc_id": fdc_id,
                    "description": description,
                    "kcal": 0.0,
                    "protein": 0.0,
                    "carbs": 0.0,
                    "fats": 0.0,
                }

    logger.info(f"Found {len(foods)} food items")
    return foods


def parse_nutrients(extract_dir: str, foods: dict[str, dict]) -> None:
    """Parse food_nutrient.csv to populate macros."""
    nutrient_csv = None
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f.lower() == "food_nutrient.csv":
                nutrient_csv = os.path.join(root, f)
                break

    if not nutrient_csv:
        raise FileNotFoundError("food_nutrient.csv not found")

    logger.info(f"Parsing nutrients from {nutrient_csv}...")

    matched = 0
    with open(nutrient_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fdc_id = row.get("fdc_id", "")
            nutrient_id = row.get("nutrient_id", "")
            amount = row.get("amount", "0")

            if fdc_id in foods and nutrient_id in NUTRIENT_IDS:
                key = NUTRIENT_IDS[nutrient_id]
                try:
                    foods[fdc_id][key] = round(float(amount), 2)
                    matched += 1
                except ValueError:
                    pass

    logger.info(f"Matched {matched} nutrient values")


def parse_portions(extract_dir: str, foods: dict[str, dict]) -> None:
    """Parse food_portion.csv to get standard portion sizes per food."""
    portion_csv = None
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f.lower() == "food_portion.csv":
                portion_csv = os.path.join(root, f)
                break

    if not portion_csv:
        logger.warning("food_portion.csv not found — skipping portion data")
        return

    logger.info(f"Parsing portions from {portion_csv}...")

    matched = 0
    with open(portion_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fdc_id = row.get("fdc_id", "")
            # SR Legacy uses 'modifier' for the description, not 'portion_description'
            desc = row.get("portion_description", "").strip()
            modifier = row.get("modifier", "").strip()
            amount = row.get("amount", "1").strip()
            gram_weight = row.get("gram_weight", "")

            if fdc_id not in foods or not gram_weight:
                continue

            try:
                grams = round(float(gram_weight), 1)
            except ValueError:
                continue

            if grams <= 0:
                continue

            # In SR Legacy, modifier has the text (e.g. "cup", "large", "tbsp")
            # while portion_description is usually empty
            label_text = desc or modifier
            if not label_text:
                continue

            # Build portion label: "1 cup", "2 tbsp", etc.
            try:
                amt = float(amount)
                if amt != 1.0:
                    label = f"{amount} {label_text}"
                else:
                    label = f"1 {label_text}"
            except ValueError:
                label = label_text

            if "portions" not in foods[fdc_id]:
                foods[fdc_id]["portions"] = []

            foods[fdc_id]["portions"].append({
                "desc": label,
                "g": grams,
            })
            matched += 1

    logger.info(f"Matched {matched} portion entries")


def generate_embeddings(foods: list[dict], api_key: str) -> np.ndarray:
    """Generate embeddings for all food descriptions using Google API."""
    from google import genai

    client = genai.Client(api_key=api_key)
    descriptions = [f["description"] for f in foods]

    all_embeddings = []
    BATCH_SIZE = 100  # Google embedding API batch limit

    for i in range(0, len(descriptions), BATCH_SIZE):
        batch = descriptions[i:i + BATCH_SIZE]
        logger.info(f"  Embedding batch {i // BATCH_SIZE + 1}/{(len(descriptions) + BATCH_SIZE - 1) // BATCH_SIZE} ({len(batch)} items)...")

        result = client.models.embed_content(
            model="models/gemini-embedding-001",
            contents=batch,
        )

        for embedding in result.embeddings:
            all_embeddings.append(embedding.values)

        # Rate limit: small delay between batches
        if i + BATCH_SIZE < len(descriptions):
            time.sleep(0.5)

    return np.array(all_embeddings, dtype=np.float32)


def save_vector_store(foods: list[dict], embeddings: np.ndarray, output_dir: str) -> None:
    """Save the vector store as numpy array + JSON metadata."""
    os.makedirs(output_dir, exist_ok=True)

    # Save embeddings as compressed numpy
    np.savez_compressed(os.path.join(output_dir, "embeddings.npz"), embeddings=embeddings)

    # Save metadata as JSON
    metadata = []
    for f in foods:
        entry = {
            "fdc_id": f["fdc_id"],
            "description": f["description"],
            "kcal": f["kcal"],
            "protein": f["protein"],
            "carbs": f["carbs"],
            "fats": f["fats"],
        }
        if f.get("portions"):
            entry["portions"] = f["portions"]
        metadata.append(entry)

    with open(os.path.join(output_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f)

    logger.info(f"✅ Saved {len(metadata)} items to {output_dir}")
    logger.info(f"   embeddings.npz: {os.path.getsize(os.path.join(output_dir, 'embeddings.npz')) / 1024 / 1024:.1f} MB")
    logger.info(f"   metadata.json:  {os.path.getsize(os.path.join(output_dir, 'metadata.json')) / 1024 / 1024:.1f} MB")


def main():
    from app.config import settings

    force = "--force" in sys.argv
    extract_dir = "data/usda_raw"
    output_dir = settings.USDA_CHROMA_PATH

    # Skip if vector store already exists (unless --force)
    emb_path = os.path.join(output_dir, "embeddings.npz")
    meta_path = os.path.join(output_dir, "metadata.json")
    if not force and os.path.exists(emb_path) and os.path.exists(meta_path):
        logger.info(f"✅ USDA vector store already exists at {output_dir}, skipping.")
        logger.info("   Use --force to re-ingest.")
        return

    # Step 1: Download
    download_and_extract(USDA_CSV_URL, extract_dir)

    # Step 2: Parse foods
    foods = parse_foods(extract_dir)

    # Step 3: Parse nutrients
    parse_nutrients(extract_dir, foods)

    # Step 4: Parse portions (NEW)
    parse_portions(extract_dir, foods)

    # Step 5: Filter foods with calorie data
    valid_foods = [v for v in foods.values() if v["kcal"] > 0]
    logger.info(f"Foods with calorie data: {len(valid_foods)}")
    foods_with_portions = sum(1 for f in valid_foods if f.get("portions"))
    logger.info(f"Foods with portion data: {foods_with_portions}")

    # Step 6: Generate embeddings
    logger.info("Generating embeddings (this may take a few minutes)...")
    embeddings = generate_embeddings(valid_foods, settings.GEMINI_API_KEY)

    # Step 7: Save vector store
    save_vector_store(valid_foods, embeddings, output_dir)

    logger.info("🎉 USDA ingestion complete (with portion data)!")


if __name__ == "__main__":
    main()
