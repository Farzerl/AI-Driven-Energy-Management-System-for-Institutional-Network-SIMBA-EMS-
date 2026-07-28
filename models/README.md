# Model assets

This public source repository includes lightweight model configuration, routing and validation evidence. It does **not** include the following local Chronos-2 weight files:

```text
models/chronos-2-base/model.safetensors
models/chronos-2-finetuned/model.safetensors
models/chronos-2-power-quality-finetuned/model.safetensors
```

In the inventoried working repository, each weight file was approximately 456 MB and the model directory was approximately 1.37 GB. Committing them directly would exceed GitHub's normal per-file limit and would make the repository unnecessarily large.

## Full adjudication build

The full local demonstration build uses separately provisioned and verified model assets. Reviewers can inspect:

- the model adapters and routing logic under `src/live/`;
- lightweight routing and setup manifests in this directory;
- chronological model-comparison evidence under `evidence/model_validation/`;
- the model card and AI justification under `docs/`.

## Distribution rule

Do not upload model weights until all of the following are confirmed:

1. the model licence permits redistribution;
2. the institution approves the distribution method;
3. the destination uses an appropriate release store or controlled link;
4. hashes and expected folder paths are documented;
5. no private training data are embedded in the package.

Git LFS or a GitHub Release can solve file-size transport, but neither replaces the licensing and access review.
