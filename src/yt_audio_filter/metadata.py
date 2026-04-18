"""Metadata template loader for the Quran-overlay workflow.

Loads a JSON metadata file describing a publish-ready Quran recitation video:
title, description (rendered from a Template with $var placeholders), tags,
category_id, privacy_status, and optional logo path/position.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import List, Optional

from .exceptions import OverlayError


VALID_PRIVACY = {"public", "unlisted", "private"}
VALID_LOGO_POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right"}
DEFAULT_PRIVACY = "private"
DEFAULT_LOGO_POSITION = "top-left"


@dataclass
class OverlayMetadata:
    """Validated, fully-rendered metadata for an overlay video upload."""

    title: str
    description: str
    tags: List[str] = field(default_factory=list)
    category_id: str = "22"
    privacy_status: str = DEFAULT_PRIVACY
    logo_path: Optional[Path] = None
    logo_position: str = DEFAULT_LOGO_POSITION


def _render_description(template_str: str, variables: dict) -> str:
    try:
        return Template(template_str).substitute(variables)
    except KeyError as e:
        raise OverlayError(
            f"Missing description_vars entry for placeholder ${e.args[0]}",
            f"Template references ${e.args[0]} but description_vars has no such key.",
        )
    except ValueError as e:
        raise OverlayError("Malformed description_template", str(e))


def load_metadata(path: Path) -> OverlayMetadata:
    """Load and validate a metadata JSON file.

    Relative `logo_path` values are resolved against the JSON file's
    directory, keeping metadata files portable across folders.
    """
    path = Path(path)
    if not path.exists():
        raise OverlayError(f"Metadata file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise OverlayError(f"Invalid JSON in metadata file: {path}", str(e))

    if not isinstance(raw, dict):
        raise OverlayError(f"Metadata root must be a JSON object: {path}")

    title = raw.get("title")
    if not title or not isinstance(title, str):
        raise OverlayError("Metadata is missing a non-empty string 'title'")

    template_str = raw.get("description_template")
    variables = raw.get("description_vars", {})
    if template_str is not None:
        if not isinstance(template_str, str):
            raise OverlayError("'description_template' must be a string")
        if not isinstance(variables, dict):
            raise OverlayError("'description_vars' must be a JSON object")
        description = _render_description(template_str, variables)
    else:
        description = raw.get("description", "")
        if not isinstance(description, str):
            raise OverlayError("'description' must be a string")

    tags = raw.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise OverlayError("'tags' must be a list of strings")

    category_id = str(raw.get("category_id", "22"))

    privacy_status = raw.get("privacy_status", DEFAULT_PRIVACY)
    if privacy_status not in VALID_PRIVACY:
        raise OverlayError(
            f"Invalid privacy_status: {privacy_status!r}",
            f"Must be one of: {sorted(VALID_PRIVACY)}",
        )

    logo_path_raw = raw.get("logo_path")
    logo_path: Optional[Path] = None
    if logo_path_raw:
        if not isinstance(logo_path_raw, str):
            raise OverlayError("'logo_path' must be a string")
        candidate = Path(logo_path_raw)
        if not candidate.is_absolute():
            candidate = (path.parent / candidate).resolve()
        logo_path = candidate

    logo_position = raw.get("logo_position", DEFAULT_LOGO_POSITION)
    if logo_position not in VALID_LOGO_POSITIONS:
        raise OverlayError(
            f"Invalid logo_position: {logo_position!r}",
            f"Must be one of: {sorted(VALID_LOGO_POSITIONS)}",
        )

    return OverlayMetadata(
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        privacy_status=privacy_status,
        logo_path=logo_path,
        logo_position=logo_position,
    )


def apply_cli_overrides(
    metadata: OverlayMetadata,
    logo: Optional[Path],
    logo_position: Optional[str],
) -> OverlayMetadata:
    """Apply CLI-flag overrides on top of loaded metadata.

    Only logo fields are CLI-overridable per the design.
    """
    if logo is not None:
        metadata.logo_path = logo
    if logo_position is not None:
        if logo_position not in VALID_LOGO_POSITIONS:
            raise OverlayError(
                f"Invalid --logo-position: {logo_position!r}",
                f"Must be one of: {sorted(VALID_LOGO_POSITIONS)}",
            )
        metadata.logo_position = logo_position
    return metadata
