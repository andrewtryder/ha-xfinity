"""Shared entity base for Xfinity."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONFIGURATION_URL, DOMAIN
from .coordinator import XfinityCoordinator


class XfinityEntity(CoordinatorEntity[XfinityCoordinator]):
    """Entity backed by one section of the coordinator data."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: XfinityCoordinator, entry_id: str, section: str, key: str) -> None:
        super().__init__(coordinator)
        self._section = section
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Xfinity",
            manufacturer="Comcast",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=CONFIGURATION_URL,
        )

    def section_data(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        section = data.get(self._section) or {}
        return section if isinstance(section, dict) else {}

    @property
    def available(self) -> bool:
        return super().available and self.section_data().get("status") != "error"
