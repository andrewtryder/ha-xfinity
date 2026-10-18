"""Bandwidth, outage, and bill sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.helpers.entity import EntityCategory

from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import OUTAGE_STATUS_OPTIONS
from .coordinator import XfinityCoordinator
from .entity import XfinityEntity

PARALLEL_UPDATES = 0


def _value(field: str) -> Callable[[dict[str, Any]], Any]:
    return lambda section: section.get(field)


def _as_date(field: str) -> Callable[[dict[str, Any]], date | None]:
    def parse(section: dict[str, Any]) -> date | None:
        value = section.get(field)
        if not isinstance(value, str) or len(value) < 10:
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    return parse


def _outage_status(section: dict[str, Any]) -> str:
    status = section.get("status")
    if status in OUTAGE_STATUS_OPTIONS:
        return status
    return "unknown"


@dataclass(frozen=True, kw_only=True)
class XfinitySensorDescription(SensorEntityDescription):
    """Sensor description plus the coordinator section it reads."""

    section: str
    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[XfinitySensorDescription, ...] = (
    XfinitySensorDescription(
        key="data_used",
        translation_key="data_used",
        section="usage",
        value_fn=_value("used_gb"),
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DATA_SIZE,
        icon="mdi:download-network",
    ),
    XfinitySensorDescription(
        key="data_allowance",
        translation_key="data_allowance",
        section="usage",
        value_fn=_value("allowance_gb"),
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        icon="mdi:speedometer",
    ),
    XfinitySensorDescription(
        key="data_used_percent",
        translation_key="data_used_percent",
        section="usage",
        value_fn=_value("used_percent"),
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent",
    ),
    XfinitySensorDescription(
        key="usage_cycle_end",
        translation_key="usage_cycle_end",
        section="usage",
        value_fn=_as_date("end_date"),
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-end",
    ),
    XfinitySensorDescription(
        key="data_policy",
        translation_key="data_policy",
        section="usage",
        value_fn=_value("policy"),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:file-document-outline",
    ),
    XfinitySensorDescription(
        key="outage_status",
        translation_key="outage_status",
        section="outage",
        value_fn=_outage_status,
        device_class=SensorDeviceClass.ENUM,
        options=list(OUTAGE_STATUS_OPTIONS),
        icon="mdi:access-point-network",
    ),
    XfinitySensorDescription(
        key="bill_status",
        translation_key="bill_status",
        section="bill",
        value_fn=_value("billing_status"),
        icon="mdi:receipt-text",
    ),
    XfinitySensorDescription(
        key="amount_due",
        translation_key="amount_due",
        section="bill",
        value_fn=_value("amount_due"),
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:currency-usd",
    ),
    XfinitySensorDescription(
        key="bill_due_date",
        translation_key="bill_due_date",
        section="bill",
        value_fn=_as_date("due_date"),
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-clock",
    ),
    XfinitySensorDescription(
        key="past_due",
        translation_key="past_due",
        section="bill",
        value_fn=_value("past_due"),
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:calendar-alert",
    ),
)


async def async_setup_entry(
    hass,
    entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: XfinityCoordinator = entry.runtime_data
    async_add_entities(XfinitySensor(coordinator, entry.entry_id, description) for description in SENSORS)


class XfinitySensor(XfinityEntity, SensorEntity):
    """One value from an outage, usage, or bill poll."""

    entity_description: XfinitySensorDescription

    def __init__(self, coordinator: XfinityCoordinator, entry_id: str, description: XfinitySensorDescription) -> None:
        super().__init__(coordinator, entry_id, description.section, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.section_data())

    @property
    def extra_state_attributes(self):
        if self.entity_description.key != "outage_status":
            return None
        incidents = self.section_data().get("incidents") or []
        if not incidents or not isinstance(incidents[0], dict):
            return None
        first = incidents[0]
        return {
            "classification": first.get("classification"),
            "start": first.get("start"),
            "estimated_restoration": first.get("estimated_restoration"),
            "customer_message": first.get("customer_message"),
        }
