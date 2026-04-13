from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
import uuid
from datetime import datetime, timezone
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Twilio client - will be None if credentials not set
twilio_client = None
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')

if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Predefined caller IDs that user can choose from
ALLOWED_CALLER_IDS = [
    "+33624676329",
    "+33649550407",
    "+33664998207"
]

# Create the main app
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Models
class CallRequest(BaseModel):
    to_number: str
    caller_id: str

class SMSRequest(BaseModel):
    to_number: str
    message: str
    caller_id: str

class CallRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    to_number: str
    caller_id: str
    status: str
    duration: Optional[int] = None
    call_sid: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SMSRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    to_number: str
    caller_id: str
    message: str
    status: str
    message_sid: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class TwilioConfig(BaseModel):
    account_sid: str
    auth_token: str
    phone_number: str

class ContactCreate(BaseModel):
    name: str
    phone: str
    company: Optional[str] = None

class ContactUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None

class Contact(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    phone: str
    company: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Routes
@api_router.get("/")
async def root():
    return {"message": "VOIP API Ready"}

@api_router.get("/health")
async def health():
    return {
        "status": "ok",
        "twilio_configured": twilio_client is not None,
        "allowed_caller_ids": ALLOWED_CALLER_IDS
    }

@api_router.get("/caller-ids")
async def get_caller_ids():
    """Get list of allowed caller IDs"""
    return {"caller_ids": ALLOWED_CALLER_IDS}

@api_router.post("/configure-twilio")
async def configure_twilio(config: TwilioConfig):
    """Configure Twilio credentials"""
    global twilio_client, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER

    try:
        test_client = Client(config.account_sid, config.auth_token)
        test_client.api.accounts(config.account_sid).fetch()

        TWILIO_ACCOUNT_SID = config.account_sid
        TWILIO_AUTH_TOKEN = config.auth_token
        TWILIO_PHONE_NUMBER = config.phone_number
        twilio_client = test_client

        return {"status": "configured", "message": "Twilio credentials validated and saved"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Twilio credentials: {str(e)}")

@api_router.post("/call")
async def make_call(request: CallRequest):
    """Initiate an outbound call"""
    if not twilio_client:
        raise HTTPException(status_code=400, detail="Twilio not configured. Please configure your Twilio credentials first.")

    if request.caller_id not in ALLOWED_CALLER_IDS:
        raise HTTPException(status_code=400, detail=f"Caller ID not allowed. Use one of: {ALLOWED_CALLER_IDS}")

    try:
        call = twilio_client.calls.create(
            to=request.to_number,
            from_=TWILIO_PHONE_NUMBER,
            twiml='<Response><Say language="fr-FR">Bonjour, ceci est un appel de test.</Say></Response>'
        )

        record = CallRecord(
            to_number=request.to_number,
            caller_id=request.caller_id,
            status=call.status,
            call_sid=call.sid
        )
        doc = record.model_dump()
        doc['timestamp'] = doc['timestamp'].isoformat()
        await db.call_history.insert_one(doc)

        return {
            "status": "initiated",
            "call_sid": call.sid,
            "to": request.to_number,
            "caller_id": request.caller_id
        }
    except Exception as e:
        logger.error(f"Call failed: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@api_router.post("/sms")
async def send_sms(request: SMSRequest):
    """Send an SMS message"""
    if not twilio_client:
        raise HTTPException(status_code=400, detail="Twilio not configured. Please configure your Twilio credentials first.")

    if request.caller_id not in ALLOWED_CALLER_IDS:
        raise HTTPException(status_code=400, detail=f"Caller ID not allowed. Use one of: {ALLOWED_CALLER_IDS}")

    try:
        message = twilio_client.messages.create(
            to=request.to_number,
            from_=TWILIO_PHONE_NUMBER,
            body=request.message
        )

        record = SMSRecord(
            to_number=request.to_number,
            caller_id=request.caller_id,
            message=request.message,
            status=message.status,
            message_sid=message.sid
        )
        doc = record.model_dump()
        doc['timestamp'] = doc['timestamp'].isoformat()
        await db.sms_history.insert_one(doc)

        return {
            "status": "sent",
            "message_sid": message.sid,
            "to": request.to_number,
            "caller_id": request.caller_id
        }
    except Exception as e:
        logger.error(f"SMS failed: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@api_router.get("/call-history")
async def get_call_history():
    """Get call history"""
    calls = await db.call_history.find({}, {"_id": 0}).sort("timestamp", -1).to_list(100)
    for call in calls:
        if isinstance(call.get('timestamp'), str):
            call['timestamp'] = datetime.fromisoformat(call['timestamp'])
    return {"calls": calls}

@api_router.get("/sms-history")
async def get_sms_history():
    """Get SMS history"""
    messages = await db.sms_history.find({}, {"_id": 0}).sort("timestamp", -1).to_list(100)
    for msg in messages:
        if isinstance(msg.get('timestamp'), str):
            msg['timestamp'] = datetime.fromisoformat(msg['timestamp'])
    return {"messages": messages}

@api_router.delete("/call-history/{call_id}")
async def delete_call(call_id: str):
    """Delete a call record"""
    result = await db.call_history.delete_one({"id": call_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Call not found")
    return {"status": "deleted"}

@api_router.delete("/sms-history/{sms_id}")
async def delete_sms(sms_id: str):
    """Delete an SMS record"""
    result = await db.sms_history.delete_one({"id": sms_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="SMS not found")
    return {"status": "deleted"}

# ─── CONTACTS CRUD ────────────────────────────────────────────────────────────

@api_router.get("/contacts")
async def get_contacts():
    """Get all contacts, sorted by name"""
    contacts = await db.contacts.find({}, {"_id": 0}).sort("name", 1).to_list(500)
    for c in contacts:
        if isinstance(c.get('created_at'), str):
            c['created_at'] = datetime.fromisoformat(c['created_at'])
    return {"contacts": contacts}

@api_router.post("/contacts")
async def create_contact(contact: ContactCreate):
    """Create a new contact"""
    # Check for duplicate phone
    existing = await db.contacts.find_one({"phone": contact.phone})
    if existing:
        raise HTTPException(status_code=400, detail="Un contact avec ce numéro existe déjà")

    record = Contact(
        name=contact.name.strip(),
        phone=contact.phone.strip(),
        company=contact.company.strip() if contact.company else None
    )
    doc = record.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.contacts.insert_one(doc)

    return {"status": "created", "contact": record.model_dump()}

@api_router.put("/contacts/{contact_id}")
async def update_contact(contact_id: str, update: ContactUpdate):
    """Update an existing contact"""
    existing = await db.contacts.find_one({"id": contact_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Contact introuvable")

    update_data = {}
    if update.name is not None:
        update_data["name"] = update.name.strip()
    if update.phone is not None:
        # Check no other contact has this phone
        other = await db.contacts.find_one({"phone": update.phone, "id": {"$ne": contact_id}})
        if other:
            raise HTTPException(status_code=400, detail="Ce numéro est déjà utilisé par un autre contact")
        update_data["phone"] = update.phone.strip()
    if update.company is not None:
        update_data["company"] = update.company.strip() if update.company else None

    if update_data:
        await db.contacts.update_one({"id": contact_id}, {"$set": update_data})

    updated = await db.contacts.find_one({"id": contact_id}, {"_id": 0})
    return {"status": "updated", "contact": updated}

@api_router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: str):
    """Delete a contact"""
    result = await db.contacts.delete_one({"id": contact_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Contact introuvable")
    return {"status": "deleted"}

# ──────────────────────────────────────────────────────────────────────────────

# Include the router
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
