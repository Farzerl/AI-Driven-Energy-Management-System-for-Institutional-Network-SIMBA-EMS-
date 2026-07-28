from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    state_path = root / "runtime" / "power_quality_setup_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("status") != "success" or not bool(dict(state.get("training", {})).get("succeeded", False)):
        raise SystemExit("Refusing cleanup because required power-quality training did not complete successfully.")
    deleted: list[str] = []
    for path in sorted((root / "training_data").glob("*.zip")):
        path.unlink()
        deleted.append(path.relative_to(root).as_posix())
    if bool(state.get("source_model_extracted_from_zip", False)):
        for path in sorted((root / "chronos_input").glob("*.zip")):
            path.unlink()
            deleted.append(path.relative_to(root).as_posix())
    state["cleanup_pending"] = False
    state["deleted_inputs"] = deleted
    temporary = state_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(state_path)
    print(json.dumps({"status": "success", "deleted": deleted}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
