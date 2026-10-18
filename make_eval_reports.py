"""Builds eval/EVAL.md and eval/REVIEW.md from the evaluation results.

Run after the evaluations:
    python evaluate_slots.py
    python evaluate_messages.py
    python make_eval_reports.py

EVAL.md   how both models were measured, with every number taken from the result files
REVIEW.md 15 of Gemma's shipped drafts (5 per language) laid out for a person to rate by hand.
          Fill in the rating columns yourself: the point is a human's judgement, not the script's.
"""
import os
import random
import shutil
from pathlib import Path

import pandas as pd

HERE = Path(os.path.abspath(__file__)).parent
START = Path.cwd()
os.chdir(HERE)  # write eval/ next to this script


def find(name: str):
    """Look next to this script, where it was started from, and in sibling walkin-platform folders."""
    places = [HERE, START, HERE / "eval"] + sorted(HERE.parent.glob("walkin-platform*"))
    for folder in places:
        if (folder / name).exists():
            return folder / name
    return None


OUT = Path("eval")
OUT.mkdir(exist_ok=True)
CHECKS = ["length", "emoji", "hashtags", "price", "time", "private", "language"]
CHECK_TEXT = {
    "length": "2 to 4 sentences", "emoji": "at most one emoji", "hashtags": "no hashtags",
    "price": "no ₹ amount that wasn't given", "time": "no clock time that wasn't given",
    "private": "doesn't copy 4+ words from the private session notes",
    "language": "right language and script (Hindi in Devanagari, Hinglish in Latin letters with Hindi words)",
}


def cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def slots_section(lines):
    path = find("eval_slots.csv")
    print("eval_slots.csv:", path or "NOT FOUND")
    lines += ["## 1. Best slots: TabPFN vs simple averages", ""]
    if path is None:
        lines += ["*Not run yet: `python evaluate_slots.py`*", ""]
        return
    r = pd.read_csv(path)
    mae = {c: (r[c] - r["actual"]).abs().mean() for c in ("tabpfn", "averages", "overall_mean")}
    wins = int(((r["tabpfn"] - r["actual"]).abs() < (r["averages"] - r["actual"]).abs()).sum())
    lines += [
        "**Question:** given a creator's own small log of sittings, does TabPFN predict a sitting's earnings better "
        "than the rule of thumb a creator would use?",
        "",
        "**Method:** walk forward through the log in date order. Each sitting after the first 8 is predicted using "
        "only the sittings before it, then compared with what actually happened. Script: `evaluate_slots.py`.",
        "",
        "| Predictor | Average error per sitting |",
        "|---|---|",
        f"| TabPFN v2 | ₹{mae['tabpfn']:,.0f} |",
        f"| Per-cafe weekday/weekend averages | ₹{mae['averages']:,.0f} |",
        f"| Overall mean of earlier sittings | ₹{mae['overall_mean']:,.0f} |",
        "",
        f"TabPFN was closer than the averages on **{wins} of {len(r)}** predicted sittings.",
        "",
        "| Date | Cafe | Actual | TabPFN | Averages | Overall mean |",
        "|---|---|---|---|---|---|",
    ]
    for _, x in r.iterrows():
        lines.append(f"| {x['date']} | {cell(x['cafe'])} | ₹{x['actual']:,.0f} | ₹{x['tabpfn']:,.0f} | "
                     f"₹{x['averages']:,.0f} | ₹{x['overall_mean']:,.0f} |")
    lines += ["", f"**Limits:** {len(r)} predictions from a small log, so this is a sanity check, not proof. "
              "`sample_sittings.csv` is made-up test data with real cafe names. Rerun on a real log as it grows.", ""]
    if path.resolve() != (OUT / path.name).resolve():
        shutil.copy(path, OUT / path.name)


def messages_section(lines):
    path = find("eval_messages.csv")
    print("eval_messages.csv:", path or "NOT FOUND")
    lines += ["## 2. Messages: does Gemma follow the rules?", ""]
    if path is None:
        lines += ["*Not run yet: `python evaluate_messages.py`*", ""]
        return None
    d = pd.read_csv(path).fillna("")
    d["passed"] = d["passed"].astype(str).str.lower().eq("true")
    n_cases = d.groupby("version").size().max()
    lines += [
        "**Question:** how often does a draft break the rules a creator cares about, and what does each part of the "
        "prompt and the checker add?",
        "",
        f"**Method:** 10 real situations (thank-yous after hard readings, booking confirmations, gentle nudges, payment "
        f"reminders) × English, Hinglish and Hindi = {n_cases} drafts per version, each checked automatically by "
        "`guard.py`. Script: `evaluate_messages.py`. Every draft is in `eval_messages.csv`.",
        "",
        "Checks: " + "; ".join(f"**{k}**: {v}" for k, v in CHECK_TEXT.items()) + ".",
        "",
        "| Prompt version | Passed every check | " + " | ".join(CHECKS) + " | Avg seconds |",
        "|---|---|" + "---|" * len(CHECKS) + "---|",
    ]
    for v in d["version"].unique():
        r = d[d["version"] == v]
        cnt = [sum(1 for f in r["failed_checks"] if c in str(f).split()) for c in CHECKS]
        lines.append(f"| {v} | {r['passed'].sum()}/{len(r)} | " + " | ".join(map(str, cnt))
                     + f" | {pd.to_numeric(r['seconds'], errors='coerce').mean():.1f} |")
    lines += ["", "Numbers under each check are drafts that failed it (a draft can fail several).", ""]
    if "retried" in d:
        shipped = d[d["version"].str.contains("shipped")]
        if len(shipped):
            retried = shipped["retried"].astype(str).str.lower().eq("true").sum()
            lines += [f"In the shipped version, **{retried} of {len(shipped)}** drafts needed the one retry.", ""]
    fails = d[(~d["passed"]) & (~d["version"].str.contains("v1"))].head(6)
    if len(fails):
        lines += ["### Examples of failed drafts (word for word)", ""]
        for _, x in fails.iterrows():
            lines += [f"**{x['version']} · {x['language']} · {x['type']}** — failed: `{x['failed_checks']}`", "",
                      f"> {cell(x['draft'])}", ""]
    if path.resolve() != (OUT / path.name).resolve():
        shutil.copy(path, OUT / path.name)
    return d


def review_file(d):
    shipped = d[d["version"].str.contains("shipped")] if d is not None else None
    lines = ["# Hand review: would a creator send these?", "",
             "Automatic checks catch broken rules, not bad writing. So a person reads Gemma's shipped drafts "
             "(prompt + creator's own messages + checker) and answers three questions for each.", "",
             "**Reviewer:** ✏️ *[who reviewed, e.g. a tarot reader who works at cafes in Mumbai]* · **Date:** ✏️", "",
             "How to rate:",
             "- **Send?** `Y` send as is · `E` send after a small edit · `N` wouldn't send",
             "- **Sounds like the creator (1–5):** compared with the creator's own messages in `sample_messages.txt`",
             "- **Respectful?** `Y` / `N`: handles private things gently, no pressure, no invented details", ""]
    if shipped is None or not len(shipped):
        lines += ["*Run `python evaluate_messages.py`, then `python make_eval_reports.py` to fill this in.*", ""]
    else:
        rnd = random.Random(7)
        picked = []
        for lang in ("English", "Hinglish", "Hindi"):
            rows = shipped[shipped["language"] == lang].to_dict("records")
            picked += rnd.sample(rows, min(5, len(rows)))
        lines += ["| # | Language | Situation | Draft | Send? | Sounds like creator (1–5) | Respectful? | Notes |",
                  "|---|---|---|---|---|---|---|---|"]
        for i, x in enumerate(picked, 1):
            lines.append(f"| {i} | {x['language']} | {x['type']} ({x['client']}) | {cell(x['draft'])} |  |  |  |  |")
        lines += ["", "## Summary", "",
                  "✏️ *Fill in after rating: e.g. \"11 of 15 I'd send as is, 3 after a small edit, 1 not at all. "
                  "Average voice match 3.9/5. The Hindi drafts were the most formal.\"*", ""]
    (OUT / "REVIEW.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    lines = ["# Evaluation", "",
             "How Walk-In's two open models were measured. Everything here is generated by `make_eval_reports.py` "
             "from the result files in this folder; nothing is typed in by hand.", ""]
    slots_section(lines)
    d = messages_section(lines)
    lines += ["## 3. Hand review", "", "Automatic checks can't tell whether a message is *good*. See "
              "[REVIEW.md](REVIEW.md) for a person's ratings of 15 shipped drafts.", ""]
    lines += ["## 4. A creator's review", "", "A tarot reader who tested Walk-In wrote about the whole product, "
              "not just the drafts: [CREATOR_REVIEW.md](CREATOR_REVIEW.md).", ""]
    (OUT / "EVAL.md").write_text("\n".join(lines), encoding="utf-8")
    review_file(d)
    print("Wrote eval/EVAL.md and eval/REVIEW.md (and copied the result CSVs into eval/).")
    print("Now open eval/REVIEW.md and fill in the rating columns by hand.")


if __name__ == "__main__":
    main()
