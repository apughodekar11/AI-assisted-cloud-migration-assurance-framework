import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from botocore.exceptions import ClientError

from ai.utils.aws import ce_client
from ai.utils.io import write_parquet


def build_date_range(start_date, end_date):
    """Return list of ISO dates from start (inclusive) to end (exclusive)."""
    days = []
    cur = start_date
    while cur < end_date:
        days.append(cur)
        cur = cur + timedelta(days=1)
    return days


def write_dummy_cost(out_dir: str, days: int):
    """
    Fallback: write a synthetic cost file with zero cost
    so the rest of the pipeline can continue.
    """
    now = datetime.now(timezone.utc).date()
    start = now - timedelta(days=days)
    dates = build_date_range(start, now)

    rows = [{"date": d.isoformat(), "cost_daily_usd": 0.0} for d in dates]
    df = pd.DataFrame(rows)

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(out_dir) / f"cost_dummy_{now.strftime('%Y%m%d')}.parquet"
    write_parquet(df, str(out_path))
    print(f"[ce_cost_collect] Cost Explorer unavailable, wrote dummy cost file: {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="eu-west-1")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--tag-key", default="Stack")
    ap.add_argument("--tag-value", default="cloud-migrate-ai-dev")
    ap.add_argument("--out-dir", default="telemetry/raw")
    args = ap.parse_args()

    now = datetime.now(timezone.utc).date()
    start = now - timedelta(days=args.days)

    try:
        # Cost Explorer endpoint is only in us-east-1
        ce = ce_client("us-east-1")

        print(
            f"[ce_cost_collect] Requesting Cost Explorer data from {start.isoformat()} "
            f"to {now.isoformat()} for tag {args.tag_key}={args.tag_value}"
        )

        res = ce.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": now.isoformat()},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            Filter={
                "Tags": {
                    "Key": args.tag_key,
                    "Values": [args.tag_value],
                    "MatchOptions": ["EQUALS"],
                }
            },
        )

        rows = []
        for d in res.get("ResultsByTime", []):
            day = d["TimePeriod"]["Start"]
            amt = float(d["Total"]["UnblendedCost"]["Amount"])
            rows.append({"date": day, "cost_daily_usd": amt})

        if not rows:
            print("[ce_cost_collect] No cost data returned, falling back to dummy cost file.")
            write_dummy_cost(args.out_dir, args.days)
            return

        df = pd.DataFrame(rows)
        Path(args.out_dir).mkdir(parents=True, exist_ok=True)
        out_path = Path(args.out_dir) / f"cost_{now.strftime('%Y%m%d')}.parquet"
        write_parquet(df, str(out_path))
        print(f"[ce_cost_collect] Wrote real cost data to {out_path}")

    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        print(f"[ce_cost_collect] ClientError from Cost Explorer ({code}): {e}")
        print("[ce_cost_collect] Falling back to dummy cost data.")
        write_dummy_cost(args.out_dir, args.days)

    except Exception as e:
        print(f"[ce_cost_collect] Unexpected error: {e}")
        print("[ce_cost_collect] Falling back to dummy cost data.")
        write_dummy_cost(args.out_dir, args.days)


if __name__ == "__main__":
    main()

