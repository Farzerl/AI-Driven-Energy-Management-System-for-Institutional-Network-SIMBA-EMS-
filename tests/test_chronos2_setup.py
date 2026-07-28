from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import scripts.chronos2_setup as chronos_setup_module
from scripts.chronos2_setup import (
    _is_complete_model_checkpoint,
    _is_lora_adapter_checkpoint,
    choose_deployment_variant,
    safe_extract_model,
)

ROOT = Path(__file__).resolve().parents[1]


def test_model_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../model.safetensors", b"bad")
        handle.writestr("../config.json", "{}")
    with pytest.raises(ValueError, match="Unsafe model ZIP path"):
        safe_extract_model(archive, tmp_path / "model")
    assert not (tmp_path.parent / "model.safetensors").exists()


def test_model_archive_rejects_unofficial_weight_checksum(tmp_path: Path) -> None:
    archive = tmp_path / "wrong-model.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("chronos-2/config.json", "{}")
        handle.writestr("chronos-2/model.safetensors", b"not-the-official-model")
    with pytest.raises(ValueError, match="checksum mismatch"):
        safe_extract_model(archive, tmp_path / "model")


def test_model_archive_accepts_verified_weight_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"verified-test-model"
    import hashlib

    monkeypatch.setattr(chronos_setup_module, "OFFICIAL_MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    archive = tmp_path / "verified-model.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("chronos-2/config.json", "{}")
        handle.writestr("chronos-2/model.safetensors", payload)
    destination = tmp_path / "model"
    safe_extract_model(archive, destination)
    assert (destination / "config.json").exists()
    assert (destination / "model.safetensors").read_bytes() == payload


def test_cleanup_deletes_inputs_only_after_successful_state(tmp_path: Path) -> None:
    (tmp_path / "runtime").mkdir()
    (tmp_path / "models" / "chronos2").mkdir(parents=True)
    (tmp_path / "evidence" / "model_validation").mkdir(parents=True)
    (tmp_path / "chronos_input").mkdir()
    (tmp_path / "training_data").mkdir()
    (tmp_path / "runtime" / "chronos2_setup_state.json").write_text(
        json.dumps({"status": "success", "cleanup_pending": True}), encoding="utf-8"
    )
    (tmp_path / "models" / "chronos2" / "setup_manifest.json").write_text(
        json.dumps({"status": "success", "cleanup_pending": True}), encoding="utf-8"
    )
    (tmp_path / "models" / "chronos2" / "routing.json").write_text("{}", encoding="utf-8")
    (tmp_path / "evidence" / "model_validation" / "chronos2_model_comparison.json").write_text("{}", encoding="utf-8")
    (tmp_path / "chronos_input" / "model.zip").write_bytes(b"model")
    (tmp_path / "training_data" / "dataset.zip").write_bytes(b"dataset")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "cleanup_chronos2_inputs.py"), "--project-root", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not list((tmp_path / "chronos_input").glob("*.zip"))
    assert not list((tmp_path / "training_data").glob("*.zip"))
    state = json.loads((tmp_path / "runtime" / "chronos2_setup_state.json").read_text(encoding="utf-8"))
    assert state["cleanup_pending"] is False
    assert len(state["deleted_inputs"]) == 2


def test_cleanup_retains_inputs_when_setup_failed(tmp_path: Path) -> None:
    (tmp_path / "runtime").mkdir()
    (tmp_path / "chronos_input").mkdir()
    archive = tmp_path / "chronos_input" / "model.zip"
    archive.write_bytes(b"model")
    (tmp_path / "runtime" / "chronos2_setup_state.json").write_text(
        json.dumps({"status": "failed"}), encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "cleanup_chronos2_inputs.py"), "--project-root", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert archive.exists()


def _variant_rows(forecast: float, actual: float = 100.0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for horizon in ("30_minutes", "2_hours", "6_hours", "24_hours"):
        rows.append({
            "facility": "Facility", "origin": "2026-03-01T00:00:00", "horizon": horizon,
            "actual": actual, "forecast": forecast, "upper": forecast, "limit": 200.0,
            "latency_ms_per_series": 1.0,
        })
    return rows


def test_variant_selection_prefers_lora_when_validation_mae_improves() -> None:
    variant, evidence = choose_deployment_variant(
        {"chronos_zero_shot": _variant_rows(90.0), "chronos_lora": _variant_rows(95.0)},
        {"minimum_finetuned_validation_mae_improvement_percent": 0.0, "maximum_recall_drop": 0.02},
    )
    assert variant == "finetuned"
    assert evidence["validation_mae_improvement_percent"] > 0


def test_variant_selection_retains_zero_shot_when_lora_is_worse() -> None:
    variant, evidence = choose_deployment_variant(
        {"chronos_zero_shot": _variant_rows(95.0), "chronos_lora": _variant_rows(80.0)},
        {"minimum_finetuned_validation_mae_improvement_percent": 0.0, "maximum_recall_drop": 0.02},
    )
    assert variant == "base"
    assert evidence["validation_mae_improvement_percent"] < 0


def test_checkpoint_detection_distinguishes_full_model_and_lora_adapter(tmp_path: Path) -> None:
    full_model = tmp_path / "full"
    full_model.mkdir()
    (full_model / "config.json").write_text("{}", encoding="utf-8")
    (full_model / "model.safetensors").write_bytes(b"weights")
    assert _is_complete_model_checkpoint(full_model)
    assert not _is_lora_adapter_checkpoint(full_model)

    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"adapter")
    assert _is_lora_adapter_checkpoint(adapter)
    assert not _is_complete_model_checkpoint(adapter)
