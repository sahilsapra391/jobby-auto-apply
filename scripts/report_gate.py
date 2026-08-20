import json
import sys

rows = json.load(open(".bodies.json"))
ok, out, unk = [], [], []
for r in rows:
    my = r.get("minYears")
    if my is None:
        unk.append(r)
    elif my <= 5:
        ok.append(r)
    else:
        out.append(r)
print("pass<=5y:%d  reject>5y:%d  noYearsStated:%d" % (len(ok), len(out), len(unk)))
which = sys.argv[1] if len(sys.argv) > 1 else "both"
groups = {"pass": [("PASS", ok)], "unstated": [("UNSTATED", unk)],
          "both": [("PASS", ok), ("UNSTATED", unk)],
          "reject": [("REJECT", out)]}[which]
for label, g in groups:
    print("\n===== %s =====" % label)
    for r in sorted(g, key=lambda x: x["updated"], reverse=True):
        print("%s | %sy | %-15.15s | %-52.52s | %-28.28s | %s" % (
            r["updated"], r.get("minYears") or "?", r["company"], r["title"],
            r["location"], r["url"]))
