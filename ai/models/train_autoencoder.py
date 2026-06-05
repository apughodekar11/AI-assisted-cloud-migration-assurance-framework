import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import StandardScaler
import yaml

from ai.utils.io import read_parquet, write_json


class TabularDataset(Dataset):
    def __init__(self, X: np.ndarray):
        self.X = X.astype(np.float32)

    def __len__(self):
        return self.X.shape[0]

    def __getitem__(self, idx):
        x = self.X[idx]
        return x, x  # input == target for autoencoder


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


def split_by_time(X, train_ratio, val_ratio):
    n = X.shape[0]
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    n_test = n - n_train - n_val

    X_train = X[:n_train]
    X_val = X[n_train : n_train + n_val]
    X_test = X[n_train + n_val :]

    return X_train, X_val, X_test


def train_epoch(model, loader, criterion, optimizer, device):
    model.train()
    total_loss = 0.0
    n = 0
    for X_batch, y_batch in loader:
        X_batch = X_batch.to(device)
        y_batch = y_batch.to(device)
        optimizer.zero_grad()
        recon = model(X_batch)
        loss = criterion(recon, y_batch)
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
            recon = model(X_batch)
            loss = criterion(recon, y_batch)
            total_loss += loss.item() * X_batch.size(0)
            n += X_batch.size(0)
    return total_loss / max(n, 1)


def _resolve_feature_columns(ds_cfg):
    # Reuse same pattern as LSTM – support flat and nested configs
    if "feature_columns" in ds_cfg:
        feature_cols = ds_cfg["feature_columns"]
    elif "features" in ds_cfg and isinstance(ds_cfg["features"], dict):
        feature_cols = ds_cfg["features"].get("columns", [])
    else:
        raise KeyError("Dataset config missing 'feature_columns' or 'features.columns'")

    if not feature_cols:
        raise ValueError("No feature columns configured in dataset.yml")

    return feature_cols


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features-path", default="telemetry/processed/features.parquet")
    ap.add_argument("--dataset-config", default="ai/configs/dataset.yml")
    ap.add_argument("--model-config", default="ai/configs/model_ae.yml")
    ap.add_argument("--out-dir", default="ai/artifacts/models")
    args = ap.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    with open(args.dataset_config) as f:
        ds_cfg = yaml.safe_load(f)
    with open(args.model_config) as f:
        m_cfg = yaml.safe_load(f)

    feature_cols = _resolve_feature_columns(ds_cfg)

    # Load and prepare data
    df = read_parquet(args.features_path)
    df = df.sort_index()
    df = df[feature_cols].dropna()

    if df.empty:
        raise RuntimeError("No data available in features file for Autoencoder.")

    print(f"[AE] Using feature columns: {feature_cols}")
    print(f"[AE] Data points after dropna: {len(df)}")

    scaler = StandardScaler()
    X = scaler.fit_transform(df.values)

    train_ratio = ds_cfg.get("train_ratio", 0.7)
    val_ratio = ds_cfg.get("val_ratio", 0.15)

    X_train, X_val, X_test = split_by_time(X, train_ratio, val_ratio)
    print(
        f"[AE] Split: train={X_train.shape[0]}, "
        f"val={X_val.shape[0]}, test={X_test.shape[0]}"
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    train_ds = TabularDataset(X_train)
    val_ds = TabularDataset(X_val)
    test_ds = TabularDataset(X_test)

    train_loader = DataLoader(
        train_ds,
        batch_size=m_cfg["batch_size"],
        shuffle=True,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=m_cfg["batch_size"],
        shuffle=False,
    )
    test_loader = DataLoader(
        test_ds,
        batch_size=m_cfg["batch_size"],
        shuffle=False,
    )

    input_dim = X.shape[1]
    hidden_dims = m_cfg.get("hidden_dims", [32, 16])
    dropout = m_cfg.get("dropout", 0.0)

    model = Autoencoder(
        input_dim=input_dim,
        hidden_dims=hidden_dims,
        dropout=dropout,
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
            f"[AE] epoch {epoch}/{m_cfg['epochs']} "
            f"train={train_loss:.6f} val={val_loss:.6f}"
        )

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = model.state_dict()

    if best_state is not None:
        model.load_state_dict(best_state)

    test_loss = eval_epoch(model, test_loader, criterion, device)
    print(f"[AE] test_loss={test_loss:.6f}")

    model_path = Path(args.out_dir) / "ae_best.pt"
    scaler_path = Path(args.out_dir) / "ae_scaler.npy"
    meta_path = Path(args.out_dir) / "ae_metrics.json"

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
            "best_val_loss": float(best_val_loss),
            "test_loss": float(test_loss),
            "feature_columns": feature_cols,
            "hidden_dims": hidden_dims,
            "train_loss_history": history["train_loss"],
            "val_loss_history": history["val_loss"],
        },
        meta_path,
    )

    print(f"[AE] saved model to {model_path}")
    print(f"[AE] saved scaler to {scaler_path}")
    print(f"[AE] saved metrics to {meta_path}")


if __name__ == "__main__":
    main()

