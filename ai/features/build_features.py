import argparse
from pathlib import Path

import pandas as pd
import yaml

from ai.utils.io import read_parquet, write_parquet


def load_latest_raw(pattern: str):
    """Return the latest raw telemetry file matching pattern in telemetry/raw."""
    raw_dir = Path("telemetry/raw")
    files = sorted(raw_dir.glob(pattern))
    if not files:
        return None
    return read_parquet(str(files[-1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="ai/configs/dataset.yml")
    ap.add_argument("--out", default="telemetry/processed/features.parquet")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    bucket = cfg.get("bucket_size_seconds", 60)

    # ---- CloudWatch metrics ----
    cw_df = load_latest_raw("cw_*.parquet")
    if cw_df is None:
        print("[build_features] no cw_*.parquet metrics found in telemetry/raw")
        return

    # Ensure timestamp index and resample to fixed bucket size
    cw_df = cw_df.sort_index()
    cw_df.index = pd.to_datetime(cw_df.index)
    cw_df = cw_df.resample(f"{bucket}s").mean()

    # ---- Cost data (real or dummy) ----
    cost_df = load_latest_raw("cost*.parquet")  # matches cost_*.parquet or cost_dummy_*.parquet

    if cost_df is not None:
        # Make sure cost_df has a date column and convert to plain Python date
        if "date" not in cost_df.columns:
            raise RuntimeError("[build_features] cost parquet missing 'date' column")
        cost_df["date"] = pd.to_datetime(cost_df["date"]).dt.date
        cost_df = cost_df.set_index("date")

        # Convert cw_df index to plain date, join on 'date'
        cw_df["date"] = cw_df.index.date
        cw_df = cw_df.join(cost_df, on="date")

        # Remove helper column
        cw_df = cw_df.drop(columns=["date"])

        # Fill missing cost with 0.0
        if "cost_daily_usd" in cw_df.columns:
            cw_df["cost_daily_usd"] = cw_df["cost_daily_usd"].fillna(0.0)
        else:
            cw_df["cost_daily_usd"] = 0.0
    else:
        # No cost data at all – default to 0
        cw_df["cost_daily_usd"] = 0.0

    # ---- Time-based features ----
    cw_df["hour_of_day"] = cw_df.index.hour
    cw_df["day_of_week"] = cw_df.index.dayofweek

    # Drop rows that are entirely NaN (shouldn't remove our time/cost features)
    cw_df = cw_df.dropna(how="all")

    # Write processed features
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(cw_df, str(out_path))
    print(f"[build_features] wrote {out_path}")


if __name__ == "__main__":
    main()

