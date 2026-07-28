# Chronos-2 model input

Place exactly one ZIP containing the official `amazon/chronos-2` model files in this folder.
The ZIP may contain a top-level `chronos-2` folder. It must include `config.json` and the official `model.safetensors` file. The installer verifies the published SHA-256 before the model is loaded.

Do not place passwords, Gmail credentials, or unrelated archives here.
The setup deletes this ZIP only after extraction, LoRA training or accepted zero-shot fallback, benchmarking, routing generation, and verification complete successfully.
