# -*- coding: utf-8 -*-
"""Request models for local feedback, optional accounts and reviewed templates."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AccountRequest(BaseModel):
    username: str = Field(min_length=3, max_length=48, pattern=r"^[\w.-]+$")
    password: str = Field(min_length=10, max_length=256)


class MemoryConsentRequest(BaseModel):
    enabled: bool


class PreferenceRequest(BaseModel):
    kind: Literal["like", "dislike"]
    statement: str = Field(min_length=1, max_length=200)


class InventoryRequest(BaseModel):
    item: str = Field(min_length=1, max_length=100)
    quantity: str = Field(default="", max_length=80)
    condition: str = Field(default="", max_length=200)


class QuickFeedbackRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["checklist_usefulness", "purchase_outcome"]
    value: Literal["helpful", "needs_work", "bought", "not_found", "chose_not_to_buy", "not_yet"]
    note: str = Field(default="", max_length=500)


class CandidateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    quantity: str | None = Field(default=None, max_length=100)
    condition: str | None = Field(default=None, max_length=100)
    purchasable: bool | None = None


class CandidateSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    required: list[CandidateItem] = Field(default_factory=list, max_length=32)
    optional: list[CandidateItem] = Field(default_factory=list, max_length=32)


class TemplateCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anonymized: Literal[True]
    sections: list[CandidateSection] = Field(min_length=2, max_length=6)


class TemplateReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=500)
    scene_id: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=80)
    core_use: str = Field(default="", max_length=120)
    semantic_description: str = Field(default="", max_length=300)
    match_terms: list[str] = Field(default_factory=list, max_length=20)
    incompatible_terms: list[str] = Field(default_factory=list, max_length=20)


class TemplateRollbackRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=500)
