"""Times each local Gemma model on the same set of messages, for the write-up.

Usage:  python bench.py gemma4:e2b gemma4:e4b
Writes bench_results.csv with every output, so you can rate quality 1-5 by hand.
"""
import csv
import sys
import time

import llm
import prompts

PROFILE = {"name": "the reader", "craft": "Tarot readings", "city": "Mumbai",
           "voice_samples": open("sample_messages.txt", encoding="utf-8").read()}
CASES = [
    ("After a session", "Client: Priya\nService: Soul Spread\nSession notes: confused about moving to Pune for a job, drew the Star\nNext available: Saturday 3 PM at Blondie Khar"),
    ("After a session", "Client: Arjun\nService: Clarity Drop\nSession notes: breakup last month, wants closure\nNext available: Sunday at Janu"),
    ("Booking confirmation", "Client: Sana\nService: Energy Snapshot (15 min)\nSession: Sunday 4:30 PM at Janu"),
    ("Gentle nudge", "Client: Dev\nService: Spirit Echo\nSession notes: last reading 5 weeks ago about a business idea\nNext available: two slots Saturday at Blondie Khar"),
    ("Payment reminder", "Client: Nikhil\nService: Deep private reading\nSession: yesterday at Blondie House\nUPI ID: reader@upi"),
]
LANGS = ["English", "Hinglish"]

models = sys.argv[1:] or [llm.MODEL]
rows = []
for model in models:
    llm.MODEL = model
    llm.generate("Reply with OK.", "OK")  # warm-up: loads the model into memory
    for lang in LANGS:
        for kind, user in CASES:
            t = time.perf_counter()
            out = llm.generate(prompts.message_prompt(PROFILE, lang, kind), user)
            secs = time.perf_counter() - t
            rows.append({"model": model, "language": lang, "type": kind, "seconds": round(secs, 1),
                         "output": out, "quality_1_to_5": ""})
            print(f"{model:12} {lang:9} {kind:22} {secs:5.1f}s")

with open("bench_results.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

for model in models:
    secs = [r["seconds"] for r in rows if r["model"] == model]
    print(f"{model}: average {sum(secs) / len(secs):.1f}s per message over {len(secs)} messages")
print("Saved bench_results.csv. Open it and fill in quality_1_to_5 for each message.")
