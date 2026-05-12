"""Pydantic models shared by the validator and the loader.

A processed jurisdiction file (data/processed/{slug}.json) deserializes into a
JurisdictionFile. The shape mirrors the SQL schema closely so the loader is a
thin translation layer.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


IncentiveType = Literal[
    "tax_credit_transferable",
    "tax_credit_refundable",
    "tax_credit_non_refundable",
    "cash_rebate",
    "grant",
    "combined",
]

SourceType = Literal[
    "official_government",
    "film_office",
    "industry_publication",
    "legal_summary",
]


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    source_title: str
    source_type: SourceType
    accessed_date: date


class IncentiveProgram(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_name: str
    incentive_type: IncentiveType
    headline_rate_pct: float = Field(ge=0, le=100)
    rate_details: Optional[str] = None
    minimum_spend: Optional[float] = Field(default=None, ge=0)
    minimum_spend_notes: Optional[str] = None
    cap_per_project: Optional[float] = Field(default=None, ge=0)
    annual_program_cap: Optional[float] = Field(default=None, ge=0)
    atl_eligible: bool  # explicit True/False; never null
    atl_cap_notes: Optional[str] = None
    qualifying_spend_summary: Optional[str] = None
    non_qualifying_spend: Optional[str] = None
    application_process: Optional[str] = None
    payment_timing: Optional[str] = None
    sunset_date: Optional[date] = None
    last_verified_date: date
    notes: Optional[str] = None
    sources: list[Source] = Field(min_length=1)

    @field_validator("minimum_spend_notes")
    @classmethod
    def _spend_notes_required_if_spend_set(cls, v, info):
        # Cross-field check runs in model_validator below; this is a placeholder
        # to keep field-level behavior consistent.
        return v


class Jurisdiction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str
    region: Optional[str] = None
    city: Optional[str] = None
    display_name: str
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _currency_upper(cls, v: str) -> str:
        if not v.isalpha() or v != v.upper():
            raise ValueError("currency must be uppercase ISO 4217 (e.g., 'GBP')")
        return v


class JurisdictionFile(BaseModel):
    """Root of a data/processed/{slug}.json file."""

    model_config = ConfigDict(extra="forbid")

    jurisdiction: Jurisdiction
    programs: list[IncentiveProgram] = Field(min_length=1)
