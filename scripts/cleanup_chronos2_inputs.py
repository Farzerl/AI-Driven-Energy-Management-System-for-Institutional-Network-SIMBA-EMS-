from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def atomic_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    root = args.project_root.resolve()
    state_path = root / "runtime" / "chronos2_setup_state.json"
    metrics_path = root / "evidence" / "model_validation" / "chronos2_model_comparison.json"
    routing_path = root / "models" / "chronos2" / "routing.json"
    if not state_path.exists():
        print("Chronos-2 setup state is missing; source ZIPs were not deleted.", file=sys.stderr)
        return 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("status") != "success":
        print("Chronos-2 setup has not completed successfully; source ZIPs were not deleted.", file=sys.stderr)
        return 1
    config_path = root / "config" / "chronos2_training.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    lora_required = bool(dict(config.get("lora", {})).get("enabled", False))
    if lora_required and not bool(dict(state.get("training", {})).get("succeeded", False)):
        print("LoRA fine-tuning is enabled but did not complete; source ZIPs were not deleted.", file=sys.stderr)
        return 1
    if not metrics_path.exists() or not routing_path.exists():
        print("Chronos-2 evidence or routing is missing; source ZIPs were not deleted.", file=sys.stderr)
        return 1
    deleted: list[str] = []
    for folder_name in ("chronos_input", "training_data"):
        folder = root / folder_name
        archives = sorted(folder.glob("*.zip"))
        if len(archives) > 1:
            print(f"Expected at most one ZIP in {folder_name}; found {len(archives)}. Nothing was deleted.", file=sys.stderr)
            return 1
        for archive in archives:
            archive.unlink()
            deleted.append(archive.relative_to(root).as_posix())
    state["cleanup_pending"] = False
    state["inputs_deleted_utc"] = datetime.now(timezone.utc).isoformat()
    state["deleted_inputs"] = deleted
    atomic_json(state_path, state)
    manifest_path = root / "models" / "chronos2" / "setup_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({key: state[key] for key in ("cleanup_pending", "inputs_deleted_utc", "deleted_inputs")})
        atomic_json(manifest_path, manifest)
    print(json.dumps({"status": "success", "deleted": deleted}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
