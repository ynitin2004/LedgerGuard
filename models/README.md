# models/

Model artifacts as JSON (`lr-v1.json`): feature order, standardisation, coefficients, intercept, held-out metrics and training provenance. There are no pickles. The API scores them in pure Python (ADR-020).

- Produced by `scripts/train_model.py` from synthetic data (`make train`, or `./scripts/dev.ps1 train` on Windows, which uses a Linux container, ADR-021).
- Registered with `scripts/register_model.py models/lr-v1.json --activate`, which stores the full artifact and its sha256 in `model_registry`. Versions are immutable: a retrained model with different coefficients must be `v2`.
- CI retrains and checks that the coefficients and metrics match this file (`tests/unit/test_model_training.py`). Metrics are explained in docs/MODEL_CARD.md.
