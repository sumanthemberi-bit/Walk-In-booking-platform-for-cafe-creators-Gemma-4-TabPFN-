"""Is TabPFN better than simple averages at predicting a sitting? An honest backtest.

Usage:  python evaluate_slots.py                      (uses sample_sittings.csv)
        python evaluate_slots.py my_sittings.csv      (a creator's real log: date,start_time,end_time,cafe,customers,earnings)

Walk forward through the log in date order. For each sitting after the first 8, predict its earnings
using ONLY the sittings before it, then compare with what actually happened. Three predictors:
  TabPFN v2      what Walk-In uses
  Averages       the creator's own rule of thumb: average ₹ per hour at that cafe on weekdays/weekends
  Overall mean   the dumbest guess: average earnings of every earlier sitting
Prints the average error in ₹ (lower is better) and a table to paste into the write-up.
"""
import sys
import time

import pandas as pd

import db
import predict

MIN_TRAIN = predict.MIN_ROWS


def load(path):
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if "venue" in df.columns and "cafe" not in df.columns:
        df = df.rename(columns={"venue": "cafe"})
    df["start_hour"] = df["start_time"].map(db.parse_time)
    df["end_hour"] = df["end_time"].map(db.parse_time)
    df["date"] = pd.to_datetime(df["date"])
    df["day"] = df["date"].dt.day_name()
    df["earnings"] = df["earnings"].astype(float)
    return df.sort_values(["date", "start_hour"]).reset_index(drop=True)


def averages(train, row):
    t = train.assign(weekend=train["day"].isin(["Saturday", "Sunday"]),
                     per_hour=train["earnings"] / (train["end_hour"] - train["start_hour"]))
    same = t[(t["cafe"] == row["cafe"]) & (t["weekend"] == (row["day"] in ("Saturday", "Sunday")))]
    rate = same["per_hour"].mean() if len(same) else t["per_hour"].mean()
    return rate * (row["end_hour"] - row["start_hour"])


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "sample_sittings.csv"
    df = load(path)
    if len(df) <= MIN_TRAIN:
        sys.exit(f"Need more than {MIN_TRAIN} sittings for a backtest; {path} has {len(df)}.")
    from tabpfn import TabPFNRegressor
    from tabpfn.constants import ModelVersion

    rows, t_total = [], 0.0
    for k in range(MIN_TRAIN, len(df)):
        train, row = df.iloc[:k], df.iloc[k]
        cafes = sorted(df["cafe"].unique())
        X, Xr = predict._features(train, cafes), predict._features(df.iloc[[k]], cafes)
        t0 = time.perf_counter()
        m = TabPFNRegressor.create_default_for_version(ModelVersion(predict.TABPFN_VERSION),
                                                       n_estimators=predict.TABPFN_ESTIMATORS, device=predict.TABPFN_DEVICE)
        m.fit(X.values, train["earnings"].values)
        tab = float(m.predict(Xr.values)[0])
        t_total += time.perf_counter() - t0
        rows.append({"date": row["date"].date(), "cafe": row["cafe"], "actual": row["earnings"],
                     "tabpfn": tab, "averages": averages(train, row), "overall_mean": train["earnings"].mean()})
        print(f"{row['date'].date()} {row['cafe']:<18} actual ₹{row['earnings']:>7,.0f}   TabPFN ₹{tab:>7,.0f}   "
              f"averages ₹{rows[-1]['averages']:>7,.0f}")
    r = pd.DataFrame(rows)
    r.to_csv("eval_slots.csv", index=False)
    mae = {c: (r[c] - r["actual"]).abs().mean() for c in ("tabpfn", "averages", "overall_mean")}
    wins = int(((r["tabpfn"] - r["actual"]).abs() < (r["averages"] - r["actual"]).abs()).sum())
    print(f"\n{len(df)} sittings in {path}; predicted the last {len(r)}, each from only the sittings before it.\n")
    print("| Predictor | Average error per sitting |")
    print("|---|---|")
    print(f"| TabPFN v2 ({predict.TABPFN_ESTIMATORS} estimators, {predict.TABPFN_DEVICE}) | ₹{mae['tabpfn']:,.0f} |")
    print(f"| Per-cafe weekday/weekend averages | ₹{mae['averages']:,.0f} |")
    print(f"| Overall mean | ₹{mae['overall_mean']:,.0f} |")
    print(f"\nTabPFN was closer than the averages on {wins} of {len(r)} sittings. "
          f"Average time per prediction: {t_total / len(r):.1f} s. Details: eval_slots.csv")
    print("With a small log, treat this as a sanity check, not proof. Rerun it as more sittings are logged.")


if __name__ == "__main__":
    main()
