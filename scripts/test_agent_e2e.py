"""
End-to-end test for the nutrition agent.
Tests various food inputs and validates the accuracy of responses.

Usage:
    python scripts/test_agent_e2e.py
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent import run_nutrition_agent

# Test cases: (input_text, expected_ranges)
# expected_ranges: dict of {field: (min, max)} for validation
TEST_CASES = [
    {
        "input": "5 egg whites",
        "expect": {
            "total_kcal": (65, 110),      # ~17 kcal each × 5 = 85 kcal
            "total_protein": (14, 22),     # ~3.6g each × 5 = 18g
        },
        "description": "Should use USDA portion: 1 large egg white = 33g"
    },
    {
        "input": "1 cup milk tea",
        "expect": {
            "total_kcal": (15, 60),        # Mostly water + small milk portion
            "total_protein": (0.5, 3),
        },
        "description": "Should NOT treat as pure milk — tea is mostly water"
    },
    {
        "input": "2 rotis with dal",
        "expect": {
            "total_kcal": (180, 550),      # ~120 kcal per roti + ~110 kcal dal
            "total_protein": (8, 28),
        },
        "description": "Should use Indian portions and find dal in IFCT data"
    },
    {
        "input": "1 banana",
        "expect": {
            "total_kcal": (80, 130),       # ~105 kcal for medium banana
            "total_protein": (0.5, 2.5),
        },
        "description": "Simple fruit — should match USDA or IFCT"
    },
    {
        "input": "paneer butter masala 1 bowl",
        "expect": {
            "total_kcal": (300, 1400),     # Rich dish, full bowl can be 500-1200+ kcal
            "total_protein": (10, 45),
        },
        "description": "Should decompose into paneer, butter, cream, tomato, etc."
    },
]


async def run_tests():
    results = []
    
    for i, tc in enumerate(TEST_CASES):
        print(f"\n{'='*60}")
        print(f"TEST {i+1}: {tc['input']}")
        print(f"Description: {tc['description']}")
        print(f"{'='*60}")
        
        try:
            result = await run_nutrition_agent(tc["input"])
            
            items = result.get("items", [])
            total_kcal = sum(item.get("kcal", 0) for item in items)
            total_protein = sum(item.get("protein_g", 0) for item in items)
            
            print(f"\nAgent Response:")
            for item in items:
                print(f"  - {item.get('name', '?')}: {item.get('kcal', 0)} kcal, "
                      f"{item.get('protein_g', 0)}g protein, "
                      f"{item.get('source', '?')}")
            
            if result.get("notes"):
                print(f"  Notes: {result['notes']}")
            
            print(f"\nTotals: {total_kcal:.1f} kcal, {total_protein:.1f}g protein")
            
            # Validate ranges
            passed = True
            expects = tc["expect"]
            
            if "total_kcal" in expects:
                lo, hi = expects["total_kcal"]
                if lo <= total_kcal <= hi:
                    print(f"  ✅ Calories in range ({lo}-{hi})")
                else:
                    print(f"  ❌ Calories OUT OF RANGE: {total_kcal:.1f} (expected {lo}-{hi})")
                    passed = False
            
            if "total_protein" in expects:
                lo, hi = expects["total_protein"]
                if lo <= total_protein <= hi:
                    print(f"  ✅ Protein in range ({lo}-{hi}g)")
                else:
                    print(f"  ❌ Protein OUT OF RANGE: {total_protein:.1f}g (expected {lo}-{hi}g)")
                    passed = False
            
            results.append({"test": tc["input"], "passed": passed, "kcal": total_kcal, "protein": total_protein})
            
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            results.append({"test": tc["input"], "passed": False, "error": str(e)})
    
    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY: {sum(1 for r in results if r['passed'])}/{len(results)} tests passed")
    print(f"{'='*60}")
    for r in results:
        status = "✅" if r["passed"] else "❌"
        if "error" in r:
            print(f"  {status} {r['test']}: ERROR - {r['error']}")
        else:
            print(f"  {status} {r['test']}: {r.get('kcal', 0):.0f} kcal, {r.get('protein', 0):.1f}g protein")
    
    # Save results to JSON
    with open("scripts/test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to scripts/test_results.json")


if __name__ == "__main__":
    asyncio.run(run_tests())
