"""'Where and when should I sit?' — TabPFN on a solo worker's tiny session log.

TabPFN is built for small tabular datasets, which is exactly what one person's
logbook is: a couple of dozen rows, not millions. Two regressors are fitted on
the log: one for earnings, one for customer count.
"""
import os

import pandas as pd

from db import DAYS

MIN_ROWS = 8
# v2 weights are Apache 2.0 (with attribution): no login, commercial use allowed.
# Set TABPFN_VERSION=v3 (or v2.5) to try newer weights; those are non-commercial and need a Prior Labs login.
TABPFN_VERSION = os.getenv("TABPFN_VERSION", "v2")
TABPFN_ESTIMATORS = int(os.getenv("TABPFN_ESTIMATORS", "4"))
# CPU by default: Ollama is already using the 4 GB GPU for Gemma.
TABPFN_DEVICE = os.getenv("TABPFN_DEVICE", "cpu")


def _features(df: pd.DataFrame, cafes: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["day"] = df["day"].map({d: i for i, d in enumerate(DAYS)})
    out["weekend"] = out["day"].isin([5, 6]).astype(int)
    out["start"] = df["start_hour"].astype(float)
    out["hours"] = (df["end_hour"] - df["start_hour"]).astype(float)
    out["cafe"] = df["cafe"].map({c: i for i, c in enumerate(cafes)})
    return out.fillna(0)


def _candidates(sessions: pd.DataFrame, cafes, days_ahead: int) -> pd.DataFrame:
    """Upcoming days x the start times he actually uses x every cafe, ending at his usual end time."""
    weekend = sessions["day"].isin(["Saturday", "Sunday"])
    starts = {
        True: sorted(sessions.loc[weekend, "start_hour"].unique()) or sorted(sessions["start_hour"].unique()),
        False: sorted(sessions.loc[~weekend, "start_hour"].unique()) or sorted(sessions["start_hour"].unique()),
    }
    end = float(sessions["end_hour"].max())
    start_day = pd.Timestamp.today().normalize() + pd.Timedelta(days=1)
    rows = []
    for d in range(days_ahead):
        date = start_day + pd.Timedelta(days=d)
        is_we = date.day_name() in ("Saturday", "Sunday")
        for s in starts[is_we]:
            for cafe in cafes:
                rows.append({"date": date.date(), "day": date.day_name(), "start_hour": s,
                             "end_hour": end, "cafe": cafe})
    return pd.DataFrame(rows)


def best_slots(sessions: pd.DataFrame, days_ahead: int = 7):
    """Returns (ranked DataFrame, method_used)."""
    if len(sessions) < MIN_ROWS:
        raise ValueError(f"Need at least {MIN_ROWS} logged sittings, have {len(sessions)}.")

    cafes = sorted(sessions["cafe"].unique())
    X = _features(sessions, cafes)
    grid = _candidates(sessions, cafes, days_ahead)
    Xg = _features(grid, cafes)

    try:
        from tabpfn import TabPFNRegressor
        from tabpfn.constants import ModelVersion

        version = ModelVersion(TABPFN_VERSION)
        for target in ("earnings", "customers"):
            # 4 ensemble members instead of 8: plenty for ~20 rows and twice as fast on CPU.
            model = TabPFNRegressor.create_default_for_version(
                version, n_estimators=TABPFN_ESTIMATORS, device=TABPFN_DEVICE)
            model.fit(X.values, sessions[target].astype(float).values)
            grid[f"pred_{target}"] = model.predict(Xg.values)
        method = f"TabPFN {TABPFN_VERSION}"
    except Exception as e:  # not installed / license not accepted yet
        s = sessions.assign(weekend=sessions["day"].isin(["Saturday", "Sunday"]),
                            per_hour=sessions["earnings"] / (sessions["end_hour"] - sessions["start_hour"]),
                            cust_per_hour=sessions["customers"] / (sessions["end_hour"] - sessions["start_hour"]))
        rate = s.groupby(["weekend", "cafe"])[["per_hour", "cust_per_hour"]].mean()
        overall = s[["per_hour", "cust_per_hour"]].mean()
        e_, c_ = [], []
        for r in grid.itertuples():
            key = (r.day in ("Saturday", "Sunday"), r.cafe)
            rr = rate.loc[key] if key in rate.index else overall
            hrs = r.end_hour - r.start_hour
            e_.append(rr["per_hour"] * hrs)
            c_.append(rr["cust_per_hour"] * hrs)
        grid["pred_earnings"], grid["pred_customers"] = e_, c_
        method = f"simple averages (TabPFN unavailable: {type(e).__name__})"

    grid["pred_earnings"] = grid["pred_earnings"].clip(lower=0).round(-1).astype(int)
    grid["pred_customers"] = grid["pred_customers"].clip(lower=0).round(1)
    ranked = grid.sort_values("pred_earnings", ascending=False).reset_index(drop=True)
    return ranked, method


def cafe_summary(sessions: pd.DataFrame) -> pd.DataFrame:
    """What actually happened, per cafe and weekday/weekend — for the write-up."""
    s = sessions.assign(when=sessions["day"].isin(["Saturday", "Sunday"]).map({True: "Weekend", False: "Weekday"}))
    g = s.groupby(["cafe", "when"]).agg(sittings=("id", "count"), avg_customers=("customers", "mean"),
                                         avg_earnings=("earnings", "mean"))
    return g.round(1).reset_index()
