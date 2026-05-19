"""Constants for the Alliant Energy integration."""
from dataclasses import dataclass
from typing import Callable, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfEnergy,
    EntityCategory,
)

DOMAIN = "alliant_energy"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
# Mapping of discovered meters, keyed by meter number, stored on the config
# entry data so we don't have to re-discover on every restart.
CONF_METERS = "meters"
# List of meter numbers the user chose to import. Stored in entry options so
# it can be changed later via the options flow.
CONF_SELECTED_METERS = "selected_meters"

# Storage constants
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_auth_store"

# Update interval (in seconds) - 1 hour
UPDATE_INTERVAL = 3600

@dataclass
class AlliantEntityDescription(SensorEntityDescription):
    """Class describing Alliant Energy sensor entities."""
    value_fn: Callable[[Any], Any] = None

ELEC_SENSORS = (
    AlliantEntityDescription(
        key="elec_usage_to_date",
        name="Current Bill Electric Usage To Date",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_fn=lambda data: data.usage_to_date,
    ),
    AlliantEntityDescription(
        key="elec_forecasted_usage",
        name="Current Bill Electric Forecasted Usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_fn=lambda data: data.forecasted_usage,
    ),
    AlliantEntityDescription(
        key="elec_typical_usage",
        name="Typical Monthly Electric Usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_fn=lambda data: data.typical_usage,
    ),
    AlliantEntityDescription(
        key="elec_cost_to_date",
        name="Current Bill Electric Cost To Date",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: data.cost_to_date,
    ),
    AlliantEntityDescription(
        key="elec_forecasted_cost",
        name="Current Bill Electric Forecasted Cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: data.forecasted_cost,
    ),
    AlliantEntityDescription(
        key="elec_typical_cost",
        name="Typical Monthly Electric Cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: data.typical_cost,
    ),
    AlliantEntityDescription(
        key="elec_cost_per_kwh",
        name="Electric Cost per kWh",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.cost_per_kwh,
    ),
    AlliantEntityDescription(
        key="elec_start_date",
        name="Current Bill Electric Start Date",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.start_date,
    ),
    AlliantEntityDescription(
        key="elec_end_date",
        name="Current Bill Electric End Date",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.end_date,
    ),
)
