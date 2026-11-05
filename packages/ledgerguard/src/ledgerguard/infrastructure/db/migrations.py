"""Helpers relating the running code to the Alembic migration history."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def alembic_config(ini_path: Path, database_url: str | None = None) -> Config:
    cfg = Config(str(ini_path))
    if database_url is not None:
        # ConfigParser interpolation: '%' in passwords must be escaped.
        cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def expected_head(ini_path: Path) -> str | None:
    """Return the single head revision this code expects, or None if migrations are absent."""
    if not ini_path.exists():
        return None
    heads = ScriptDirectory.from_config(alembic_config(ini_path)).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected exactly one alembic head, found {heads}")
    return heads[0]
