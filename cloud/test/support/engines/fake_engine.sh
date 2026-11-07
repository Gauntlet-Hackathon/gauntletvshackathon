#!/bin/sh
# Fake engine for replay tests: usage fake_engine.sh <tier> <rules_version>.
# Reads one engine_input per stdin line and prints one engine_output per line (always <tier>).
tier="$1"
version="$2"
while IFS= read -r _line; do
  printf '{"outcome":"prioritized","priority_tier":"%s","rule_id":"R-P1-01","duplicate_of":null,"abstain_reason":null,"follow_up_question":null,"rules_version":"%s"}\n' "$tier" "$version"
done
