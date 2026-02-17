"""USDA RAG Service — Numpy-based vector search over USDA FoodData Central."""

import json
import logging
import os

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)


class USDAService:
    """Vector search over USDA food database using numpy cosine similarity."""

    def __init__(self):
        self._embeddings = None
        self._metadata = None
        self._loaded = False
        self._embed_client = None

    def _load(self):
        """Lazy-load the vector store from disk."""
        if self._loaded:
            return

        store_dir = settings.USDA_CHROMA_PATH
        emb_path = os.path.join(store_dir, "embeddings.npz")
        meta_path = os.path.join(store_dir, "metadata.json")

        if not os.path.exists(emb_path) or not os.path.exists(meta_path):
            logger.warning(f"USDA vector store not found at {store_dir}. Run: python scripts/ingest_usda.py")
            self._loaded = True
            return

        try:
            data = np.load(emb_path)
            self._embeddings = data["embeddings"]

            with open(meta_path, "r", encoding="utf-8") as f:
                self._metadata = json.load(f)

            # Normalize embeddings for cosine similarity (precompute)
            norms = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
            norms[norms == 0] = 1  # avoid division by zero
            self._embeddings = self._embeddings / norms

            logger.info(f"USDA vector store loaded: {len(self._metadata)} foods, {self._embeddings.shape}")
            self._loaded = True

        except Exception as e:
            logger.error(f"Failed to load USDA vector store: {e}")
            self._loaded = True

    def _get_embedding(self, text: str) -> np.ndarray | None:
        """Get embedding for a query text using Google's embedding API."""
        try:
            from google import genai

            if self._embed_client is None:
                self._embed_client = genai.Client(api_key=settings.GEMINI_API_KEY)

            result = self._embed_client.models.embed_content(
                model="models/gemini-embedding-001",
                contents=[text],
            )
            return np.array(result.embeddings[0].values, dtype=np.float32)

        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return None

    def lookup(self, food_name: str) -> dict | None:
        """
        Search USDA database for a single food item using cosine similarity.

        Returns dict with kcal/protein/carbs/fats per 100g if match is confident,
        or None if no good match found.
        """
        self._load()

        if self._embeddings is None or self._metadata is None:
            return None

        # Get query embedding
        query_emb = self._get_embedding(food_name)
        if query_emb is None:
            return None

        # Normalize query
        query_norm = np.linalg.norm(query_emb)
        if query_norm == 0:
            return None
        query_emb = query_emb / query_norm

        # Cosine similarity (dot product since both are normalized)
        similarities = self._embeddings @ query_emb

        # Best match
        best_idx = int(np.argmax(similarities))
        best_score = float(similarities[best_idx])

        match = self._metadata[best_idx]

        # Threshold: 0.80 cosine similarity (configurable)
        threshold = settings.USDA_MATCH_THRESHOLD
        if best_score >= threshold:
            logger.info(f"USDA match: '{food_name}' → '{match['description']}' (sim={best_score:.3f})")
            return {
                "name": match["description"],
                "kcal": match.get("kcal", 0),
                "protein": match.get("protein", 0),
                "carbs": match.get("carbs", 0),
                "fats": match.get("fats", 0),
                "source": "Verified",
                "similarity": round(best_score, 3),
            }
        else:
            logger.info(f"USDA no match: '{food_name}' best='{match['description']}' (sim={best_score:.3f})")
            return None

    def lookup_as_text(self, food_name: str) -> str:
        """
        LangChain tool-friendly version.
        Returns a JSON string for the agent to parse.
        """
        result = self.lookup(food_name)
        if result:
            return json.dumps({
                "found": True,
                "usda_name": result["name"],
                "kcal_per_100g": result["kcal"],
                "protein_per_100g": result["protein"],
                "carbs_per_100g": result["carbs"],
                "fats_per_100g": result["fats"],
                "source": "USDA Verified",
            })
        else:
            return json.dumps({
                "found": False,
                "message": f"No USDA match for '{food_name}'. Estimate using your knowledge.",
            })


# Singleton
usda_service = USDAService()
