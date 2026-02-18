import json
results = json.load(open("scripts/test_results.json"))
for r in results:
    s = "PASS" if r["passed"] else "FAIL"
    t = r["test"]
    if "error" in r:
        print(f"{s}:{t}:ERR")
    else:
        k = int(r.get("kcal", 0))
        p = round(r.get("protein", 0), 1)
        print(f"{s}:{t}:{k}kcal:{p}gP")
p = sum(1 for r in results if r["passed"])
print(f"TOTAL:{p}/{len(results)}")
