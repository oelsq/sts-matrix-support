"""CRUD helper functions for the FastAPI routes.

All functions receive a SQLAlchemy `Session` instance.
"""

from sqlalchemy.orm import Session
from . import models, schemas


def create_ticket(db: Session, ticket_in: schemas.TicketCreate) -> models.Ticket:
    db_ticket = models.Ticket(title=ticket_in.title, description=ticket_in.description)
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    return db_ticket


def get_ticket(db: Session, ticket_id: int) -> models.Ticket | None:
    return db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()


def get_tickets(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Ticket).offset(skip).limit(limit).all()


def update_ticket_status(db: Session, ticket: models.Ticket, new_status: str) -> models.Ticket:
    ticket.status = new_status
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def create_message(db: Session, ticket_id: int, msg_in: schemas.MessageBase, role: str) -> models.Message:
    db_msg = models.Message(ticket_id=ticket_id, content=msg_in.content, role=role)
    db.add(db_msg)
    db.commit()
    db.refresh(db_msg)
    return db_msg
