"""SQL-owned prospecting runs; every snapshot and audit event belongs to a tenant."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, DateTime, JSON
from app.db.session import Base
from app.db.tenancy import TenantScoped


class ProspectingRun(Base, TenantScoped):
    __tablename__ = 'prospecting_runs'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    market = Column(String(300), nullable=False)
    product = Column(Text, nullable=False)
    limit = Column(Integer, nullable=False, default=5)
    status = Column(String(24), nullable=False, default='pending', index=True)
    candidates = Column(JSON, nullable=False, default=list)
    cursor = Column(Integer, nullable=False, default=0)
    results = Column(JSON, nullable=False, default=list)
    events = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
