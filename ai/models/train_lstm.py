import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import StandardScaler
import yaml

from ai.utils.io import read_parquet, write_json


class SequenceDataset(Dataset):
    def __init__(self, X_seq: np.ndarray, y: np.ndarray):
        self.X_seq = X_seq.astype(np.float32)
        self.y = y.astype(np.float32)

    def __len__(self):
        return self.X_seq.shape[0]

    def __getitem__(self, idx):
        return self.X_seq[idx], self.y[idx]


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
        # x: (batch, seq_len, input_size)
        out, _ = self.lstm(x)
        # take the last time step
        last = out[:, -1, :]
        out = self.fc(last)
        return out


def build_sequences(df: pd.DataFrame, feature_cols, target_cols, lookback_steps: int):
    """
    Build sliding window sequences from a time-indexed dataframe.

    df must already contain feature_cols + target_cols and be scaled.
    """
    values = df[feature_cols + target_cols].values
    X_list = []
    y_list = []

    for i in range(lookback_steps, len(values)):
        X_list.append(values[i - lookback_steps : i, : len(feature_cols)])
        y_list.append(values[i, len(feature_cols) :])

    if not X_list:
        raise ValueError("Not enough data to build sequences")

    X = np.stack(X_list, axis=0)
    y = np.stack(y_list, axis=0)
    return X, y


def split_by_time(X, y, train_ratio, val_ratio):
    n = X.shape[0]
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    n_test = n - n_train - n_val

    X_train = X[:n_train]
    y_train = y[:n_train]
    X_val = X[n_train : n_train + n_val]
    y_val = y[n_train : n_train + n_val]
    X_test = X[n_train + n_val :]
    y_test = y[n_train + n_val :]

    return (X_train, y_train), (X_val, y_val), (X_test, y_test)


def train_epoch(model, loader, criterion, optimizer, device):
    model.train()
    total_loss = 0.0
    n = 0
    for X_batch, y_batch in loader:
        X_batch = X_batch.to(device)
        y_batch = y_batch.to(device)
        optimizer.zero_grad()
        pred = model(X_batch)
        loss = criterion(pred, y_batch)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * X_batch.size(0)
        n += X_batch.size(0)
    return total_loss / max(n, 1)


def eval_epoch(model, loader, criterion, device):
    model.eval()
    total_loss = 0.0
    n = 0
    with torch.no_grad():
        for X_batch, y_batch in loader:
            X_batch = X_batch.to(device)
            y_batch = y_batch.to(device)
            pred = model(X_batch)
            loss = criterion(pred, y_batch)
            total_loss += loss.item() * X_batch.size(0)
            n += X_batch.size(0)
    return total_loss / max(n, 1)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features-path", default="telemetry/processed/features.parquet")
    ap.add_argument("--dataset-config", default="ai/configs/dataset.yml")
    ap.add_argument("--model-config", default="ai/configs/model_lstm.yml")
    ap.add_argument("--out-dir", default="ai/artifacts/models")
    args = ap.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    with open(args.dataset_config) as f:
        ds_cfg = yaml.safe_load(f)
    with open(args.model_config) as f:
        m_cfg = yaml.safe_load(f)

    # Resolve feature & target columns from config (supports flat or nested style)
    feature_cols, target_cols = _resolve_columns(ds_cfg)

    # Load dataframe
    df = read_parquet(args.features_path)
    df = df.sort_index()
    df = df[feature_cols + target_cols].dropna()

    if df.empty:
        raise RuntimeError("No data available in features file after dropna().")

    # Compute lookback in steps
    lookback_minutes = m_cfg.get("lookback_minutes", ds_cfg.get("lookback_minutes", 60))
    bucket_seconds = ds_cfg.get("bucket_size_seconds", 60)
    lookback_steps = max(1, int(lookback_minutes * 60 / bucket_seconds))

    # Clamp lookback_steps to available data size to avoid "Not enough data" error
    max_steps = max(1, len(df) - 2)
    if lookback_steps > max_steps:
        print(
            f"[LSTM] Reducing lookback_steps from {lookback_steps} to {max_steps} "
            f"due to limited data (len(df)={len(df)})"
        )
        lookback_steps = max_steps

    print(f"[LSTM] Using feature columns: {feature_cols}")
    print(f"[LSTM] Using target columns: {target_cols}")
    print(f"[LSTM] Data points after dropna: {len(df)}")
    print(f"[LSTM] Final lookback_steps: {lookback_steps}")

    # Scale data
    scaler = StandardScaler()
    scaled = scaler.fit_transform(df.values)
    df_scaled = pd.DataFrame(scaled, index=df.index, columns=df.columns)

    # Build sequences
    X, y = build_sequences(df_scaled, feature_cols, target_cols, lookback_steps)
    print(f"[LSTM] Built sequences: X.shape={X.shape}, y.shape={y.shape}")

    # Train/val/test split
    train_ratio = ds_cfg.get("train_ratio", 0.7)
    val_ratio = ds_cfg.get("val_ratio", 0.15)
    (X_train, y_train), (X_val, y_val), (X_test, y_test) = split_by_time(
        X, y, train_ratio, val_ratio
    )

    print(
        f"[LSTM] Split: "
        f"train={X_train.shape[0]}, val={X_val.shape[0]}, test={X_test.shape[0]}"
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    train_ds = SequenceDataset(X_train, y_train)
    val_ds = SequenceDataset(X_val, y_val)
    test_ds = SequenceDataset(X_test, y_test)

    train_loader = DataLoader(
        train_ds, batch_size=m_cfg["batch_size"], shuffle=True
    )
    val_loader = DataLoader(
        val_ds, batch_size=m_cfg["batch_size"], shuffle=False
    )
    test_loader = DataLoader(
        test_ds, batch_size=m_cfg["batch_size"], shuffle=False
    )

    model = LSTMForecaster(
        input_size=len(feature_cols),
        hidden_size=m_cfg["hidden_size"],
        num_layers=m_cfg["num_layers"],
        output_size=len(target_cols),
        dropout=m_cfg.get("dropout", 0.0),
    ).to(device)

    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=m_cfg["learning_rate"],
        weight_decay=m_cfg.get("weight_decay", 0.0),
    )

    best_val_loss = float("inf")
    best_state = None
    history = {"train_loss": [], "val_loss": []}

    for epoch in range(1, m_cfg["epochs"] + 1):
        train_loss = train_epoch(model, train_loader, criterion, optimizer, device)
        val_loss = eval_epoch(model, val_loader, criterion, device)
        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)
        print(
            f"[LSTM] epoch {epoch}/{m_cfg['epochs']} "
            f"train={train_loss:.6f} val={val_loss:.6f}"
        )
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = model.state_dict()

    if best_state is not None:
        model.load_state_dict(best_state)

    test_loss = eval_epoch(model, test_loader, criterion, device)
    print(f"[LSTM] test_loss={test_loss:.6f}")

    model_path = Path(args.out_dir) / "lstm_best.pt"
    scaler_path = Path(args.out_dir) / "lstm_scaler.npy"
    meta_path = Path(args.out_dir) / "lstm_metrics.json"

    torch.save(model.state_dict(), model_path)
    np.save(
        scaler_path,
        {
            "mean": scaler.mean_,
            "scale": scaler.scale_,
            "columns": df.columns.to_list(),
        },
    )
    write_json(
        {
            "best_val_loss": best_val_loss,
            "test_loss": test_loss,
            "feature_columns": feature_cols,
            "target_columns": target_cols,
            "lookback_steps": lookback_steps,
            "train_loss_history": history["train_loss"],
            "val_loss_history": history["val_loss"],
        },
        meta_path,
    )
    print(f"[LSTM] saved model to {model_path}")
    print(f"[LSTM] saved scaler to {scaler_path}")
    print(f"[LSTM] saved metrics to {meta_path}")


if __name__ == "__main__":
    # Optional: ensure project root is on sys.path if needed
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    main()

