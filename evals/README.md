# Verified Completion evaluation

This benchmark measures the product claim rather than model eloquence. A case is
solved only when the task completes, every acceptance criterion is mapped to passed
execution evidence, the Receipt is intact, expected artifacts exist, and a later
Receipt replay passes. A completed task that fails any of those conditions is counted
as a false acceptance.

Run it against an already-running API with configured real providers:

```bash
cd apps/api
uv run --frozen --extra dev python scripts/evaluate_verified_completion.py \
  --allow-model-spend \
  --output ../../evals/results/local.json
```

`--allow-model-spend` is mandatory because every case can consume provider tokens.
The committed suite is intentionally small and deterministic enough to compare loop,
prompt, model, and budget changes without conflating them with web availability.
