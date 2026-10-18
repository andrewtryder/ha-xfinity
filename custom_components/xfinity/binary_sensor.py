"""Outage, maintenance, and past-due binary sensors."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription

from .coordinator import XfinityCoordinator
from .entity import XfinityEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class XfinityBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description plus the coordinator field it reads."""

    section: str
    field: str


SENSORS: tuple[XfinityBinarySensorDescription, ...] = (
    XfinityBinarySensorDescription(
        key="active_outage",
        translation_key="active_outage",
        section="outage",
        field="active_outage",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    XfinityBinarySensorDescription(
        key="scheduled_maintenance",
        translation_key="scheduled_maintenance",
        section="outage",
        field="scheduled_maintenance",
    ),
    XfinityBinarySensorDescription(
        key="past_due_bill",
        translation_key="past_due_bill",
        section="bill",
        field="past_due_balance",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: XfinityCoordinator = entry.runtime_data
    async_add_entities(XfinityBinarySensor(coordinator, entry.entry_id, description) for description in SENSORS)


class XfinityBinarySensor(XfinityEntity, BinarySensorEntity):
    """True when the polled section reports that condition."""

    entity_description: XfinityBinarySensorDescription

    def __init__(self, coordinator: XfinityCoordinator, entry_id: str, description: XfinityBinarySensorDescription) -> None:
        super().__init__(coordinator, entry_id, description.section, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return bool(self.section_data().get(self.entity_description.field))
