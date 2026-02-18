"""Debug USDA vector search — output to UTF-8 file."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from app.usda_rag import usda_service

usda_service._load()

lines = []
lines.append(f"Shape: {usda_service._embeddings.shape}")
lines.append(f"Foods: {len(usda_service._metadata)}")

# Find egg-related foods in metadata
lines.append("\n--- Egg-related USDA entries ---")
for i, m in enumerate(usda_service._metadata):
    if "egg" in m["description"].lower() and "eggplant" not in m["description"].lower():
        lines.append(f"  [{i}] {m['description']} | kcal={m['kcal']}")

# Test queries
queries = ["boiled egg", "egg", "rice", "butter", "chicken breast", "milk", "paneer"]
for q in queries:
    qe = usda_service._get_embedding(q)
    if qe is None:
        lines.append(f"\n'{q}': FAILED")
        continue
    qe = qe / np.linalg.norm(qe)
    sims = usda_service._embeddings @ qe
    top5 = np.argsort(sims)[-5:][::-1]
    lines.append(f"\n'{q}' top 5:")
    for idx in top5:
        m = usda_service._metadata[idx]
        lines.append(f"  sim={sims[idx]:.4f} | {m['description']} | kcal={m['kcal']}")

with open("scripts/debug_output.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("Done. Output in scripts/debug_output.txt")
