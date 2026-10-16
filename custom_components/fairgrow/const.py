"""Constants for the FairGrow integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "fairgrow"
VERSION: Final = "0.1.0"

DEFAULT_URL: Final = "https://fairgrow.eu"
API_PATH: Final = "/wp-json/fg-ha/v1"

CONF_URL: Final = "url"
CONF_CODE: Final = "code"
CONF_TOKEN: Final = "token"
CONF_ADAPTER_ID: Final = "adapter_id"
CONF_ENTITIES: Final = "entities"
CONF_ADVANCED: Final = "advanced"

# How often queued readings are pushed, and how often a heartbeat is sent. The
# platform may ask for a different push interval in its answers.
PUSH_INTERVAL: Final = 60
HEARTBEAT_INTERVAL: Final = 300
TICK_INTERVAL: Final = 10

# Readings kept while FairGrow cannot be reached. Oldest go first when full.
MAX_QUEUE: Final = 20000
MAX_BATCH: Final = 500
# Backoff after a failed push, in seconds: doubles from MIN to MAX.
BACKOFF_MIN: Final = 30
BACKOFF_MAX: Final = 900
# Re-announce the shared entities every so often, so that names and areas
# changed in Home Assistant follow on FairGrow.
REANNOUNCE_INTERVAL: Final = 6 * 3600

STORAGE_VERSION: Final = 1

# Device classes FairGrow can use. The selector in the config flow only offers
# entities with one of these, so that a user is not asked to choose from every
# sensor in the house.
SENSOR_DEVICE_CLASSES: Final = [
    "temperature",
    "humidity",
    "moisture",
    "illuminance",
    "battery",
    "pressure",
    "atmospheric_pressure",
    "carbon_dioxide",
    "pm1",
    "pm25",
    "pm10",
    "power",
    "energy",
    "voltage",
    "current",
    "ph",
    "weight",
    "wind_direction",
    "wind_speed",
    "precipitation",
    "precipitation_intensity",
    "irradiance",
    "conductivity",
    "volatile_organic_compounds",
    "volatile_organic_compounds_parts",
    "water",
    "volume_flow_rate",
    "distance",
]

BINARY_SENSOR_DEVICE_CLASSES: Final = [
    "occupancy",
    "motion",
    "presence",
    "opening",
    "door",
    "window",
    "garage_door",
    "moisture",
    "power",
    "plug",
    "running",
]
