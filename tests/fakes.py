"""In-memory fake of HikvisionIsapiClient for Home Assistant tests."""

from __future__ import annotations

from typing import Any, ClassVar

from custom_components.hikvision_away.api import (
    Channel,
    DeviceInfo,
    LinkageState,
)


class FakeClient:
    """Small stateful stand-in for the real NVR client."""

    instances: ClassVar[list[FakeClient]] = []
    serial = "TEST-SERIAL"
    device_name = "Test NVR"
    model = "DS-7808N-TEST"
    firmware = "V4.85.010"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.host = kwargs.get("host") or (args[0] if args else "192.0.2.1")
        self.port = int(kwargs.get("port", 80))
        self.base_url = f"http://{self.host}:{self.port}"
        self.upload_center: dict[int, bool] = {
            1: True,
            2: True,
            7: False,
            8: False,
        }
        self.closed = False
        self.channels = [
            Channel(id=1, name="前院", ip_address="192.0.2.10", model="CAM-1"),
            Channel(id=2, name="后院", ip_address="192.0.2.11", model="CAM-2"),
            Channel(id=7, name="餐厅", ip_address="192.0.2.17", model="CAM-7"),
            Channel(id=8, name="客厅", ip_address="192.0.2.18", model="CAM-8"),
        ]
        FakeClient.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances.clear()

    @classmethod
    def latest(cls) -> FakeClient:
        return cls.instances[-1]

    def get_device_info(self) -> DeviceInfo:
        return DeviceInfo(
            name=self.device_name,
            model=self.model,
            serial_number=self.serial,
            firmware_version=self.firmware,
            device_type="NVR",
        )

    def get_channels(self) -> list[Channel]:
        return list(self.channels)

    def get_linkage_state(self, channel: int) -> LinkageState:
        enabled = self.upload_center[channel]
        methods = ("record", "center") if enabled else ("record",)
        return LinkageState(
            upload_center=enabled,
            notification_methods=methods,
            raw_xml="",
        )

    def set_upload_center(self, channel: int, enabled: bool) -> bool:
        changed = self.upload_center[channel] is not enabled
        self.upload_center[channel] = enabled
        return changed

    def close(self) -> None:
        self.closed = True
