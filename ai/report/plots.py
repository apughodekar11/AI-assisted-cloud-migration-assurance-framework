import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import yaml
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt

from ai.utils.io import read_parquet


# -----------------------------
#  Models (same as training)
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
#  Helper utilities
# -----------------------------


def resolve_columns(ds_cfg):
    if "feature_columns" in ds_cfg:
        feature_cols = ds_cfg["feature_columns"]
    elif "features" in ds_cfg and isinstance(ds_cfg["features"], dict):
        feature_cols = ds_cfg["features"].get("columns", [])
    else:
        raise KeyError("Missing feature_columns in dataset.yml")

    if "target_columns" in ds_cfg:
        target_cols = ds_cfg["target_columns"]
    elif "targets" in ds_cfg and isinstance(ds_cfg["targets"], dict):
        target_cols = ds_cfg["targets"].get("columns", [])
    else:
        raise KeyError("Missing target_columns in dataset.yml")

    return feature_cols, target_cols


def apply_scaler(df: pd.DataFrame, scaler_dict: dict) -> pd.DataFrame:
    cols = scaler_dict["columns"]
    mean = np.array(scaler_dict["mean"])
    scale = np.array(scaler_dict["scale"])
    values = df[cols].values
    scaled = (values - mean) / scale
    return pd.DataFrame(scaled, index=df.index, columns=cols)


def build_sequences(values: np.ndarray, n_features: int, lookback_steps: int):
    X_list = []
    y_list = []
    for i in range(lookback_steps, len(values)):
        X_list.append(values[i - lookback_steps : i, :n_features])
        y_list.append(values[i, n_features:])
    X = np.stack(X_list, axis=0)
    y = np.stack(y_list, axis=0)
    return X, y


def split_by_time(X, y, times, train_ratio, val_ratio):
    n = X.shape[0]
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)

    X_train = X[:n_train]
    y_train = y[:n_train]
    t_train = times[:n_train]

    X_val = X[n_train : n_train + n_val]
    y_val = y[n_train : n_train + n_val]
    t_val = times[n_train : n_train + n_val]

    X_test = X[n_train + n_val :]
    y_test = y[n_train + n_val :]
    t_test = times[n_train + n_val :]

    return (X_train, y_train, t_train), (X_val, y_val, t_val), (X_test, y_test, t_test)


# -----------------------------
#  Core computations
# -----------------------------


def compute_lstm_test(
    features_path="telemetry/processed/features.parquet",
    ds_cfg_path="ai/configs/dataset.yml",
    lstm_cfg_path="ai/configs/model_lstm.yml",
    models_dir="ai/artifacts/models",
):
    """
    Load the trained LSTM, run it on the test split, and return:
      - times
      - y_true, y_pred (in original units)
      - per-sample normalized squared error
    """
    models_dir = Path(models_dir)
    with open(ds_cfg_path) as f:
        ds_cfg = yaml.safe_load(f)
    with open(lstm_cfg_path) as f:
        m_cfg = yaml.safe_load(f)

    feature_cols, target_cols = resolve_columns(ds_cfg)
    target_col = target_cols[0]

    df = read_parquet(features_path)
    df = df.sort_index()
    df = df[feature_cols + target_cols].dropna()

    scaler_dict = np.load(models_dir / "lstm_scaler.npy", allow_pickle=True).item()
    with open(models_dir / "lstm_metrics.json") as f:
        meta = json.load(f)

    lookback_steps = int(meta.get("lookback_steps", 10))
    baseline = float(meta.get("test_loss", 1e-6)) + 1e-6

    df_scaled = apply_scaler(df, scaler_dict)

    values = df_scaled.values
    n_features = len(feature_cols)

    X_all, y_all = build_sequences(values, n_features, lookback_steps)
    times_all = df_scaled.index[lookback_steps:]

    train_ratio = ds_cfg.get("train_ratio", 0.7)
    val_ratio = ds_cfg.get("val_ratio", 0.15)

    (_, _, _), (_, _, _), (X_test, y_test, t_test) = split_by_time(
        X_all, y_all, times_all, train_ratio, val_ratio
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = LSTMForecaster(
        input_size=len(feature_cols),
        hidden_size=m_cfg["hidden_size"],
        num_layers=m_cfg["num_layers"],
        output_size=len(target_cols),
        dropout=m_cfg.get("dropout", 0.0),
    ).to(device)

    state = torch.load(models_dir / "lstm_best.pt", map_location=device)
    model.load_state_dict(state)
    model.eval()

    with torch.no_grad():
        X_t = torch.from_numpy(X_test.astype(np.float32)).to(device)
        pred_scaled = model(X_t).cpu().numpy()  # (N, 1)

    # Target index in scaler
    target_idx = scaler_dict["columns"].index(target_col)
    mean = np.array(scaler_dict["mean"])[target_idx]
    scale = np.array(scaler_dict["scale"])[target_idx]

    y_true_scaled = y_test[:, 0]
    y_pred_scaled = pred_scaled[:, 0]

    # For plotting in original units
    y_true = y_true_scaled * scale + mean
    y_pred = y_pred_scaled * scale + mean

    # Per-sample error (in scaled space)
    err_scaled = (y_pred_scaled - y_true_scaled) ** 2  # shape (N,)
    norm_err = err_scaled / baseline

    return {
        "times": t_test,
        "y_true": y_true,
        "y_pred": y_pred,
        "norm_error": norm_err,
        "baseline": baseline,
        "target_col": target_col,
    }


def compute_ae_all(
    features_path="telemetry/processed/features.parquet",
    ds_cfg_path="ai/configs/dataset.yml",
    ae_cfg_path="ai/configs/model_ae.yml",
    models_dir="ai/artifacts/models",
):
    """
    Load AE, run reconstruction on the full dataset, and return:
      - times
      - reconstruction MSE per point
      - normalized error per point
    """
    models_dir = Path(models_dir)
    with open(ds_cfg_path) as f:
        ds_cfg = yaml.safe_load(f)
    with open(ae_cfg_path) as f:
        m_cfg = yaml.safe_load(f)

    if "feature_columns" in ds_cfg:
        feature_cols = ds_cfg["feature_columns"]
    elif "features" in ds_cfg and isinstance(ds_cfg["features"], dict):
        feature_cols = ds_cfg["features"].get("columns", [])
    else:
        raise KeyError("Missing feature_columns in dataset.yml")

    df = read_parquet(features_path)
    df = df.sort_index()
    df = df[feature_cols].dropna()

    scaler_dict = np.load(models_dir / "ae_scaler.npy", allow_pickle=True).item()
    with open(models_dir / "ae_metrics.json") as f:
        meta = json.load(f)

    baseline = float(meta.get("test_loss", 1e-6)) + 1e-6

    df_scaled = apply_scaler(df, scaler_dict)
    X_scaled = df_scaled.values
    times = df_scaled.index

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    input_dim = X_scaled.shape[1]
    hidden_dims = m_cfg.get("hidden_dims", [32, 16])
    dropout = m_cfg.get("dropout", 0.0)

    model = Autoencoder(
        input_dim=input_dim,
        hidden_dims=hidden_dims,
        dropout=dropout,
    ).to(device)

    state = torch.load(models_dir / "ae_best.pt", map_location=device)
    model.load_state_dict(state)
    model.eval()

    with torch.no_grad():
        X_t = torch.from_numpy(X_scaled.astype(np.float32)).to(device)
        recon = model(X_t).cpu().numpy()

    mse = np.mean((recon - X_scaled) ** 2, axis=1)  # per-point
    norm_err = mse / baseline

    return {
        "times": times,
        "recon_mse": mse,
        "norm_error": norm_err,
        "baseline": baseline,
    }


# -----------------------------
#  PLOT 1 – Time series:
#  AE reconstruction error over time
# -----------------------------


def plot_ae_recon_timeseries(
    out_path="ai/artifacts/plots/ae_recon_error_timeseries.png",
):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    res = compute_ae_all()
    times = res["times"]
    mse = res["recon_mse"]
    baseline = res["baseline"]

    plt.figure(figsize=(10, 4))
    plt.plot(times, mse, label="Reconstruction MSE")
    plt.axhline(baseline, linestyle="--", label="Test loss baseline")
    plt.xlabel("Time")
    plt.ylabel("Reconstruction error")
    plt.title("Autoencoder Reconstruction Error Over Time")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()
    print(f"[PLOT] Saved AE reconstruction error timeseries to {out_path}")


# -----------------------------
#  PLOT 2 – Histogram:
#  AE reconstruction error distribution
# -----------------------------


def plot_ae_recon_hist(
    out_path="ai/artifacts/plots/ae_recon_error_hist.png",
):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    res = compute_ae_all()
    mse = res["recon_mse"]
    baseline = res["baseline"]

    plt.figure(figsize=(8, 4))
    plt.hist(mse, bins=15)
    plt.axvline(baseline, linestyle="--", label="Test loss baseline")
    plt.xlabel("Reconstruction MSE")
    plt.ylabel("Frequency")
    plt.title("Autoencoder Reconstruction Error Distribution")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()
    print(f"[PLOT] Saved AE reconstruction error histogram to {out_path}")


# -----------------------------
#  PLOT 3 – Density hexbin:
#  LSTM actual vs predicted
# -----------------------------


def plot_lstm_actual_vs_pred_scatter(
    out_path="ai/artifacts/plots/lstm_actual_vs_pred_scatter.png",
):
    """
    Plot LSTM actual vs predicted using a hexbin density plot with y=x line.
    Much clearer than raw scatter when values are tiny and clustered.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    res = compute_lstm_test()
    y_true = res["y_true"]
    y_pred = res["y_pred"]
    target_col = res["target_col"]

    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)

    # Focus on the main mass of points (99th percentile) for readable axes
    x_max = np.percentile(y_true, 99)
    y_max = np.percentile(y_pred, 99)
    lim = max(x_max, y_max)

    fig, ax = plt.subplots(figsize=(6, 6))

    hb = ax.hexbin(y_true, y_pred, gridsize=30, bins="log")
    cbar = fig.colorbar(hb, ax=ax)
    cbar.set_label("log10(count)")

    ax.plot([0, lim], [0, lim], linestyle="--")

    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)

    ax.set_xlabel(f"Actual {target_col}")
    ax.set_ylabel(f"Predicted {target_col}")
    ax.set_title("LSTM Forecast – Actual vs Predicted (density)")

    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    print(f"[PLOT] Saved LSTM actual vs predicted density to {out_path}")


# -----------------------------
#  PLOT 4 – Density curves:
#  LSTM vs AE normalized error distributions
# -----------------------------


def plot_error_kde(
    out_path="ai/artifacts/plots/lstm_vs_ae_error_kde.png",
):
    """
    Compare LSTM vs Autoencoder normalized errors using smooth density curves
    derived from histograms. This replaces the old boxplot with something
    more thesis-friendly.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lstm_res = compute_lstm_test()
    ae_res = compute_ae_all()

    lstm_norm_err = np.asarray(lstm_res["norm_error"])
    ae_norm_err = np.asarray(ae_res["norm_error"])

    all_err = np.concatenate([lstm_norm_err, ae_norm_err])
    x_min = all_err.min()
    x_max = np.percentile(all_err, 99)  # clip extreme outliers

    # Build simple "KDE-like" density from histograms (no SciPy dependency)
    def density(values):
        counts, edges = np.histogram(
            values, bins=30, range=(x_min, x_max), density=True
        )
        centers = 0.5 * (edges[:-1] + edges[1:])
        return centers, counts

    xs_lstm, d_lstm = density(lstm_norm_err)
    xs_ae, d_ae = density(ae_norm_err)

    plt.figure(figsize=(8, 6))
    plt.plot(xs_lstm, d_lstm, label="LSTM normalized error", linewidth=2)
    plt.plot(xs_ae, d_ae, label="Autoencoder normalized error", linewidth=2)
    plt.fill_between(xs_lstm, d_lstm, alpha=0.15)
    plt.fill_between(xs_ae, d_ae, alpha=0.15)

    plt.xlabel("Normalized error")
    plt.ylabel("Density")
    plt.title("Error Distribution Comparison – LSTM vs Autoencoder")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()
    print(f"[PLOT] Saved KDE-style error comparison to {out_path}")


# -----------------------------
#  Main – generate all 4 plots
# -----------------------------


def main():
    plot_ae_recon_timeseries()          # time series
    plot_ae_recon_hist()                # histogram
    plot_lstm_actual_vs_pred_scatter()  # density hexbin
    plot_error_kde()                    # density comparison of errors


if __name__ == "__main__":
    main()

