"""Dnevnik odluka: svaka odluka (sablon, preset, granice, primijenjeni default) se
biljezi i u Python logger i u listu koja ide u JSON metapodatke."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("fabagent")


class DecisionLog:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, category: str, message: str, **data: Any) -> None:
        entry = {"category": category, "message": message}
        if data:
            entry["data"] = data
        self.entries.append(entry)
        logger.info("[%s] %s%s", category, message, f" {data}" if data else "")

    def warn(self, message: str, **data: Any) -> None:
        entry = {"category": "upozorenje", "message": message}
        if data:
            entry["data"] = data
        self.entries.append(entry)
        logger.warning("[upozorenje] %s%s", message, f" {data}" if data else "")

    @property
    def warnings(self) -> list[str]:
        return [e["message"] for e in self.entries if e["category"] == "upozorenje"]
