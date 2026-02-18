"""Test the full LangChain nutrition agent pipeline — with error handling."""
import sys, os, asyncio, json, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_result.json")

async def main():
    results = {}
    try:
        from app.agent import run_nutrition_agent

        # Test 1: Simple food
        result1 = await run_nutrition_agent("2 boiled eggs", {})
        results["test1_2_boiled_eggs"] = result1

        # Test 2: Complex dish
        result2 = await run_nutrition_agent("paneer butter masala with 2 rotis", {})
        results["test2_paneer_masala"] = result2

        results["status"] = "SUCCESS"
    except Exception as e:
        results["status"] = "ERROR"
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"Results written to {OUTPUT_FILE}")
    print(f"Status: {results['status']}")

asyncio.run(main())
