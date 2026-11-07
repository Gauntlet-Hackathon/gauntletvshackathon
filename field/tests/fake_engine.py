"""Fake engine for client tests. Reads engine_input on stdin, prints a canned engine_output.

Behaviour is picked with argv[1]: ok (default), echo (rule_id carries people_count),
fail, hang, garbage, invalid.
"""

import json
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
data = json.loads(sys.stdin.read())

if mode == "fail":
    print("boom: engine exploded", file=sys.stderr)
    sys.exit(3)
if mode == "hang":
    time.sleep(30)
if mode == "garbage":
    print("not json")
    sys.exit(0)
if mode == "invalid":
    print(json.dumps({"outcome": "nonsense"}))
    sys.exit(0)

out = {
    "outcome": "prioritized",
    "priority_tier": "P2",
    "rule_id": "R-P2-01",
    "duplicate_of": None,
    "abstain_reason": None,
    "follow_up_question": None,
    "rules_version": "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21",
}
if mode == "echo":
    out["rule_id"] = f"R-P2-{data['extracted']['people_count']['value']}"
print(json.dumps(out))
