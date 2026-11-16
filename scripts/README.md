# scripts/

- `dev.ps1`: Windows task runner, same targets as the `Makefile` (M0).
- `create_api_client.py`: create an API client and print its key once (owner connection, M1).
- `train_model.py`: train the LR model on synthetic data and write `models/lr-v1.json` (M2; needs numpy).
- `register_model.py`: register a model artifact in `model_registry`, optionally activating it (M2).
- `seed_history.py`: new client + 30 days of synthetic history, for demos and load tests (M2).
- `verify_decisions.py`: replay every stored decision and fail on any mismatch (M2).
- Planned: `replay_dlq.py` (M3), `mint_dev_jwt.py` (M5).
