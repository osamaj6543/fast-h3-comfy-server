"""Ad-hoc validation: every JSON sample in docs/api-usage.md must parse."""
import json
import re

doc = open("docs/api-usage.md", encoding="utf-8").read()
# Undo the POSIX bash quote-escape so the embedded JSON parses.
text = doc.replace("'\\''", "'")

ok = bad = 0

# 1) bare ```json blocks (T2V-2 .. T2V-5)
for m in re.finditer(r"```json\n(\{.*?\})\n```", text, re.S):
    try:
        d = json.loads(m.group(1))
        ok += 1
        print(f"OK  bare block  duration={d.get('duration_seconds')} "
              f"seed={d.get('seed')} ratio={d.get('aspect_ratio')} "
              f"mp={d.get('megapixels')} dims={(d.get('width'), d.get('height'))}")
    except Exception as exc:  # noqa: BLE001
        bad += 1
        print("BAD bare block:", exc)

# 2) curl -d '{...}' JSON (T2V-1)
for m in re.finditer(r"-d '(\{.*?\})'", text, re.S):
    try:
        d = json.loads(m.group(1))
        ok += 1
        print(f"OK  curl -d     duration={d['duration_seconds']} seed={d['seed']}")
    except Exception as exc:  # noqa: BLE001
        bad += 1
        print("BAD curl -d:", exc)

# 3) multipart request={...};type=application/json (I2V-1 .. I2V-4)
for m in re.finditer(r"request=\s*(\{.*?\});type=application/json", text, re.S):
    try:
        d = json.loads(m.group(1))
        ok += 1
        print(f"OK  multipart   duration={d['duration_seconds']} "
              f"seed={d.get('seed')} dims={(d.get('width'), d.get('height'))}")
    except Exception as exc:  # noqa: BLE001
        bad += 1
        print("BAD multipart:", exc)

print()
print(f"valid JSON samples: {ok} | invalid: {bad}")
assert bad == 0, "some documented samples are not valid JSON"
print("ALL DOCUMENTED SAMPLES ARE VALID JSON")
