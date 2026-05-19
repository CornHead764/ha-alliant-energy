"""The Alliant Energy integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .client import (
    AlliantEnergyClient,
    AlliantEnergyData,
    AlliantEnergyMeter,
)
from .const import (
    DOMAIN,
    STORAGE_VERSION,
    STORAGE_KEY,
    UPDATE_INTERVAL,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_METERS,
    CONF_SELECTED_METERS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old (single-meter) config entries to the multi-meter schema."""
    if entry.version < 2:
        # The meter list can't be discovered here without a network round
        # trip, so just bump the version. async_setup_entry backfills the
        # meters lazily and selects all of them by default.
        hass.config_entries.async_update_entry(entry, version=2)
        _LOGGER.info("Migrated Alliant Energy config entry to version 2")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alliant Energy from a config entry."""
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    client = AlliantEnergyClient(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        store=store,
    )

    # Legacy entries (or freshly migrated ones) have no cached meter list.
    # Discover it once, persist it, and default to importing every meter.
    meters_meta: dict = entry.data.get(CONF_METERS) or {}
    if not meters_meta:
        discovered = await client.async_get_meters()
        meters_meta = {m.meter_number: m.to_dict() for m in discovered}
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_METERS: meters_meta},
        )

    selected = entry.options.get(CONF_SELECTED_METERS) or list(meters_meta)
    meters = [
        AlliantEnergyMeter.from_dict(meters_meta[mn])
        for mn in selected
        if mn in meters_meta
    ]

    if not meters:
        _LOGGER.error(
            "No valid meters selected for Alliant Energy entry %s", entry.entry_id
        )
        return False

    async def async_update_data() -> dict[str, AlliantEnergyData]:
        """Fetch data for every selected meter."""
        results: dict[str, AlliantEnergyData] = {}
        errors: list[str] = []
        for meter in meters:
            try:
                results[meter.meter_number] = await client.async_get_data(meter)
            except Exception as err:  # pylint: disable=broad-except
                errors.append(f"{meter.meter_number}: {err}")
                _LOGGER.warning(
                    "Failed to fetch data for meter %s: %s",
                    meter.meter_number,
                    err,
                )
        if not results:
            raise UpdateFailed(
                f"Failed to fetch data for all meters: {'; '.join(errors)}"
            )
        return results

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=UPDATE_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "meters": meters,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when the selected meters change via the options flow."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["client"].async_close()

    return unload_ok
