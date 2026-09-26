from __future__ import annotations

from typing import Any

REQUIRED_PANELS = ("front", "back", "label")


class CaptureManifest:
    """Track required front/back/label captures and block completion until all are present."""

    def __init__(self, required_panels: tuple[str, ...] | list[str] | None = None) -> None:
        self.required_panels = tuple((required_panels or REQUIRED_PANELS))
        self.captured: dict[str, dict[str, Any]] = {}

    def normalize_panel(self, panel_name: str | None) -> str:
        if panel_name is None:
            return ""
        key = str(panel_name).strip().lower()
        if key in {"front", "front panel", "front-side", "front side"}:
            return "front"
        if key in {"back", "back panel", "back-side", "back side"}:
            return "back"
        if key in {"label", "product label", "front label", "label panel"}:
            return "label"
        return key

    def record(self, panel_name: str | None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        panel_key = self.normalize_panel(panel_name)
        if not panel_key:
            return {"status": "ignored", "panel": None, "complete": self.is_complete()}
        self.captured[panel_key] = {"panel": panel_key, "metadata": metadata or {}, "captured_at": "now"}
        return self.get_status()

    def is_complete(self) -> bool:
        return all(panel in self.captured for panel in self.required_panels)

    def missing_panels(self) -> list[str]:
        return [panel for panel in self.required_panels if panel not in self.captured]

    def get_status(self) -> dict[str, Any]:
        missing = self.missing_panels()
        return {
            "complete": not missing,
            "required_panels": list(self.required_panels),
            "captured_panels": sorted(self.captured),
            "missing_panels": missing,
            "officer_review_required": bool(missing),
        }
