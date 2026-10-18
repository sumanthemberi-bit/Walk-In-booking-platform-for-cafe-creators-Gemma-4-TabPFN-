"""Does Gemma follow the rules? Runs every test case through four prompt versions and checks each draft.

Usage (Ollama running):   python evaluate_messages.py
                          python evaluate_messages.py gemma4:e2b      (another model)
Writes eval_messages.csv (every draft + which checks failed) and prints a table for the write-up.

The checks are automatic and strict, so they catch what a quick read misses:
  length      2 to 4 sentences
  emoji       at most one
  hashtags    none
  price       no ₹/Rs amount that wasn't in the input
  time        no clock time that wasn't in the input
  private     doesn't repeat a sensitive note word for word (4+ words in a row)
  language    Hindi in Devanagari; Hinglish in Latin letters with Hindi words; English without Hindi
"""
import os as _os
_os.chdir(_os.path.dirname(_os.path.abspath(__file__)))  # run from this script's folder, wherever it was started from

import csv
import re
import sys
import time

import guard
import llm
import prompts

VOICE = open("sample_messages.txt", encoding="utf-8").read()
PROFILE = {"name": "Aarav", "craft": "Tarot readings", "city": "Mumbai", "voice_samples": VOICE}

# kind, client, service, notes (private), next spot / booking, UPI, sensitive phrases that must not be quoted
CASES = [
    ("After a session", "Priya", "Soul Spread", "confused about moving to Pune for a new job, drew the Star", "Saturday 3 PM at Blondie Khar", "", ["moving to pune for a new job"]),
    ("After a session", "Arjun", "Clarity Drop", "breakup last month, still hoping she comes back", "Sunday at Janu", "", ["still hoping she comes back"]),
    ("After a session", "Sana", "Love reading", "thinking of leaving her husband, very emotional", "", "", ["thinking of leaving her husband", "leaving her husband"]),
    ("After a session", "Dev", "Career spread", "worried about his dad's heart surgery next week", "Saturday 4 PM at Blondie Khar", "", ["dad's heart surgery", "heart surgery next week"]),
    ("Booking confirmation", "Rohan", "Energy Snapshot", "", "Saturday 5:30 PM at Blondie Khar", "", []),
    ("Booking confirmation", "Meera", "Soul Spread", "", "Sunday 4 PM at Janu", "", []),
    ("Gentle nudge", "Kabir", "Clarity Drop", "last reading 5 weeks ago about starting a cafe", "two slots Saturday at Blondie Khar", "", ["starting a cafe"]),
    ("Gentle nudge", "Ira", "Love reading", "came three times in summer, asked about a long-distance relationship", "Sunday at Janu", "", ["long-distance relationship"]),
    ("Payment reminder", "Nikhil", "Deep private reading", "", "yesterday at Blondie House", "aarav@upi", []),
    ("Payment reminder", "Tanvi", "Soul Spread", "", "Saturday at Blondie Khar", "aarav@upi", []),
]
LANGS = ["English", "Hinglish", "Hindi"]
HINDI_WORDS = {"hai", "hain", "ho", "hoon", "ka", "ki", "ke", "ko", "se", "mein", "aap", "tum", "kar", "karo", "kya", "na",
               "bhi", "toh", "phir", "milte", "aapka", "aapki", "tumhara", "accha", "acha", "zaroor", "shukriya", "dhanyavaad",
               "baje", "aur", "par", "pe", "yaad", "rakhna", "kuch", "bas", "ji"}


def user_text(kind, client, service, notes, spot, upi):
    return (f"Client: {client}\nService: {service}\n"
            + (f"Session notes: {notes}\n" if notes else "")
            + (f"Next available: {spot}\n" if spot and kind in ("After a session", "Gentle nudge") else "")
            + (f"Session: {spot}\n" if spot and kind in ("Booking confirmation", "Payment reminder") else "")
            + (f"UPI ID: {upi}\n" if upi else ""))


# The three prompt versions compared in the write-up
def prompt_v1(p, lang, kind):  # what you'd write first
    return f"Write a WhatsApp message from {p['name']}, a tarot reader, to a client. Type: {kind}. Language: {lang}."


def prompt_v2(p, lang, kind):  # rules, no voice samples
    q = dict(p, voice_samples="")
    return prompts.message_prompt(q, lang, kind)


def prompt_v3(p, lang, kind):  # what Walk-In ships: rules + the creator's own messages
    return prompts.message_prompt(p, lang, kind)


VERSIONS = [("v1 plain request", prompt_v1), ("v2 + rules", prompt_v2), ("v3 + rules + voice", prompt_v3),
            ("v4 + checker, one retry (shipped)", None)]

def checks(out, lang, case_input, sensitive, notes=""):
    fails = guard.check(out, lang, case_input, notes)
    if "private" not in fails and any(x in out.lower() for x in sensitive):
        fails.append("private")
    return fails


def main():
    if len(sys.argv) > 1:
        llm.MODEL = sys.argv[1]
    ok, status = llm.is_running()
    if not ok:
        sys.exit(status)
    llm.generate("Reply with OK.", "OK")  # load the model first, so timings are fair
    rows = []
    total = len(VERSIONS) * len(CASES) * len(LANGS)
    for vname, make in VERSIONS:
        for lang in LANGS:
            for kind, client, service, notes, spot, upi, sensitive in CASES:
                u = user_text(kind, client, service, notes, spot, upi)
                t0 = time.perf_counter()
                retried = False
                if make is None:  # what the app does: draft, check, retry once
                    out, _, retried = guard.generate_checked(llm.generate, prompt_v3(PROFILE, lang, kind), u, lang, notes)
                else:
                    out = llm.generate(make(PROFILE, lang, kind), u)
                secs = time.perf_counter() - t0
                f = checks(out, lang, u, sensitive, notes)
                rows.append({"version": vname, "language": lang, "type": kind, "client": client, "seconds": round(secs, 1),
                             "retried": retried, "passed": not f, "failed_checks": " ".join(f), "draft": out})
                print(f"[{len(rows):>3}/{total}] {vname[:22]:<22} {lang:<8} {kind:<20} {'PASS' if not f else 'FAIL ' + ' '.join(f)}")
    with open("eval_messages.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    names = list(guard.RULE_TEXT)
    print(f"\nModel: {llm.MODEL} · {len(CASES)} cases × {len(LANGS)} languages = {len(CASES) * len(LANGS)} drafts per version\n")
    print("| Prompt | Passed every check | " + " | ".join(names) + " | Avg seconds |")
    print("|---|---|" + "---|" * len(names) + "---|")
    for vname, _ in VERSIONS:
        r = [x for x in rows if x["version"] == vname]
        cnt = {n: sum(1 for x in r if any(c.startswith(n) for c in x["failed_checks"].split())) for n in names}
        print(f"| {vname} | {sum(x['passed'] for x in r)}/{len(r)} | " + " | ".join(str(cnt[n]) for n in names)
              + f" | {sum(x['seconds'] for x in r) / len(r):.1f} |")
    print("\n(Numbers under each check = drafts that failed it.) Every draft is in eval_messages.csv.")
    print("Open it and read the failures: the best examples for your write-up are in there.")


if __name__ == "__main__":
    main()
