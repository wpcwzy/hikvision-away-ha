"""Shared runtime data for the Hikvision NVR Away Arm integration."""

from __future__ import annotations

from dataclasses import dataclass

from .api import DeviceInfo, HikvisionIsapiClient
from .coordinator import HikvisionAwayCoordinator


@dataclass(slots=True)
class HikvisionAwayRuntimeData:
    """Objects shared by the switch platform and diagnostics."""

    client: HikvisionIsapiClient
    coordinator: HikvisionAwayCoordinator
    device: DeviceInfo
    channels: tuple[int, ...]
    channel_names: tuple[str, ...]
    name: str
