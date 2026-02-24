import json
from pathlib import Path

memory = json.load(open("data/tenders_memory.json", encoding="utf-8"))
print(f"Total tenders: {len(memory)}")
for tn, t in list(memory.items())[:3]:
    links = t.get("doc_links", [])
    print(f"\n{tn}: {len(links)} doc links")
    for lnk in links[:4]:
        print(f"  {lnk}")
    print(f"  closing_date: {repr(t.get('closing_date', ''))[:80]}")
    print(f"  description : {repr(t.get('description', ''))[:80]}")
