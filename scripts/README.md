# scripts/

- `dev.ps1`: Windows task runner, same targets as the `Makefile` (M0).
- `create_api_client.py`: create an API client and print its key once; connects as the schema owner (M1). `./scripts/dev.ps1 api-client -Name demo` / `make api-client NAME=demo`.
- Planned: `gen_synthetic.py`, `train_model.py` (M2), `seed.py` (M2), `replay_dlq.py` (M3), `mint_dev_jwt.py` (M5).
