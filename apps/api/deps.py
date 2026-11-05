"""FastAPI dependency providers.

Resources are created once in the app lifespan and stored on ``app.state``;
dependencies only hand them out. This is the composition root's seam: tests
replace what is on ``app.state`` instead of patching modules.
"""

from __future__ import annotations

from fastapi import Request

from ledgerguard.config import Settings
from ledgerguard.infrastructure.health import Probe


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_probes(request: Request) -> list[Probe]:
    probes: list[Probe] = request.app.state.probes
    return probes
