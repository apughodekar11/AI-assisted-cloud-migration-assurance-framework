import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import yaml

from ai.utils.io import read_parquet, write_json


# -----------------------------
#  Shared model definitions
# -----------------------------


class LSTMForecaster(nn.Module):
    def __init__(
        self,
        input_size: int,
        hidden_size: int,
        num_layers: int,
        output_size: int,
        dropout: float = 0.0,
    ):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x):
        out, _ = self.lstm(x)  # (batch, seq, hidden)
        last = out[:, -1, :]   # (batch, hidden)
        out = self.fc(last)    # (batch, output_size)
        return out


class Autoencoder(nn.Module):
    def __init__(self, input_dim: int, hidden_dims=None, dropout: float = 0.0):
        super().__init__()
        if hidden_dims is None:
            hidden_dims = [32, 16]

        encoder_layers = []
        prev = input_dim
        for h in hidden_dims:
            encoder_layers.append(nn.Linear(prev, h))
            encoder_layers.append(nn.ReLU())
            if dropout > 0:
                encoder_layers.append(nn.Dropout(dropout))
            prev = h
        self.encoder = nn.Sequential(*encoder_layers)

        decoder_layers = []
        hidden_rev = list(reversed(hidden_dims))
        prev = hidden_rev[0] if hidden_rev else input_dim
        for h in hidden_rev[1:]:
            decoder_layers.append(nn.Linear(prev, h))
            decoder_layers.append(nn.ReLU())
            if dropout > 0:
                decoder_layers.append(nn.Dropout(dropout))
            prev = h
        decoder_layers.append(nn.Linear(prev, input_dim))
        self.decoder = nn.Sequential(*decoder_layers)

    def forward(self, x):
        z = self.encoder(x)
        recon = self.decoder(z)
        return recon


# -----------------------------
#  Helpers
# -----------------------------


def _resolve_columns(ds_cfg):
    """
    Support both:
      feature_columns / target_columns
    and:
      features: { columns: [...] }, targets: { columns: [...] }
    """
    if "feature_columns" in ds_cfg:
        feature_cols = ds_cfg["feature_columns"]
    elif "features" in ds_cfg and isinstance(ds_cfg["features"], dict):
        feature_cols = ds_cfg["features"].get("columns", [])
    else:
        raise KeyError("Dataset config missing 'feature_columns' or 'features.columns'")

    if "target_columns" in ds_cfg:
        target_cols = ds_cfg["target_columns"]
    elif "targets" in ds_cfg and isinstance(ds_cfg["targets"], dict):
        target_cols = ds_cfg["targets"].get("columns", [])
    else:
        raise KeyError("Dataset config missing 'target_columns' or 'targets.columns'")

    if not feature_cols:
        raise ValueError("No feature columns configured in dataset.yml")
    if not target_cols:
        raise ValueError("No target columns configured in dataset.yml")

    return feature_cols, target_cols


def _apply_scaler(df: pd.DataFrame, scaler_dict: dict) -> np.ndarray:
    """
    Apply StandardScaler using saved mean/scale and column order.
    """
    columns = scaler_dict["columns"]
    mean = np.array(scaler_dict["mean"])
    scale = np.array(scaler_dict["scale"])
    values = df[columns].values
    return (values - mean) / scale


# -----------------------------
#  LSTM scoring
# -----------------------------


def score_lstm_model(
    df: pd.DataFrame,
    ds_cfg_path: str,
    lstm_cfg_path: str,
    models_dir: Path,
    device: torch.device,
):
    with open(ds_cfg_path) as f:
        ds_cfg = yaml.safe_load(f)
    with open(lstm_cfg_path) as f:
        m_cfg = yaml.safe_load(f)

    feature_cols, target_cols = _resolve_columns(ds_cfg)

    # Load metrics & scaler
    lstm_meta_path = models_dir / "lstm_metrics.json"
    lstm_scaler_path = models_dir / "lstm_scaler.npy"
    lstm_model_path = models_dir / "lstm_best.pt"

    if not lstm_meta_path.exists() or not lstm_scaler_path.exists() or not lstm_model_path.exists():
        raise FileNotFoundError("LSTM artifacts not found in models_dir.")

    with open(lstm_meta_path) as f:
        meta = json.load(f)
    lookback_steps = int(meta.get("lookback_steps", 10))

    scaler_dict = np.load(lstm_scaler_path, allow_pickle=True).item()

    # Prepare dataframe
    df_lstm = df[feature_cols + target_cols].dropna()
    if len(df_lstm) <= lookback_steps:
        raise RuntimeError(
            f"Not enough rows for LSTM scoring: len(df)={len(df_lstm)}, "
            f"lookback_steps={lookback_steps}"
        )

    df_lstm = df_lstm.sort_index()
    scaled = _apply_scaler(df_lstm, scaler_dict)
    scaled_df = pd.DataFrame(scaled, index=df_lstm.index, columns=scaler_dict["columns"])

    # Build last sequence
    values = scaled_df.values
    X_seq = values[-lookback_steps:, : len(feature_cols)]
    y_true = values[-1:, len(feature_cols):]  # last target row

    X_seq = np.expand_dims(X_seq, axis=0)  # (1, seq_len, input_size)

    input_size = len(feature_cols)
    output_size = len(target_cols)

    model = LSTMForecaster(
        input_size=input_size,
        hidden_size=m_cfg["hidden_size"],
        num_layers=m_cfg["num_layers"],
        output_size=output_size,
        dropout=m_cfg.get("dropout", 0.0),
    ).to(device)

    state = torch.load(lstm_model_path, map_location=device)
    model.load_state_dict(state)
    model.eval()

    with torch.no_grad():
        X_t = torch.from_numpy(X_seq.astype(np.float32)).to(device)
        pred = model(X_t).cpu().numpy()

    mse_lstm = float(np.mean((pred - y_true) ** 2))

    baseline = float(meta.get("test_loss", mse_lstm + 1e-6)) + 1e-6
    norm_lstm = mse_lstm / baseline

    return {
        "raw_error": mse_lstm,
        "normalized_error": norm_lstm,
        "baseline": baseline,
        "feature_columns": feature_cols,
        "target_columns": target_cols,
    }


# -----------------------------
#  Autoencoder scoring
# -----------------------------


def score_ae_model(
    df: pd.DataFrame,
    ds_cfg_path: str,
    ae_cfg_path: str,
    models_dir: Path,
    device: torch.device,
):
    with open(ds_cfg_path) as f:
        ds_cfg = yaml.safe_load(f)
    with open(ae_cfg_path) as f:
        m_cfg = yaml.safe_load(f)

    # Feature columns only (AE reconstructs inputs)
    if "feature_columns" in ds_cfg:
        feature_cols = ds_cfg["feature_columns"]
    elif "features" in ds_cfg and isinstance(ds_cfg["features"], dict):
        feature_cols = ds_cfg["features"].get("columns", [])
    else:
        raise KeyError("Dataset config missing feature_columns for AE.")

    ae_meta_path = models_dir / "ae_metrics.json"
    ae_scaler_path = models_dir / "ae_scaler.npy"
    ae_model_path = models_dir / "ae_best.pt"

    if not ae_meta_path.exists() or not ae_scaler_path.exists() or not ae_model_path.exists():
        raise FileNotFoundError("AE artifacts not found in models_dir.")

    with open(ae_meta_path) as f:
        meta = json.load(f)
    scaler_dict = np.load(ae_scaler_path, allow_pickle=True).item()

    df_ae = df[feature_cols].dropna()
    if len(df_ae) < 1:
        raise RuntimeError("No rows available for AE scoring after dropna().")

    df_ae = df_ae.sort_index()
    X_scaled = _apply_scaler(df_ae, scaler_dict)

    # Use last point for scoring
    x_last = X_scaled[-1:, :]
    input_dim = x_last.shape[1]

    hidden_dims = m_cfg.get("hidden_dims", [32, 16])
    dropout = m_cfg.get("dropout", 0.0)

    model = Autoencoder(
        input_dim=input_dim,
        hidden_dims=hidden_dims,
        dropout=dropout,
    ).to(device)

    state = torch.load(ae_model_path, map_location=device)
    model.load_state_dict(state)
    model.eval()

    with torch.no_grad():
        x_t = torch.from_numpy(x_last.astype(np.float32)).to(device)
        recon = model(x_t).cpu().numpy()

    mse_ae = float(np.mean((recon - x_last) ** 2))
    baseline = float(meta.get("test_loss", mse_ae + 1e-6)) + 1e-6
    norm_ae = mse_ae / baseline

    return {
        "raw_error": mse_ae,
        "normalized_error": norm_ae,
        "baseline": baseline,
        "feature_columns": feature_cols,
    }


# -----------------------------
#  Main
# -----------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features-path", default="telemetry/processed/features.parquet")
    ap.add_argument("--dataset-config", default="ai/configs/dataset.yml")
    ap.add_argument("--lstm-config", default="ai/configs/model_lstm.yml")
    ap.add_argument("--ae-config", default="ai/configs/model_ae.yml")
    ap.add_argument("--models-dir", default="ai/artifacts/models")
    ap.add_argument("--out-json", default="validate/ai_score.json")
    ap.add_argument("--threshold", type=float, default=3.0)
    args = ap.parse_args()

    models_dir = Path(args.models_dir)
    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Load features
    df = read_parquet(args.features_path)
    if df is None or len(df) == 0:
        raise RuntimeError("Features file is empty or missing.")

    # Compute scores
    lstm_res = score_lstm_model(
        df=df,
        ds_cfg_path=args.dataset_config,
        lstm_cfg_path=args.lstm_config,
        models_dir=models_dir,
        device=device,
    )

    ae_res = score_ae_model(
        df=df,
        ds_cfg_path=args.dataset_config,
        ae_cfg_path=args.ae_config,
        models_dir=models_dir,
        device=device,
    )

    # Combine into a single anomaly score
    lstm_score = lstm_res["normalized_error"]
    ae_score = ae_res["normalized_error"]
    anomaly_score = 0.5 * lstm_score + 0.5 * ae_score

    flag = anomaly_score > args.threshold

    result = {
        "anomaly_score": anomaly_score,
        "threshold": args.threshold,
        "flag": flag,
        "lstm": lstm_res,
        "autoencoder": ae_res,
    }

    write_json(result, out_path)
    print(f"[AI] wrote anomaly score to {out_path}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    # Ensure project root is on sys.path if needed
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    main()

