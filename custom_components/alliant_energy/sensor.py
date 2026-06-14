"""Support for Alliant Energy sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util.dt import as_local

from .const import DOMAIN, ELEC_SENSORS, AlliantEntityDescription
from .client import AlliantEnergyData, AlliantEnergyMeter

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alliant Energy sensors based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DataUpdateCoordinator = data["coordinator"]
    meters: list[AlliantEnergyMeter] = data["meters"]

    entities = [
        AlliantEnergySensor(
            coordinator=coordinator,
            entry_id=entry.entry_id,
            meter=meter,
            description=description,
        )
        for meter in meters
        for description in ELEC_SENSORS
    ]

    async_add_entities(entities)

class AlliantEnergySensor(CoordinatorEntity, SensorEntity):
    """Representation of an Alliant Energy sensor for a single meter."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry_id: str,
        meter: AlliantEnergyMeter,
        description: AlliantEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

        self._meter_number = meter.meter_number
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{meter.meter_number}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry_id}_{meter.meter_number}")},
            "name": f"Alliant Energy Meter {meter.meter_number}",
            "manufacturer": "Alliant Energy",
            "model": "Usage Monitor",
        }

    @property
    def _meter_data(self) -> AlliantEnergyData | None:
        """Return this meter's slice of the coordinator data."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._meter_number)

    @property
    def available(self) -> bool:
        """Only available when this meter has data in the latest refresh."""
        return super().available and self._meter_data is not None

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        meter_data = self._meter_data
        if meter_data is None:
            return None
        return self.entity_description.value_fn(meter_data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attributes: dict[str, Any] = {}
        meter_data = self._meter_data
        if meter_data is None:
            return attributes

        attributes["meter_number"] = self._meter_number

        # Add last update times if available
        if meter_data.last_api_update:
            attributes["last_api_update"] = as_local(meter_data.last_api_update).isoformat()

        if meter_data.last_meter_read:
            attributes["last_meter_read"] = as_local(meter_data.last_meter_read).isoformat()

        # Add billing period dates if available
        if meter_data.start_date:
            attributes["billing_period_start"] = as_local(meter_data.start_date).isoformat()

        if meter_data.end_date:
            attributes["billing_period_end"] = as_local(meter_data.end_date).isoformat()

        # For cost sensors, add estimated flag if applicable
        if self.entity_description.key in ["elec_cost_to_date", "elec_forecasted_cost"]:
            attributes["is_estimated"] = meter_data.is_cost_estimated

        # For cost per kWh sensor, expose the daily customer charge that is
        # backed out when deriving the rate. (The rate itself comes from the
        # most recent billing period with positive net consumption, not a
        # fixed window, so no calculation_period_* attributes are reported.)
        if self.entity_description.key == "elec_cost_per_kwh":
            attributes["customer_charge_per_day"] = meter_data.customer_charge

        return attributes
