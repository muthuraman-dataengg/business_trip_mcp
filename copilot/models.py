"""Typed domain models shared by servers, host and UI."""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

CabinClass = Literal["economy", "premium_economy", "business", "first"]
SeatPreference = Literal["window", "aisle", "any"]
TaskStatus = Literal["open", "in_progress", "done", "cancelled"]


class TravelerPreferences(BaseModel):
    """Everything the old project hard-coded in `travel://preferences` — now per traveler."""

    preferred_airlines: List[str] = Field(default_factory=list)
    avoided_airlines: List[str] = Field(default_factory=list)
    seat: SeatPreference = "any"
    cabin_class: CabinClass = "economy"
    hotel_min_stars: int = Field(3, ge=1, le=5)
    hotel_max_stars: int = Field(4, ge=1, le=5)
    max_stops: int = Field(1, ge=0, le=3)
    dietary: str = ""
    loyalty_programs: List[str] = Field(default_factory=list)
    trip_style: str = ""
    notes: str = ""

    @field_validator("hotel_max_stars")
    @classmethod
    def _max_ge_min(cls, v: int, info):  # noqa: ANN001
        lo = info.data.get("hotel_min_stars", 1)
        return max(v, lo)


class TravelerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: Optional[str] = None
    home_city: str = Field(..., min_length=1)
    home_currency: str = Field("USD", min_length=3, max_length=3)
    department: str = ""
    preferences: TravelerPreferences = Field(default_factory=TravelerPreferences)


class Traveler(TravelerCreate):
    id: str
    created_at: datetime
    updated_at: datetime


class TripTask(BaseModel):
    id: str
    traveler_id: Optional[str]
    title: str
    notes: str = ""
    due_date: Optional[date] = None
    status: TaskStatus = "open"
    created_at: datetime


class Booking(BaseModel):
    id: str
    traveler_id: Optional[str]
    city: str
    hotel_name: str
    check_in: date
    nights: int
    stars: Optional[int] = None
    nightly_rate: Optional[float] = None
    currency: Optional[str] = None
    status: Literal["confirmed", "cancelled"] = "confirmed"
    approved_by: str = ""
    created_at: datetime


class BookingRequest(BaseModel):
    """A hotel the agent proposed. Nothing is booked until a human approves it."""

    id: str
    traveler_id: Optional[str]
    city: str
    hotel_name: str
    check_in: date
    nights: int
    stars: Optional[int] = None
    nightly_rate: Optional[float] = None
    currency: Optional[str] = None
    reason: str = ""
    status: Literal["pending", "approved", "rejected"] = "pending"
    decided_by: str = ""
    booking_id: Optional[str] = None
    created_at: datetime
    decided_at: Optional[datetime] = None
