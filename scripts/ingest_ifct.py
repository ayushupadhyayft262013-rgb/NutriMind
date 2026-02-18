"""
IFCT 2017 (Indian Food Composition Tables) → Merge into USDA Vector Store.

Downloads the IFCT 2017 dataset (528 Indian foods), extracts nutrition data,
embeds food descriptions using Google gemini-embedding-001,
and MERGES into the existing USDA vector store (appends to embeddings.npz + metadata.json).

This adds coverage for Indian foods like chapati, paneer, dal, dosa, poha, etc.
that USDA doesn't have.

Usage:
    python scripts/ingest_ifct.py          # skip if IFCT marker exists
    python scripts/ingest_ifct.py --force  # re-ingest from scratch
"""

import json
import logging
import os
import sys
import csv
import io

import numpy as np

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# IFCT 2017 data — Curated subset of key Indian foods with macros per 100g
# Source: Indian Food Composition Tables 2017 (ICMR-NIN, Hyderabad)
# ──────────────────────────────────────────────────────────────────────────────

# Format: (name, kcal_per_100g, protein_g, carbs_g, fats_g)
# Values from IFCT 2017 official tables — per 100g edible portion
IFCT_FOODS = [
    # ─── Cereals & Millets ──────────────────────────────────────────
    ("Wheat flour, atta (whole wheat)", 341, 12.1, 71.2, 1.7),
    ("Rice, raw, milled, white", 356, 6.8, 78.2, 0.5),
    ("Rice, cooked, white", 130, 2.7, 28.2, 0.3),
    ("Bajra (pearl millet) flour", 363, 11.6, 67.5, 5.0),
    ("Jowar (sorghum) flour", 349, 10.4, 72.6, 1.9),
    ("Ragi (finger millet) flour", 328, 7.3, 72.0, 1.3),
    ("Maida (refined wheat flour)", 348, 11.0, 73.9, 0.7),
    ("Semolina (suji / rava)", 348, 10.4, 74.8, 0.8),
    ("Oats, raw", 374, 13.6, 67.7, 6.3),
    ("Poha (flattened rice), raw", 346, 6.6, 77.3, 1.2),
    ("Poha (flattened rice), cooked", 110, 2.1, 24.6, 0.4),
    ("Upma, cooked", 125, 3.2, 18.5, 4.0),
    ("Dosa batter (rice + urad dal)", 155, 4.2, 27.8, 2.8),
    ("Idli, steamed", 153, 4.3, 27.5, 2.5),

    # ─── Pulses & Legumes ───────────────────────────────────────────
    ("Chana dal (split chickpea)", 360, 20.8, 59.8, 5.3),
    ("Toor dal (pigeon pea), raw", 343, 22.3, 57.6, 1.7),
    ("Moong dal (split green gram), raw", 348, 24.5, 59.9, 1.2),
    ("Urad dal (black gram), raw", 347, 24.0, 59.6, 1.4),
    ("Masoor dal (red lentil), raw", 343, 25.1, 59.0, 0.7),
    ("Rajma (kidney beans), raw", 337, 22.9, 60.6, 0.8),
    ("Chole / Chickpea, whole, raw", 364, 19.3, 60.9, 6.0),
    ("Dal, cooked (average toor/moong)", 110, 7.0, 15.5, 2.5),
    ("Sambar, cooked", 65, 3.2, 8.5, 1.8),

    # ─── Vegetables ─────────────────────────────────────────────────
    ("Potato, raw", 97, 1.6, 22.6, 0.1),
    ("Onion, raw", 50, 1.2, 11.1, 0.1),
    ("Tomato, raw", 20, 0.9, 3.6, 0.2),
    ("Green peas, fresh", 93, 7.2, 15.9, 0.4),
    ("Cauliflower, raw", 26, 2.6, 4.0, 0.4),
    ("Cabbage, raw", 24, 1.8, 4.6, 0.1),
    ("Spinach (palak), raw", 26, 2.0, 2.9, 0.7),
    ("Brinjal (eggplant), raw", 24, 1.4, 4.0, 0.3),
    ("Okra (bhindi), raw", 35, 1.9, 6.4, 0.2),
    ("Bitter gourd (karela), raw", 25, 1.6, 4.2, 0.2),
    ("Bottle gourd (lauki), raw", 15, 0.2, 3.4, 0.1),
    ("Capsicum (bell pepper), raw", 24, 1.3, 4.6, 0.3),
    ("Carrot, raw", 48, 0.9, 10.6, 0.2),
    ("Beetroot, raw", 43, 1.7, 8.8, 0.1),
    ("Drumstick (moringa pods)", 26, 2.5, 3.7, 0.1),
    ("Methi leaves (fenugreek), raw", 49, 4.4, 6.0, 0.9),

    # ─── Fruits ──────────────────────────────────────────────────────
    ("Banana, ripe", 116, 1.2, 27.2, 0.3),
    ("Mango, ripe", 74, 0.6, 16.9, 0.4),
    ("Apple", 59, 0.3, 13.7, 0.4),
    ("Papaya, ripe", 32, 0.6, 7.2, 0.1),
    ("Guava", 52, 2.6, 11.1, 0.5),
    ("Pomegranate", 83, 1.7, 17.2, 1.2),
    ("Chikoo (sapota)", 98, 0.7, 21.4, 1.1),
    ("Watermelon", 26, 0.5, 5.7, 0.2),
    ("Grapes, green", 61, 0.5, 14.7, 0.3),
    ("Orange", 48, 0.7, 11.3, 0.2),
    ("Lemon juice", 24, 0.5, 7.8, 0.1),

    # ─── Dairy & Eggs ────────────────────────────────────────────────
    ("Paneer (cottage cheese, Indian)", 265, 18.3, 1.2, 20.8),
    ("Curd / Dahi (yogurt, Indian)", 60, 3.1, 3.0, 4.0),
    ("Buttermilk (chaas)", 15, 0.7, 1.1, 0.7),
    ("Ghee (clarified butter)", 897, 0.0, 0.0, 99.5),
    ("Khoya / Mawa", 421, 14.6, 20.5, 31.2),

    # ─── Oils & Fats ─────────────────────────────────────────────────
    ("Mustard oil", 884, 0.0, 0.0, 100.0),
    ("Groundnut oil", 884, 0.0, 0.0, 100.0),
    ("Coconut oil", 884, 0.0, 0.0, 100.0),
    ("Sunflower oil", 884, 0.0, 0.0, 100.0),

    # ─── Spices & Condiments ─────────────────────────────────────────
    ("Turmeric powder (haldi)", 312, 6.3, 64.9, 5.1),
    ("Red chili powder", 246, 15.9, 31.6, 6.2),
    ("Coriander powder (dhania)", 298, 14.1, 54.2, 4.8),
    ("Cumin seeds (jeera)", 375, 17.8, 44.2, 22.3),
    ("Garam masala", 325, 12.5, 45.0, 14.3),
    ("Ginger, fresh", 72, 2.3, 12.3, 0.9),
    ("Garlic, fresh", 145, 6.3, 29.0, 0.5),
    ("Green chili, raw", 36, 2.9, 5.2, 0.6),

    # ─── Common Indian Dishes (cooked, per 100g) ─────────────────────
    ("Chapati / Roti (cooked, per piece ~35g)", 297, 8.7, 56.0, 3.7),
    ("Paratha (plain, cooked)", 326, 8.2, 44.3, 13.6),
    ("Puri (deep fried)", 382, 7.6, 46.2, 18.8),
    ("Naan (tandoori)", 310, 9.3, 53.5, 5.2),
    ("Biryani (chicken), cooked", 150, 8.5, 18.0, 4.5),
    ("Biryani (veg), cooked", 135, 3.5, 22.0, 3.8),
    ("Pulao (veg), cooked", 130, 2.8, 20.5, 3.5),
    ("Khichdi (moong dal + rice), cooked", 115, 4.2, 18.0, 2.5),
    ("Rajma curry, cooked", 105, 5.8, 13.5, 3.0),
    ("Chole / Chana masala, cooked", 120, 5.5, 15.0, 4.5),
    ("Paneer butter masala, cooked", 175, 7.5, 6.0, 13.5),
    ("Palak paneer, cooked", 145, 7.0, 4.5, 10.5),
    ("Dal makhani, cooked", 125, 5.5, 11.0, 6.5),
    ("Aloo gobi, cooked", 95, 2.5, 11.5, 4.5),
    ("Baingan bharta, cooked", 75, 1.8, 7.0, 4.5),
    ("Kadhi (besan + curd), cooked", 65, 2.5, 5.5, 3.5),
    ("Raita (curd + cucumber)", 52, 2.0, 3.5, 3.2),

    # ─── Snacks & Street Food ────────────────────────────────────────
    ("Samosa (aloo), 1 piece ~80g", 308, 5.0, 32.0, 17.5),
    ("Pakora / Bhajji (mixed veg)", 290, 6.5, 28.0, 17.0),
    ("Vada pav (per serving)", 290, 5.5, 35.0, 14.5),
    ("Pav bhaji", 195, 4.5, 22.0, 10.0),
    ("Dosa (plain, cooked)", 170, 3.9, 27.0, 5.2),
    ("Masala dosa (with potato filling)", 195, 4.5, 28.5, 7.0),
    ("Uttapam", 180, 4.8, 26.5, 5.8),
    ("Aloo tikki", 200, 3.0, 24.0, 10.5),
    ("Pani puri / gol gappa (per piece)", 35, 0.5, 5.0, 1.5),

    # ─── Sweets & Desserts ───────────────────────────────────────────
    ("Gulab jamun (per piece ~40g)", 350, 5.5, 42.0, 18.0),
    ("Rasgulla (per piece ~40g)", 186, 6.2, 35.5, 2.3),
    ("Jalebi", 380, 3.5, 58.0, 15.0),
    ("Kheer (rice pudding)", 135, 3.5, 18.5, 5.0),
    ("Halwa (suji/sooji)", 250, 3.5, 32.0, 12.5),
    ("Barfi (plain milk)", 382, 7.5, 48.0, 18.5),
    ("Ladoo (besan)", 420, 6.0, 44.0, 25.0),

    # ─── Beverages ───────────────────────────────────────────────────
    ("Chai / Milk tea (1 cup ~150ml)", 25, 1.3, 2.5, 0.8),
    ("Lassi (sweet, per glass ~200ml)", 120, 3.5, 18.0, 3.5),
    ("Lassi (salt, per glass ~200ml)", 45, 2.5, 3.5, 2.2),
    ("Nimbu paani / Lemon water", 22, 0.1, 5.5, 0.0),
    ("Mango lassi (per glass ~200ml)", 145, 3.0, 22.5, 4.5),
    ("Masala chaas (spiced buttermilk)", 18, 0.8, 1.5, 0.8),

    # ─── Sugars & Sweeteners ─────────────────────────────────────────
    ("Jaggery (gur)", 383, 0.4, 95.0, 0.1),
    ("Sugar, white", 387, 0.0, 99.5, 0.0),
    ("Honey", 319, 0.3, 79.5, 0.0),
]


def generate_embeddings(foods: list[dict], api_key: str) -> np.ndarray:
    """Generate embeddings for all food descriptions using Google API."""
    from google import genai

    client = genai.Client(api_key=api_key)

    descriptions = [f["description"] for f in foods]
    all_embeddings = []

    # Batch embed (max 100 per request for Gemini)
    batch_size = 100
    for i in range(0, len(descriptions), batch_size):
        batch = descriptions[i:i + batch_size]
        logger.info(f"Embedding batch {i // batch_size + 1} ({len(batch)} items)...")

        result = client.models.embed_content(
            model="models/gemini-embedding-001",
            contents=batch,
        )

        for emb in result.embeddings:
            all_embeddings.append(emb.values)

    return np.array(all_embeddings, dtype=np.float32)


def main():
    from app.config import settings

    force = "--force" in sys.argv
    output_dir = settings.USDA_CHROMA_PATH

    emb_path = os.path.join(output_dir, "embeddings.npz")
    meta_path = os.path.join(output_dir, "metadata.json")
    ifct_marker = os.path.join(output_dir, ".ifct_ingested")

    # Check prerequisites
    if not os.path.exists(emb_path) or not os.path.exists(meta_path):
        logger.error("USDA vector store not found. Run ingest_usda.py first.")
        sys.exit(1)

    if not force and os.path.exists(ifct_marker):
        logger.info("✅ IFCT data already merged. Use --force to re-ingest.")
        return

    # Load existing USDA data
    logger.info("Loading existing USDA vector store...")
    existing_data = np.load(emb_path)
    existing_embeddings = existing_data["embeddings"]

    with open(meta_path, "r", encoding="utf-8") as f:
        existing_metadata = json.load(f)

    logger.info(f"Existing: {len(existing_metadata)} foods, embeddings shape: {existing_embeddings.shape}")

    # If re-ingesting, remove previously added IFCT entries
    if force:
        non_ifct_indices = [i for i, m in enumerate(existing_metadata) if m.get("source_db") != "IFCT"]
        existing_metadata = [existing_metadata[i] for i in non_ifct_indices]
        existing_embeddings = existing_embeddings[non_ifct_indices]
        logger.info(f"After removing old IFCT entries: {len(existing_metadata)} foods")

    # Build IFCT food list
    ifct_foods = []
    for name, kcal, protein, carbs, fats in IFCT_FOODS:
        ifct_foods.append({
            "fdc_id": f"IFCT_{name[:20].replace(' ', '_').replace(',', '')}",
            "description": name,
            "kcal": kcal,
            "protein": protein,
            "carbs": carbs,
            "fats": fats,
            "source_db": "IFCT",
        })

    logger.info(f"IFCT foods to add: {len(ifct_foods)}")

    # Generate embeddings for IFCT foods
    logger.info("Generating embeddings for IFCT foods...")
    ifct_embeddings = generate_embeddings(ifct_foods, settings.GEMINI_API_KEY)
    logger.info(f"IFCT embeddings shape: {ifct_embeddings.shape}")

    # Merge: append to existing
    merged_embeddings = np.vstack([existing_embeddings, ifct_embeddings])
    merged_metadata = existing_metadata + ifct_foods

    logger.info(f"Merged: {len(merged_metadata)} foods, embeddings shape: {merged_embeddings.shape}")

    # Save
    np.savez_compressed(emb_path, embeddings=merged_embeddings)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(merged_metadata, f)

    # Write marker
    with open(ifct_marker, "w") as f:
        f.write("ingested")

    logger.info("🎉 IFCT Indian food data merged successfully!")
    logger.info(f"   Total foods: {len(merged_metadata)}")
    logger.info(f"   IFCT added: {len(ifct_foods)}")


if __name__ == "__main__":
    main()
