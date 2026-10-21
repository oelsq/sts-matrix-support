"""FastAPI entry point for the Matrix Support Bot MVP.

The application provides a REST API for managing support tickets and
communicating with a (stubbed) Matrix bot as well as a local LLM.
"""

import os
from fastapi import FastAPI, HTTPException, Depends, Query
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session

from . import models, schemas, crud, database

@asynccontextmanager
async def lifespan(app):
    models.Base.metadata.create_all(bind=database.engine)
    yield

app = FastAPI(title="Matrix Support Bot", lifespan=lifespan)

# Health check endpoint
@app.get("/health")
def health():
    return {"status": "ok"}

# Dependency to get DB session
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------- Ticket endpoints ----------
@app.post("/tickets/", response_model=schemas.TicketOut)
def create_ticket(ticket_in: schemas.TicketCreate, db: Session = Depends(get_db)):
    return crud.create_ticket(db=db, ticket_in=ticket_in)

@app.get("/tickets/{ticket_id}", response_model=schemas.TicketOut)
def read_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket = crud.get_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket

@app.get("/tickets/", response_model=list[schemas.TicketOut])
def list_tickets(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    return crud.get_tickets(db, skip=skip, limit=limit)

# ---------- Message endpoint ----------
@app.post("/tickets/{ticket_id}/messages/", response_model=schemas.MessageOut)
def add_message(
    ticket_id: int,
    message_in: schemas.MessageCreate,
    db: Session = Depends(get_db),
    llm_endpoint: str = os.getenv("LLM_ENDPOINT", ""),
):
    ticket = crud.get_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    # Store user message
    user_msg = crud.create_message(db, ticket_id=ticket_id, msg_in=message_in, role="user")
    # Generate bot reply via stub LLM (echo back for MVP)
    bot_reply_text = f"Echo: {message_in.content}" if not llm_endpoint else _call_llm(llm_endpoint, message_in.content)
    bot_msg = crud.create_message(db, ticket_id=ticket_id, msg_in=schemas.MessageBase(content=bot_reply_text), role="assistant")
    return bot_msg

def _call_llm(endpoint: str, prompt: str) -> str:
    """Call a local LLM endpoint. For the MVP we simply return the prompt.
    This placeholder can be replaced with a real HTTP request.
    """
    # Example implementation (commented out to avoid network calls in tests)
    # import httpx
    # resp = httpx.post(endpoint, json={"prompt": prompt}, timeout=10.0)
    # resp.raise_for_status()
    # return resp.json().get("response", "")
    return f"Echo: {prompt}"

# ---------- Status endpoint ----------
@app.put("/tickets/{ticket_id}/status/", response_model=schemas.TicketOut)
def update_status(ticket_id: int, status_in: schemas.StatusUpdate, db: Session = Depends(get_db)):
    ticket = crud.get_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket = crud.update_ticket_status(db, ticket, new_status=status_in.status)
    return ticket
