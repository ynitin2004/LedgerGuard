"""Deterministic risk evaluation: features -> rules + model -> policy (plan section 7).

Pure code (no I/O). The API, the training script and decision replay all call the same
functions, so online/offline parity holds by construction.
"""
