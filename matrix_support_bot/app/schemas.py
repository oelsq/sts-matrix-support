"""Pydantic schemas for request and response models.
"""

from datetime import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict

class MessageBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    content: str = Field(..., min_length=1, max_length=24000)

class MessageCreate(MessageBase):
    content: str = Field(..., min_length=1, max_length=12000)

class MessageOut(MessageBase):
    id: int
    ticket_id: int
    role: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class TicketBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=20000)

class TicketCreate(TicketBase):
    pass

class TicketOut(TicketBase):
    id: int
    status: Literal["open", "pending", "closed"]
    created_at: datetime
    updated_at: datetime
    messages: List[MessageOut] = []

    model_config = ConfigDict(from_attributes=True)

class StatusUpdate(BaseModel):
    status: Literal["open", "pending", "closed"]
