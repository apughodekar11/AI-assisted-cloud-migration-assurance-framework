import pandas as pd
from pathlib import Path
import json

def write_parquet(df: pd.DataFrame, path: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p)

def read_parquet(path: str) -> pd.DataFrame:
    return pd.read_parquet(path)

def write_json(obj, path: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        json.dump(obj, f, indent=2, default=str)

