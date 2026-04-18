"""Metadata template loader for the Quran-overlay workflow.

Loads a JSON metadata file describing a publish-ready Quran recitation video.
The description template is stored verbatim and rendered later in the pipeline,
so auto-extracted vars (surah name, reciter from YouTube) can be merged in.
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
    """Validated metadata for an overlay video upload.

    The description is stored as a template + user-provided vars; call
    `render_description(extra_vars)` to produce the final string. User vars
    override extra vars on key conflict, so auto-extracted values are only
    used when the user didn't pin them manually.
    """

    title: str
    tags: List[str] = field(default_factory=list)
    category_id: str = "22"
    privacy_status: str = DEFAULT_PRIVACY
    logo_path: Optional[Path] = None
    logo_position: str = DEFAULT_LOGO_POSITION
    description_template: Optional[str] = None
    description_vars: dict = field(default_factory=dict)
    description_literal: Optional[str] = None

    def render_description(self, extra_vars: Optional[dict] = None) -> str:
        if self.description_template:
            return self._substitute(
                self.description_template, extra_vars, field="description"
            )
        return self.description_literal or ""

    def render_title(self, extra_vars: Optional[dict] = None) -> str:
        """Render the title as a Template, so `$detected_surah` etc. work here too."""
        return self._substitute(self.title, extra_vars, field="title")

    def _substitute(self, template: str, extra_vars: Optional[dict], field: str) -> str:
        merged = {**(extra_vars or {}), **self.description_vars}
        try:
            return Template(template).substitute(merged)
        except KeyError as e:
            raise OverlayError(
                f"Missing variable for {field} placeholder ${e.args[0]}",
                "Either add it to description_vars in the metadata JSON, or "
                "ensure auto-extraction supplies it (e.g. $detected_surah "
                "requires a recognizable surah name in the audio URL's title).",
            )
        except ValueError as e:
            raise OverlayError(f"Malformed {field} template", str(e))


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
    literal_desc: Optional[str] = None
    if template_str is not None:
        if not isinstance(template_str, str):
            raise OverlayError("'description_template' must be a string")
        if not isinstance(variables, dict):
            raise OverlayError("'description_vars' must be a JSON object")
    else:
        literal_desc = raw.get("description", "")
        if not isinstance(literal_desc, str):
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
        tags=tags,
        category_id=category_id,
        privacy_status=privacy_status,
        logo_path=logo_path,
        logo_position=logo_position,
        description_template=template_str,
        description_vars=variables if template_str else {},
        description_literal=literal_desc,
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
