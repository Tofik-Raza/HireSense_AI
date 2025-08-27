import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field

class Candidate(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    phone_e164: str
    email: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Interview(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    candidate_id: str
    status: str = "created"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    recommendation: Optional[str] = None
    overall_score: Optional[float] = None

class Question(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    interview_id: str
    idx: int
    text: str

class Answer(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    interview_id: str
    question_id: str
    pending: bool = True
    idx: int
    recording_url: Optional[str] = None
    transcript: Optional[str] = None
    score: Optional[float] = None
