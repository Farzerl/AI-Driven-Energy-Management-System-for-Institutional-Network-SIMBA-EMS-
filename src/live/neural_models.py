from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

import numpy as np

HARARE = ZoneInfo("Africa/Harare")


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=HARARE)
    return parsed.astimezone(HARARE)


def _sigmoid(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _gelu(value: np.ndarray) -> np.ndarray:
    flat = value.reshape(-1)
    result = np.asarray(
        [0.5 * float(item) * (1.0 + math.erf(float(item) / math.sqrt(2.0))) for item in flat],
        dtype=float,
    )
    return result.reshape(value.shape)


def _softmax(value: np.ndarray, axis: int = -1) -> np.ndarray:
    shifted = value - np.max(value, axis=axis, keepdims=True)
    exp = np.exp(np.clip(shifted, -60.0, 60.0))
    return exp / np.maximum(exp.sum(axis=axis, keepdims=True), 1e-12)


def _layer_norm(value: np.ndarray, weight: np.ndarray, bias: np.ndarray, eps: float = 1e-5) -> np.ndarray:
    mean = value.mean(axis=-1, keepdims=True)
    variance = ((value - mean) ** 2).mean(axis=-1, keepdims=True)
    return (value - mean) / np.sqrt(variance + eps) * weight + bias


def sequence_features(
    records: Iterable[Mapping[str, object]],
    *,
    facility_id: str,
    bundle: Mapping[str, object],
) -> np.ndarray:
    rows = sorted(records, key=lambda row: _timestamp(row["timestamp"]))
    required = int(bundle.get("sequence_length", 49))
    if len(rows) < required:
        raise ValueError(f"Neural forecast requires {required} completed readings; only {len(rows)} are available.")
    rows = rows[-required:]
    facilities = [str(item) for item in bundle["facilities"]]
    if facility_id not in facilities:
        raise ValueError(f"Facility {facility_id!r} is not present in the neural model bundle.")
    kva_scale = max(float(dict(bundle["facility_scales_kva"])[facility_id]), 1e-9)
    kwh_scale = max(float(dict(bundle["facility_scales_kwh"])[facility_id]), 1e-9)
    facility_index = facilities.index(facility_id)
    output: list[list[float]] = []
    for row in rows:
        timestamp = _timestamp(row["timestamp"])
        hour = timestamp.hour + timestamp.minute / 60.0
        day = timestamp.weekday()
        kva = max(float(row.get("kva", 0.0)), 0.0)
        kwh = max(float(row.get("kwh", kva * 0.5)), 0.0)
        base = [
            kva / kva_scale,
            kwh / kwh_scale,
            min(max(abs(float(row.get("power_factor", 0.95))), 0.0), 1.0),
            float(bool(row.get("kwh_is_measured", True))),
            float(np.sin(2 * np.pi * hour / 24)),
            float(np.cos(2 * np.pi * hour / 24)),
            float(np.sin(2 * np.pi * day / 7)),
            float(np.cos(2 * np.pi * day / 7)),
            float(day >= 5),
        ]
        one_hot = [0.0] * len(facilities)
        one_hot[facility_index] = 1.0
        output.append(base + one_hot)
    return np.asarray(output, dtype=float)


class PortableLSTM:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.bundle = json.loads(self.path.read_text(encoding="utf-8"))
        weights = self.bundle["weights"]
        self.w_ih = np.asarray(weights["lstm.weight_ih_l0"], dtype=float)
        self.w_hh = np.asarray(weights["lstm.weight_hh_l0"], dtype=float)
        self.b_ih = np.asarray(weights["lstm.bias_ih_l0"], dtype=float)
        self.b_hh = np.asarray(weights["lstm.bias_hh_l0"], dtype=float)
        self.norm_weight = np.asarray(weights["norm.weight"], dtype=float)
        self.norm_bias = np.asarray(weights["norm.bias"], dtype=float)
        self.fc1_weight = np.asarray(weights["fc1.weight"], dtype=float)
        self.fc1_bias = np.asarray(weights["fc1.bias"], dtype=float)
        self.fc2_weight = np.asarray(weights["fc2.weight"], dtype=float)
        self.fc2_bias = np.asarray(weights["fc2.bias"], dtype=float)
        self.hidden_size = self.w_hh.shape[1]

    def predict(self, records: Iterable[Mapping[str, object]], facility_id: str) -> dict[str, float]:
        sequence = sequence_features(records, facility_id=facility_id, bundle=self.bundle)
        hidden = np.zeros(self.hidden_size, dtype=float)
        cell = np.zeros(self.hidden_size, dtype=float)
        for row in sequence:
            gates = self.w_ih @ row + self.b_ih + self.w_hh @ hidden + self.b_hh
            input_gate, forget_gate, candidate, output_gate = np.split(gates, 4)
            input_gate = _sigmoid(input_gate)
            forget_gate = _sigmoid(forget_gate)
            candidate = np.tanh(candidate)
            output_gate = _sigmoid(output_gate)
            cell = forget_gate * cell + input_gate * candidate
            hidden = output_gate * np.tanh(cell)
        normalised = _layer_norm(hidden, self.norm_weight, self.norm_bias)
        projected = _gelu(self.fc1_weight @ normalised + self.fc1_bias)
        output = self.fc2_weight @ projected + self.fc2_bias
        scale = max(float(dict(self.bundle["facility_scales_kva"])[facility_id]), 1e-9)
        horizons = [str(item) for item in self.bundle["horizons"]]
        return {name: max(float(value) * scale, 0.0) for name, value in zip(horizons, output)}


class PortableTransformer:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.bundle = json.loads(self.path.read_text(encoding="utf-8"))
        weights = self.bundle["weights"]
        self.w = {key: np.asarray(value, dtype=float) for key, value in weights.items()}
        self.nhead = 2

    def _linear(self, value: np.ndarray, name: str) -> np.ndarray:
        return value @ self.w[f"{name}.weight"].T + self.w[f"{name}.bias"]

    def predict(self, records: Iterable[Mapping[str, object]], facility_id: str) -> dict[str, float]:
        sequence = sequence_features(records, facility_id=facility_id, bundle=self.bundle)
        value = self._linear(sequence, "in_proj") + self.w["pos"]
        length, dimension = value.shape
        head_dimension = dimension // self.nhead
        query = self._linear(value, "q").reshape(length, self.nhead, head_dimension).transpose(1, 0, 2)
        key = self._linear(value, "k").reshape(length, self.nhead, head_dimension).transpose(1, 0, 2)
        projected_value = self._linear(value, "v").reshape(length, self.nhead, head_dimension).transpose(1, 0, 2)
        scores = query @ key.transpose(0, 2, 1) / math.sqrt(head_dimension)
        attention = _softmax(scores, axis=-1)
        attended = (attention @ projected_value).transpose(1, 0, 2).reshape(length, dimension)
        value = _layer_norm(
            value + self._linear(attended, "o"),
            self.w["norm1.weight"],
            self.w["norm1.bias"],
        )
        feed_forward = self._linear(_gelu(self._linear(value, "ff1")), "ff2")
        value = _layer_norm(
            value + feed_forward,
            self.w["norm2.weight"],
            self.w["norm2.bias"],
        )
        projected = _gelu(self._linear(value[-1], "fc1"))
        output = self._linear(projected, "fc2")
        scale = max(float(dict(self.bundle["facility_scales_kva"])[facility_id]), 1e-9)
        horizons = [str(item) for item in self.bundle["horizons"]]
        return {name: max(float(item) * scale, 0.0) for name, item in zip(horizons, output)}
