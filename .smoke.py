import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "manager" / "core"))
import settings  # noqa: E402

p = settings.page()
print("keys:", sorted(p))
print("overview worst:", p["overview"]["worst"])
for r in p["overview"]["rows"]:
    print(" ", r["id"], "|", r["state"], "|", r["detail"][:72])
print("recs:", [(r["priority"], r["id"]) for r in p["recommendations"]])
print("draft:", {k: v for k, v in p["editing"]["draft"].items()
                 if k != "document"})
inst = p["installation"]
print("install:", inst["mode"], inst["version"], (inst["revision"] or "unknown")[:12])
print("verification state:", p["verification"]["state"],
      "checks", len(p["verification"]["checks"]))
print("acceptance state:", p["acceptance"]["state"])
print("access states:", {k: v.get("state") for k, v in p["access"]["intents"].items()})
print("settings rows:", len(p["settings"]))
print("sample setting:", p["settings"][0])
print("json bytes:", len(json.dumps(p)))
print("board summary:", settings.board_summary())
