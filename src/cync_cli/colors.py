"""Color parsing and Cync-friendly RGB helpers."""

from __future__ import annotations

import re

NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "orange": (255, 96, 0),
    "amber": (255, 160, 0),
    "yellow": (255, 255, 0),
    "lime": (128, 255, 0),
    "green": (0, 255, 0),
    "mint": (0, 255, 160),
    "cyan": (0, 255, 255),
    "teal": (0, 128, 128),
    "blue": (0, 0, 255),
    "indigo": (75, 0, 130),
    "violet": (128, 0, 255),
    "purple": (180, 0, 255),
    "magenta": (255, 0, 255),
    "pink": (255, 48, 128),
    "warm-white": (255, 180, 96),
    "daylight": (180, 220, 255),
    "white": (255, 255, 255),
}


def parse_rgb(value: str) -> tuple[int, int, int]:
    """Parse #RRGGBB, RRGGBB, or comma-separated decimal channels."""
    text = value.strip().lower()
    if text in NAMED_COLORS:
        return NAMED_COLORS[text]
    if text.startswith("#"):
        text = text[1:]
    if re.fullmatch(r"[0-9a-f]{6}", text):
        return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    parts = [part.strip() for part in text.split(",")]
    if len(parts) == 3 and all(re.fullmatch(r"\d+", part) for part in parts):
        channels = tuple(int(part) for part in parts)
        if all(0 <= channel <= 255 for channel in channels):
            return channels  # type: ignore[return-value]
        raise ValueError("RGB channels must each be from 0 to 255")
    known = ", ".join(NAMED_COLORS)
    raise ValueError(f"Invalid color {value!r}; use #RRGGBB, R,G,B, or a name ({known})")


def parse_percent(value: str, *, name: str = "value") -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number from 0 to 100") from exc
    if not 0 <= number <= 100:
        raise ValueError(f"{name} must be from 0 to 100")
    if number != int(number):
        raise ValueError(f"{name} must be a whole number")
    return int(number)


def format_rgb(rgb: list[int] | tuple[int, int, int]) -> str:
    if len(rgb) != 3 or any(not isinstance(c, int) or not 0 <= c <= 255 for c in rgb):
        raise ValueError("RGB must contain exactly three integers from 0 to 255")
    return "#" + "".join(f"{channel:02X}" for channel in rgb)


def quantize_rgb(rgb: tuple[int, int, int] | list[int]) -> tuple[int, int, int]:
    """Round RGB to the device's reported 3/3/2-bit color grid."""
    if len(rgb) != 3 or any(not isinstance(c, int) or not 0 <= c <= 255 for c in rgb):
        raise ValueError("RGB must contain exactly three integers from 0 to 255")
    levels = (7, 7, 3)
    return tuple(round(channel * level / 255) for channel, level in zip(rgb, levels))  # type: ignore[return-value]
