from dotenv import load_dotenv
import os
import phonenumbers
from fastapi import Header, HTTPException, Depends
from sqlmodel import Session, create_engine, SQLModel

load_dotenv()

API_KEY = os.getenv("API_KEY", "secret123")
OUTBOUND_WHITELIST = set([n.strip() for n in os.getenv("OUTBOUND_WHITELIST", "").split(",") if n.strip()])

engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///./screener.db"))

def get_session():
    with Session(engine) as session:
        yield session

def require_api_key(x_api_key: str = Header(None)):
    print(x_api_key)
    print(API_KEY)
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API Key")

def require_whitelisted(phone: str):
    print(OUTBOUND_WHITELIST)
    if phone not in OUTBOUND_WHITELIST:
        raise HTTPException(403, "Destination not whitelisted")

def to_e164(raw: str) -> str:
    try:
        cleaned = "".join(c for c in raw if c.isdigit() or c == "+")
        if not cleaned.startswith("+"):
            cleaned = "+91" + cleaned
        num = phonenumbers.parse(cleaned, None)
        print(num)
        if not phonenumbers.is_valid_number(num):
            raise ValueError("invalid")
        return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except Exception as e:
        print(f"[Phone Parse Error] raw={raw} err={e}")
        raise HTTPException(400, "Invalid phone number")
def reset_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
