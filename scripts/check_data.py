import json
m = json.load(open("data/usda_chroma/metadata.json"))
print(f"Total foods: {len(m)}")
print(f"With portions: {sum(1 for x in m if 'portions' in x)}")
print(f"IFCT foods: {sum(1 for x in m if x.get('source_db') == 'IFCT')}")
# Show a sample with portions
for x in m:
    if 'portions' in x and 'egg' in x['description'].lower():
        print(f"Sample: {x['description']} -> portions: {x['portions'][:3]}")
        break
