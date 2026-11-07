"""Constants for the FairGrow integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "fairgrow"
VERSION: Final = "0.3.0"

DEFAULT_URL: Final = "https://fairgrow.eu"
API_PATH: Final = "/wp-json/fg-ha/v1"

CONF_URL: Final = "url"
CONF_CODE: Final = "code"
CONF_TOKEN: Final = "token"
CONF_ADAPTER_ID: Final = "adapter_id"
CONF_ENTITIES: Final = "entities"
CONF_ADVANCED: Final = "advanced"
CONF_SHARE_AUTOMATIONS: Final = "share_automations"
CONF_SHARE_SCRIPTS: Final = "share_scripts"
CONF_CONTROL: Final = "control"

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

# Sharing the user's own automations and scripts (opt-in, see automations.py). The files
# are checked for a change every minute; the list is sent again when they change, when
# FairGrow asks for it, and otherwise every six hours.
AUTOMATION_FILE: Final = "automations.yaml"
SCRIPT_FILE: Final = "scripts.yaml"
AUTOMATIONS_CHECK_INTERVAL: Final = 60
AUTOMATIONS_RESEND_INTERVAL: Final = 6 * 3600
MAX_AUTOMATIONS: Final = 500
MAX_AUTOMATIONS_BYTES: Final = 1_500_000

# Letting FairGrow switch lights and switches (opt-in per entity, see commands.py). While
# at least one entity is on that list, the integration asks FairGrow every tick whether
# there is something to do. A command older than COMMAND_MAX_AGE is refused: a lamp that
# switches on five minutes late is worse than one that does not switch at all.
CONTROL_DOMAINS: Final = ["light", "switch"]
CONTROL_SERVICES: Final = ["turn_on", "turn_off", "toggle"]
LIGHT_DATA_KEYS: Final = [
    "brightness",
    "brightness_pct",
    "color_temp_kelvin",
    "rgb_color",
    "hs_color",
    "xy_color",
    "transition",
]
COMMAND_MAX_AGE: Final = 120
COMMAND_TIMEOUT: Final = 10
MAX_COMMANDS: Final = 20

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
