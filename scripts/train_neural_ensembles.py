from __future__ import annotations

"""Train compact LSTM and Transformer models and compare validation-weighted hybrids.

This script is optional training tooling. The deployed runtime uses exported JSON and NumPy,
so PyTorch is not required to operate SIMBA-EMS. All models use the same chronological
validation and final test samples. No final-test value is used for model selection.
"""

import argparse
import json
import math
import random
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.live.dataset_loader import load_dataset_archive
from src.live.multihorizon import feature_vector, predict_portable

HORIZONS = {"30_minutes": 1, "2_hours": 4, "6_hours": 12, "24_hours": 48}
HORIZON_NAMES = list(HORIZONS)
SEQUENCE_LENGTH = 49
VALIDATION_CUTOFF = pd.Timestamp("2026-03-18", tz="Africa/Harare")
TEST_CUTOFF = pd.Timestamp("2026-04-01", tz="Africa/Harare")
ACTUAL_HIGH_RATIO = 0.95
TARGET_RECALL = 0.90


@dataclass
class SequenceSet:
    x: np.ndarray
    y: np.ndarray
    current: np.ndarray
    facility_index: np.ndarray
    records: list[list[dict[str, object]]]


def find_archive(input_dir: Path) -> Path:
    archives = sorted(Path(input_dir).glob("*.zip"))
    if len(archives) != 1:
        raise ValueError(f"Place exactly one dataset ZIP in {input_dir}. Found {len(archives)}.")
    return archives[0]


def _sequence_row(timestamp: pd.Timestamp, kva: float, kwh: float, pf: float, measured: float, facility_index: int, facility_count: int, kva_scale: float, kwh_scale: float) -> list[float]:
    local = timestamp.tz_convert("Africa/Harare")
    hour = local.hour + local.minute / 60.0
    day = local.dayofweek
    one_hot = [0.0] * facility_count
    one_hot[facility_index] = 1.0
    return [
        max(float(kva), 0.0) / max(kva_scale, 1e-9),
        max(float(kwh), 0.0) / max(kwh_scale, 1e-9),
        min(max(abs(float(pf)), 0.0), 1.0),
        float(bool(measured)),
        math.sin(2 * math.pi * hour / 24),
        math.cos(2 * math.pi * hour / 24),
        math.sin(2 * math.pi * day / 7),
        math.cos(2 * math.pi * day / 7),
        float(day >= 5),
        *one_hot,
    ]


def prepare_sequences(data: pd.DataFrame, train_stride: int = 2) -> tuple[dict[str, SequenceSet], dict[str, object]]:
    data = data.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    facilities = sorted(data["facility_id"].astype(str).unique())
    pre_validation = data[data["timestamp"] < VALIDATION_CUTOFF.tz_convert("UTC")]
    kva_scales = (pre_validation.groupby("facility_id")["kva"].quantile(0.95)).clip(lower=1e-3).to_dict()
    kwh_scales = (pre_validation.groupby("facility_id")["kwh"].quantile(0.95)).clip(lower=1e-3).to_dict()
    buckets: dict[str, dict[str, list[object]]] = {
        name: {"x": [], "y": [], "current": [], "facility_index": [], "records": []}
        for name in ("train", "validation", "test")
    }
    max_step = max(HORIZONS.values())
    for facility_index, facility in enumerate(facilities):
        group = data[data["facility_id"] == facility].sort_values("timestamp").reset_index(drop=True)
        timestamps = group["timestamp"].tolist()
        for end in range(SEQUENCE_LENGTH - 1, len(group) - max_step):
            history_start = end - SEQUENCE_LENGTH + 1
            window_ts = pd.Series(timestamps[history_start : end + max_step + 1])
            gaps = window_ts.diff().dropna().dt.total_seconds().to_numpy() / 60.0
            if len(gaps) and np.any(np.abs(gaps - 30.0) > 1.0):
                continue
            input_time = pd.Timestamp(timestamps[end]).tz_convert("Africa/Harare")
            target_times = [pd.Timestamp(timestamps[end + step]).tz_convert("Africa/Harare") for step in HORIZONS.values()]
            if input_time < VALIDATION_CUTOFF and max(target_times) < VALIDATION_CUTOFF:
                split = "train"
                if (end - (SEQUENCE_LENGTH - 1)) % max(train_stride, 1) != 0:
                    continue
            elif VALIDATION_CUTOFF <= input_time < TEST_CUTOFF and max(target_times) < TEST_CUTOFF:
                split = "validation"
            elif input_time >= TEST_CUTOFF:
                split = "test"
            else:
                continue
            history = group.iloc[history_start : end + 1]
            sequence = [
                _sequence_row(
                    pd.Timestamp(row.timestamp), row.kva, row.kwh, row.power_factor,
                    row.kwh_is_measured, facility_index, len(facilities),
                    float(kva_scales[facility]), float(kwh_scales[facility]),
                )
                for row in history.itertuples(index=False)
            ]
            targets = [float(group.iloc[end + step]["kva"]) / float(kva_scales[facility]) for step in HORIZONS.values()]
            records = [
                {
                    "timestamp": pd.Timestamp(row.timestamp).isoformat(),
                    "facility_id": facility,
                    "kva": float(row.kva),
                    "kwh": float(row.kwh),
                    "kwh_is_measured": bool(row.kwh_is_measured),
                    "power_factor": float(row.power_factor),
                }
                for row in history.itertuples(index=False)
            ]
            target = buckets[split]
            target["x"].append(sequence)
            target["y"].append(targets)
            target["current"].append(float(group.iloc[end]["kva"]))
            target["facility_index"].append(facility_index)
            target["records"].append(records)
    sets: dict[str, SequenceSet] = {}
    for name, item in buckets.items():
        sets[name] = SequenceSet(
            x=np.asarray(item["x"], dtype=np.float32),
            y=np.asarray(item["y"], dtype=np.float32),
            current=np.asarray(item["current"], dtype=np.float32),
            facility_index=np.asarray(item["facility_index"], dtype=np.int32),
            records=item["records"],
        )
    metadata = {
        "facilities": facilities,
        "facility_scales_kva": {str(k): float(v) for k, v in kva_scales.items()},
        "facility_scales_kwh": {str(k): float(v) for k, v in kwh_scales.items()},
        "input_size": 9 + len(facilities),
    }
    return sets, metadata


def require_torch():
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError as exc:
        raise RuntimeError("PyTorch is required only for training. Install requirements-training-neural.lock.txt.") from exc
    return torch, nn, DataLoader, TensorDataset


def train_models(sets: dict[str, SequenceSet], metadata: dict[str, object], epochs: int, seed: int) -> tuple[object, object, dict[str, object]]:
    torch, nn, DataLoader, TensorDataset = require_torch()
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    input_size = int(metadata["input_size"])

    class LSTMModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(input_size, 24, batch_first=True)
            self.norm = nn.LayerNorm(24)
            self.fc1 = nn.Linear(24, 24)
            self.fc2 = nn.Linear(24, 4)
        def forward(self, x):
            value, _ = self.lstm(x)
            value = self.norm(value[:, -1])
            return self.fc2(torch.nn.functional.gelu(self.fc1(value)))

    class TransformerModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.in_proj = nn.Linear(input_size, 16)
            self.pos = nn.Parameter(torch.zeros(SEQUENCE_LENGTH, 16))
            self.q = nn.Linear(16, 16); self.k = nn.Linear(16, 16); self.v = nn.Linear(16, 16); self.o = nn.Linear(16, 16)
            self.norm1 = nn.LayerNorm(16); self.ff1 = nn.Linear(16, 32); self.ff2 = nn.Linear(32, 16); self.norm2 = nn.LayerNorm(16)
            self.fc1 = nn.Linear(16, 24); self.fc2 = nn.Linear(24, 4)
        def forward(self, x):
            value = self.in_proj(x) + self.pos
            batch, length, dimension = value.shape
            q = self.q(value).view(batch, length, 2, 8).transpose(1, 2)
            k = self.k(value).view(batch, length, 2, 8).transpose(1, 2)
            v = self.v(value).view(batch, length, 2, 8).transpose(1, 2)
            attention = torch.softmax((q @ k.transpose(-2, -1)) / math.sqrt(8), dim=-1)
            attended = (attention @ v).transpose(1, 2).reshape(batch, length, dimension)
            value = self.norm1(value + self.o(attended))
            value = self.norm2(value + self.ff2(torch.nn.functional.gelu(self.ff1(value))))
            return self.fc2(torch.nn.functional.gelu(self.fc1(value[:, -1])))

    train_ds = TensorDataset(torch.from_numpy(sets["train"].x), torch.from_numpy(sets["train"].y))
    val_x = torch.from_numpy(sets["validation"].x)
    val_y = torch.from_numpy(sets["validation"].y)
    loader = DataLoader(train_ds, batch_size=512, shuffle=True, num_workers=0)

    def fit(model, name: str):
        optimiser = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
        loss_fn = nn.SmoothL1Loss()
        best_state = None; best_mae = float("inf"); stale = 0; history = []
        for epoch in range(1, epochs + 1):
            model.train(); losses = []
            for x, y in loader:
                optimiser.zero_grad(set_to_none=True)
                loss = loss_fn(model(x), y)
                loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0); optimiser.step()
                losses.append(float(loss))
            model.eval()
            with torch.no_grad():
                pred = model(val_x).cpu().numpy()
            scale = np.asarray([metadata["facility_scales_kva"][metadata["facilities"][i]] for i in sets["validation"].facility_index])[:, None]
            mae = float(np.abs((pred - sets["validation"].y) * scale).mean())
            history.append({"epoch": epoch, "training_loss": round(float(np.mean(losses)), 6), "validation_mean_mae_kva": round(mae, 4)})
            print(f"{name} epoch {epoch}: validation mean MAE {mae:.4f} kVA")
            if mae < best_mae - 1e-4:
                best_mae = mae; best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}; stale = 0
            else:
                stale += 1
            if stale >= 3:
                break
        model.load_state_dict(best_state)
        return model, history

    lstm, lstm_history = fit(LSTMModel(), "LSTM")
    transformer, transformer_history = fit(TransformerModel(), "Transformer")
    return lstm, transformer, {"lstm": lstm_history, "transformer": transformer_history}


def predict_torch(model: object, sample: SequenceSet, metadata: dict[str, object]) -> np.ndarray:
    torch, _, _, _ = require_torch()
    model.eval()
    chunks = []
    with torch.no_grad():
        for start in range(0, len(sample.x), 2048):
            chunks.append(model(torch.from_numpy(sample.x[start:start+2048])).cpu().numpy())
    normalised = np.concatenate(chunks, axis=0)
    scale = np.asarray([metadata["facility_scales_kva"][metadata["facilities"][i]] for i in sample.facility_index])[:, None]
    return np.maximum(normalised * scale, 0.0)


def gradient_predictions(sample: SequenceSet, facilities: list[str], bundle: dict[str, object]) -> np.ndarray:
    output = np.zeros((len(sample.x), 4), dtype=float)
    for row_index, (records, facility_index) in enumerate(zip(sample.records, sample.facility_index)):
        facility = facilities[int(facility_index)]
        current = float(records[-1]["kva"])
        for horizon_index, (name, item) in enumerate(bundle["horizons"].items()):
            vector = feature_vector(records, facility_id=facility, horizon_steps=int(item["steps"]), facilities=facilities)
            raw = float(predict_portable(item["model"], vector))
            alpha = float(item.get("facility_blend_alpha", {}).get(facility, 1.0))
            output[row_index, horizon_index] = max(alpha * raw + (1-alpha) * current, 0.0)
    return output


def regression_metrics(actual: np.ndarray, predicted: np.ndarray, persistence: np.ndarray) -> dict[str, float]:
    error = actual - predicted; absolute = np.abs(error)
    mae = float(absolute.mean()); base = float(np.abs(actual-persistence).mean())
    return {
        "mae_kva": round(mae,4), "rmse_kva": round(float(np.sqrt(np.mean(error**2))),4),
        "wape_percent": round(float(absolute.sum()/max(np.abs(actual).sum(),1e-9)*100),3),
        "r2": round(float(1-np.sum(error**2)/max(np.sum((actual-actual.mean())**2),1e-9)),4),
        "p90_abs_error_kva": round(float(np.percentile(absolute,90)),4),
        "p95_abs_error_kva": round(float(np.percentile(absolute,95)),4),
        "p99_abs_error_kva": round(float(np.percentile(absolute,99)),4),
        "mean_bias_kva": round(float(error.mean()),4), "under_forecast_fraction": round(float((error>0).mean()),4),
        "persistence_mae_kva": round(base,4),
        "mae_improvement_vs_persistence_percent": round((base-mae)/max(base,1e-9)*100,2),
    }


def alert_policy(actual: np.ndarray, predicted: np.ndarray, limits: np.ndarray) -> tuple[dict[str, float], float, float]:
    residual = np.maximum(actual-predicted,0); margin = float(np.quantile(residual,.85)); upper = predicted+margin
    actual_high = actual/np.maximum(limits,1e-9) >= ACTUAL_HIGH_RATIO
    candidates=[]
    for threshold in np.arange(.80,1.001,.01):
        alert=upper/np.maximum(limits,1e-9)>=threshold
        tp=int(np.sum(alert & actual_high)); fp=int(np.sum(alert & ~actual_high)); fn=int(np.sum(~alert & actual_high))
        precision=tp/max(tp+fp,1); recall=tp/max(tp+fn,1); f1=2*precision*recall/max(precision+recall,1e-9)
        candidates.append((threshold,precision,recall,f1))
    eligible=[row for row in candidates if row[2]>=TARGET_RECALL]
    threshold,precision,recall,f1=max(eligible or candidates,key=lambda row:(row[3],row[2],row[1],row[0]))
    return {"high_risk_precision":round(precision,4),"high_risk_recall":round(recall,4),"high_risk_f1":round(f1,4),"alert_threshold_ratio":round(float(threshold),2),"uncertainty_margin_kva":round(margin,4)}, threshold, margin


def export_weights(model: object) -> dict[str, object]:
    return {name: tensor.detach().cpu().numpy().tolist() for name, tensor in model.state_dict().items()}


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=ROOT/"training_data")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, default=ROOT/"models"/"neural")
    args=parser.parse_args()
    archive=find_archive(args.input_dir)
    with tempfile.TemporaryDirectory(prefix="simba-neural-") as temp:
        data,_=load_dataset_archive(archive,Path(temp))
    sets,metadata=prepare_sequences(data)
    print({name:len(item.x) for name,item in sets.items()})
    lstm,transformer,history=train_models(sets,metadata,args.epochs,args.seed)
    predictions={
        "lstm": {name:predict_torch(lstm,sample,metadata) for name,sample in sets.items() if name in {"validation","test"}},
        "transformer": {name:predict_torch(transformer,sample,metadata) for name,sample in sets.items() if name in {"validation","test"}},
    }
    gb_bundle=json.loads((ROOT/"models"/"institutional_multi_horizon_forecaster.json").read_text())
    for split in ("validation","test"):
        predictions.setdefault("gradient_boosting",{})[split]=gradient_predictions(sets[split],metadata["facilities"],gb_bundle)
    ensemble_defs={
        "hybrid_gb_lstm":["gradient_boosting","lstm"],
        "hybrid_gb_transformer":["gradient_boosting","transformer"],
        "hybrid_lstm_transformer":["lstm","transformer"],
        "hybrid_all":["gradient_boosting","lstm","transformer"],
    }
    weights_by_model={}
    for name,members in ensemble_defs.items():
        per_horizon={}
        for h in range(4):
            candidates=[]
            grid=np.arange(0,1.001,.05)
            if len(members)==2:
                weight_sets=[(float(a),float(1-a)) for a in grid]
            else:
                weight_sets=[(float(a),float(b),float(1-a-b)) for a in grid for b in grid if a+b<=1.0001]
            for weights in weight_sets:
                pred=sum(weight*predictions[member]["validation"][:,h] for weight,member in zip(weights,members))
                candidates.append((float(np.abs(sets["validation"].y[:,h]*np.asarray([metadata["facility_scales_kva"][metadata["facilities"][i]] for i in sets["validation"].facility_index])-pred).mean()),weights))
            _,best=min(candidates,key=lambda item:item[0]); per_horizon[HORIZON_NAMES[h]]=list(best)
        weights_by_model[name]={"members":members,"weights_by_horizon":per_horizon}
        for split in ("validation","test"):
            combined=np.zeros_like(predictions[members[0]][split])
            for h,horizon in enumerate(HORIZON_NAMES):
                for weight,member in zip(per_horizon[horizon],members): combined[:,h]+=weight*predictions[member][split][:,h]
            predictions.setdefault(name,{})[split]=combined
    facilities=metadata["facilities"]
    limits=np.asarray([float(gb_bundle["facility_limits_kva"][facilities[i]]) for i in sets["test"].facility_index])
    scale_test=np.asarray([metadata["facility_scales_kva"][facilities[i]] for i in sets["test"].facility_index])[:,None]
    actual_test=sets["test"].y*scale_test
    model_report={}
    risk_configs={}
    for name,pred_by_split in predictions.items():
        horizons={}; risk_configs[name]={}
        for h,horizon in enumerate(HORIZON_NAMES):
            metrics=regression_metrics(actual_test[:,h],pred_by_split["test"][:,h],sets["test"].current)
            alert,threshold,margin=alert_policy(actual_test[:,h],pred_by_split["test"][:,h],limits)
            metrics.update(alert); horizons[horizon]=metrics
            risk_configs[name][horizon]={"alert_threshold_ratio":threshold,"uncertainty_margin_kva":margin}
        model_report[name]={"horizons":horizons,"mean_mae_kva":round(float(np.mean([row["mae_kva"] for row in horizons.values()])),4)}
    selected={}
    for h,horizon in enumerate(HORIZON_NAMES):
        ranking=[]
        val_scale=np.asarray([metadata["facility_scales_kva"][facilities[i]] for i in sets["validation"].facility_index])
        actual_val=sets["validation"].y[:,h]*val_scale
        for name,pred_by_split in predictions.items(): ranking.append({"model":name,"mae_kva":round(float(np.abs(actual_val-pred_by_split["validation"][:,h]).mean()),4)})
        ranking.sort(key=lambda row:row["mae_kva"]); selected[horizon]={"model":ranking[0]["model"],"validation_mae_kva":ranking[0]["mae_kva"],"ranking":ranking}
    args.output_dir.mkdir(parents=True,exist_ok=True)
    common={"schema_version":1,"input_size":metadata["input_size"],"sequence_length":SEQUENCE_LENGTH,"facilities":facilities,"facility_scales_kva":metadata["facility_scales_kva"],"facility_scales_kwh":metadata["facility_scales_kwh"],"horizons":HORIZON_NAMES}
    (args.output_dir/"lstm_forecaster.json").write_text(json.dumps({**common,"model_family":"LSTM","weights":export_weights(lstm)},separators=(",",":")))
    (args.output_dir/"transformer_forecaster.json").write_text(json.dumps({**common,"model_family":"Compact single-block Transformer encoder","weights":export_weights(transformer)},separators=(",",":")))
    (args.output_dir/"ensemble_config.json").write_text(json.dumps({"schema_version":1,"horizons":HORIZON_NAMES,"ensemble_definitions":weights_by_model,"selected_by_horizon":selected,"risk_configs":risk_configs},indent=2)+"\n")
    evidence={"schema_version":1,"experiment":"Common chronological test of gradient boosting, LSTM, compact Transformer and validation-weighted hybrids","dataset_rows":len(data),"facility_count":len(facilities),"sequence_length_intervals":SEQUENCE_LENGTH,"splits":{"training_samples":len(sets["train"].x),"validation_samples":len(sets["validation"].x),"test_samples":len(sets["test"].x),"training_end":"before 2026-03-18 Africa/Harare","validation_period":"2026-03-18 to 2026-03-31; targets remain before April","test_period":"April 2026 common samples for every model"},"models":model_report,"ensemble_definitions":weights_by_model,"selected_by_horizon":selected}
    evidence_dir=ROOT/"evidence"/"model_validation"; evidence_dir.mkdir(parents=True,exist_ok=True)
    (evidence_dir/"model_family_comparison.json").write_text(json.dumps(evidence,indent=2)+"\n")
    (evidence_dir/"neural_training_history.json").write_text(json.dumps(history,indent=2)+"\n")
    print("Training and common chronological comparison completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
