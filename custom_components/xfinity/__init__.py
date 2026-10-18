"""The Xfinity integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import STALE_ENTRY_KEYS
from .coordinator import XfinityCoordinator

type XfinityConfigEntry = ConfigEntry[XfinityCoordinator]

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_migrate_entry(hass: HomeAssistant, entry: XfinityConfigEntry) -> bool:
    """Keep a 0.2.0 refresh token and collect Browserless credentials on reauth."""
    if entry.version < 2:
        hass.config_entries.async_update_entry(entry, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: XfinityConfigEntry) -> bool:
    """Set up Xfinity from a config entry."""
    stale = [key for key in STALE_ENTRY_KEYS if key in entry.data]
    if stale:
        hass.config_entries.async_update_entry(entry, data={key: value for key, value in entry.data.items() if key not in stale})
    coordinator = XfinityCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: XfinityConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
