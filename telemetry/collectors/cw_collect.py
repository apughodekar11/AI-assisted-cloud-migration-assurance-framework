import argparse
from datetime import datetime, timedelta, timezone
import pandas as pd
from pathlib import Path
from ai.utils.aws import cw_client
from ai.utils.io import write_parquet

METRICS = [
    {
        "namespace": "AWS/ApiGateway",
        "name": "Latency",
        "stat": "p50",
        "alias": "api_p50_ms",
        "dimensions": [{"Name": "Stage", "Value": "dev"}],
    },
    {
        "namespace": "AWS/ApiGateway",
        "name": "Latency",
        "stat": "p95",
        "alias": "api_p95_ms",
        "dimensions": [{"Name": "Stage", "Value": "dev"}],
    },
    {
        "namespace": "AWS/ApiGateway",
        "name": "4xxError",
        "stat": "Average",
        "alias": "api_4xx_rate",
        "dimensions": [{"Name": "Stage", "Value": "dev"}],
    },
    {
        "namespace": "AWS/ApiGateway",
        "name": "5xxError",
        "stat": "Average",
        "alias": "api_5xx_rate",
        "dimensions": [{"Name": "Stage", "Value": "dev"}],
    },
    {
        "namespace": "AWS/ApiGateway",
        "name": "Count",
        "stat": "Sum",
        "alias": "api_rps",
        "dimensions": [{"Name": "Stage", "Value": "dev"}],
    },
    {
        "namespace": "AWS/RDS",
        "name": "CPUUtilization",
        "stat": "Average",
        "alias": "db_cpu",
        "dimensions": [],
    },
    {
        "namespace": "AWS/RDS",
        "name": "DatabaseConnections",
        "stat": "Average",
        "alias": "db_conn",
        "dimensions": [],
    },
    {
        "namespace": "AWS/RDS",
        "name": "ReadLatency",
        "stat": "Average",
        "alias": "db_read_ms",
        "dimensions": [],
    },
    {
        "namespace": "AWS/RDS",
        "name": "WriteLatency",
        "stat": "Average",
        "alias": "db_write_ms",
        "dimensions": [],
    },
]

def fetch_metric(cw, m, start, end, period, rds_identifier=None):
    dims = list(m["dimensions"])
    if m["namespace"].startswith("AWS/RDS") and rds_identifier:
        dims = dims + [{"Name": "DBInstanceIdentifier", "Value": rds_identifier}]

    params = {
        "Namespace": m["namespace"],
        "MetricName": m["name"],
        "Dimensions": dims,
        "StartTime": start,
        "EndTime": end,
        "Period": period,
    }

    # Only one of these should be set, and never an empty list
    if m["stat"] in ("p50", "p95"):
        params["ExtendedStatistics"] = [m["stat"]]
    else:
        params["Statistics"] = [m["stat"]]

    res = cw.get_metric_statistics(**params)

    data = []
    for p in res.get("Datapoints", []):
        ts = p["Timestamp"]
        if m["stat"] in ("p50", "p95"):
            val = p["ExtendedStatistics"][m["stat"]]
        else:
            val = p[m["stat"]]
        data.append({"timestamp": ts, m["alias"]: float(val)})
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="eu-west-1")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--period", type=int, default=60)
    ap.add_argument("--stage", default="dev")
    ap.add_argument("--rds-id", default=None)
    ap.add_argument("--out-dir", default="telemetry/raw")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=args.hours)
    cw = cw_client(args.region)

    rows = []
    for m in METRICS:
        if m["namespace"] == "AWS/ApiGateway":
            for d in m["dimensions"]:
                if d["Name"] == "Stage":
                    d["Value"] = args.stage
        rows += fetch_metric(cw, m, start, now, args.period, args.rds_id)

    if not rows:
        print("no metrics found")
        return

    df = pd.DataFrame(rows).groupby("timestamp").first().sort_index()
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    out_path = f"{args.out_dir}/cw_{now.strftime('%Y%m%d_%H%M%S')}.parquet"
    write_parquet(df, out_path)
    print(f"wrote {out_path}")

if __name__ == "__main__":
    main()

