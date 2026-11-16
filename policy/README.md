# policy/

Versioned decision thresholds (`v1.0.yaml` = thresholds `1.0`). Combined with the rules version and the model version into `policy_version = rules@x.y+model@vN+thresholds@z`, which is stored on every transaction and decision.

`decline` if any hard rule fires or combined ≥ `decline_threshold`; `review` if any soft rule fires or combined ≥ `review_threshold`; otherwise `approve`. `combined = clamp(model_score + Σ soft weights, 0, 1)`. Released files are immutable and pinned by SHA-256 in tests (ADR-020).
