"""Pydantic contracts for both graphs.

Validation failures counted here ARE the Schema Validation Pass Rate metric, so
these models are deliberately strict: an alert with no government citation, or a
verdict with no quote, must fail rather than degrade.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

GOV = re.compile(r"^https://[\w.-]*\.gov\.sg/", re.I)


class Alert(BaseModel):
    """What the advisor produces for a founder.

    `resource` is required and must be a primary government URL, so an uncited
    alert cannot validate. That is Answer Fidelity enforced structurally.
    """

    rule_path: str
    headline: str
    who: list[str]
    deadline: Optional[dt.date] = None
    dollar_impact: Optional[float] = None
    next_step: str
    resource: str

    @field_validator("resource")
    @classmethod
    def _gov_sg_only(cls, v: str) -> str:
        if not GOV.match(v):
            raise ValueError(f"resource must be a primary .gov.sg URL, got {v!r}")
        return v

    @field_validator("who")
    @classmethod
    def _somebody(cls, v: list) -> list:
        if not v:
            raise ValueError("an alert that hits nobody is not an alert")
        return v


class Verdict(BaseModel):
    """What the curator's model returns when a source page has changed.

    The model describes; it never acts. Every field here is a claim that
    ante.curator.verify re-checks against the fetched page before anything is
    written to rules/.
    """

    verdict: Literal["unchanged", "figure_changed", "date_changed",
                     "superseded", "page_moved", "unclear"]
    new_value: Optional[float] = None
    new_effective: Optional[dt.date] = None
    quote: str = ""
    confidence: Literal["high", "medium", "low"]
    reasoning: str
