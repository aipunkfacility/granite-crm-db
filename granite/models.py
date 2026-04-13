# models.py
from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone
from enum import Enum

__all__ = [
    "Source",
    "RawCompany",
]


class Source(str, Enum):
    WEB_SEARCH = "web_search"
    JSPRAV = "jsprav"
    JSPRAV_PW = "jsprav_playwright"
    DGIS = "2gis"
    YELL = "yell"

    GOOGLE_MAPS = "google_maps"
    AVITO = "avito"


class RawCompany(BaseModel):
    """Сырые данные от любого скрепера. Единый формат для всех источников."""
    source: Source
    source_url: str = ""
    name: str
    phones: list[str] = Field(default_factory=list)  # E.164: 7XXXXXXXXXX
    address_raw: str = ""
    website: str | None = None
    emails: list[str] = Field(default_factory=list)
    geo: list[float] | None = None  # [lat, lon]

    @field_validator('geo', mode='before')
    @classmethod
    def _parse_geo(cls, v):
        """Convert comma-separated string 'lat,lon' from ORM to [lat, lon] list."""
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            parts = v.split(',')
            if len(parts) == 2:
                try:
                    return [float(parts[0].strip()), float(parts[1].strip())]
                except (ValueError, TypeError):
                    return None
        return None
    messengers: dict[str, str] = Field(default_factory=dict)  # {"telegram": "...", "vk": "...", "whatsapp": "..."}
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    city: str = ""
