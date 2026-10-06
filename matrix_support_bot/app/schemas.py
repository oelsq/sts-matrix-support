"""Pydantic schemas for request and response models.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

class MessageBase(BaseModel):
    content: str = Field(..., min_length=1)

class MessageCreate(MessageBase):
    pass

class MessageOut(MessageBase):
    id: int
    ticket_id: int
    role: str
    created_at: datetime

    class Config:
        from_attributes = True

class TicketBase(BaseModel):
    title: str = Field(..., min_length=1)
    description: Optional[str] = None

class TicketCreate(TicketBase):
    pass

class TicketOut(TicketBase):
    id: int
    status: str
    created_at: datetime
    updated_at: datetime
    messages: List[MessageOut] = []

    class Config:
        from_attributes = True

class StatusUpdate(BaseModel):
    status: str
