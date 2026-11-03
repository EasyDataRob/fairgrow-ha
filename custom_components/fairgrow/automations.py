"""Read the user's own automations and scripts, so that FairGrow can offer to take them over.

This only reads. FairGrow gets the definitions, never the power to run anything here:
taking one over on fairgrow.eu makes a copy there, switched off, and leaves the original
in Home Assistant untouched.

Why the files and not the loaded automations: Home Assistant fills in `!secret` and
`!include` while loading, so the loaded configuration can carry passwords and tokens. The
files are read with a loader that keeps every tag as literal text (`!secret wifi_pw`), so
a secret never leaves Home Assistant. Automations made in the UI all live in these two
files; whatever sits in packages or other includes is counted as not shared.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import yaml

from .const import AUTOMATION_FILE, MAX_AUTOMATIONS, MAX_AUTOMATIONS_BYTES, SCRIPT_FILE


class _TagsAsText(yaml.SafeLoader):
    """SafeLoader that turns every `!tag value` into the string "!tag value"."""


def _tag_as_text(loader: _TagsAsText, suffix: str, node: yaml.Node) -> str:
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
        return f"!{suffix} {value}".strip()
    return f"!{suffix}"


_TagsAsText.add_multi_constructor("!", _tag_as_text)


def _load(path: str) -> tuple[Any, str]:
    """(data, status). Status is ok, missing, empty or an error text."""
    if not os.path.isfile(path):
        return None, "missing"
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.load(fh, Loader=_TagsAsText)  # noqa: S506 - SafeLoader subclass
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as err:
        return None, f"error: {str(err).splitlines()[0][:200]}"
    if data is None:
        return None, "empty"
    return data, "ok"


def _plain(value: Any) -> Any:
    """Make the data JSON-safe (dates and odd scalars become text)."""
    return json.loads(json.dumps(value, default=str))


def fingerprint(config_dir: str) -> str:
    """Changes when either file changes; cheap enough to check every minute."""
    parts = []
    for name in (AUTOMATION_FILE, SCRIPT_FILE):
        try:
            st = os.stat(os.path.join(config_dir, name))
            parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{name}:-")
    return "|".join(parts)


def read_all(config_dir: str) -> dict[str, Any]:
    """The payload for POST /automations. Runs in the executor (file I/O)."""
    items: list[dict[str, Any]] = []
    sources: dict[str, str] = {}

    data, sources[AUTOMATION_FILE] = _load(os.path.join(config_dir, AUTOMATION_FILE))
    if data is not None and not isinstance(data, list):
        sources[AUTOMATION_FILE] = "error: not a list"
        data = None
    for i, auto in enumerate(data or []):
        if not isinstance(auto, dict):
            continue
        auto_id = str(auto.get("id") or f"zonder-id-{i + 1}")
        items.append(
            {
                "id": auto_id,
                "naam": str(auto.get("alias") or auto_id),
                "soort": "automation",
                # Made by FairGrow through the bridge; taking those over makes no sense.
                "van_ons": auto_id.startswith("fairgrow_"),
                "definitie": _plain(auto),
            }
        )

    data, sources[SCRIPT_FILE] = _load(os.path.join(config_dir, SCRIPT_FILE))
    if data is not None and not isinstance(data, dict):
        sources[SCRIPT_FILE] = "error: not a mapping"
        data = None
    for key, script in (data or {}).items():
        if not isinstance(script, dict):
            continue
        definition = _plain(script)
        definition.setdefault("alias", str(key))
        definition["id"] = str(key)
        items.append(
            {
                "id": f"script.{key}",
                "naam": str(script.get("alias") or key),
                "soort": "script",
                "van_ons": str(key).startswith("fairgrow_"),
                "definitie": definition,
            }
        )

    total = len(items)
    kept: list[dict[str, Any]] = []
    size = 0
    for item in items[:MAX_AUTOMATIONS]:
        n = len(json.dumps(item))
        if size + n > MAX_AUTOMATIONS_BYTES:
            break
        kept.append(item)
        size += n
    return {
        "automations": kept,
        "totaal": total,
        "afgekapt": total - len(kept),
        "bronnen": sources,
        "vingerafdruk": hashlib.sha256(
            json.dumps(kept, sort_keys=True).encode()
        ).hexdigest()[:16],
    }
