"""Quick test: What does our USDA RAG return for key items?"""
from app.usda_rag import usda_service
import json

tests = ["egg white", "egg white, cooked", "egg, whole", "milk, whole", "milk", "tea"]
for t in tests:
    result = usda_service.lookup(t)
    print(f"=== {t} ===")
    print(json.dumps(result, indent=2) if result else "NO MATCH")
    print()
