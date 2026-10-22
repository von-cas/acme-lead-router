import hashlib
import os
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from buyers import BUYERS
from qualify import qualify

app = FastAPI(title="Acme lead router")


def require_service_token(x_service_token: str = Header("")) -> None:
    if not x_service_token or x_service_token != os.environ.get("SERVICE_TOKEN"):
        raise HTTPException(status_code=401, detail="bad service token")


class LeadIn(BaseModel):
    contact_id: str
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    postal_code: str = ""
    monthly_bill: float = 0
    homeowner: bool = False
    roof_shade: str = ""
    timeline: str = ""
    consent: bool = False


def to_e164(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return "+1" + digits
    return "+" + digits if digits else ""


def normalize(lead: LeadIn) -> dict:
    data = lead.model_dump()
    data["email"] = lead.email.strip().lower()
    data["phone_e164"] = to_e164(lead.phone)
    data["lead_key"] = hashlib.sha256(f"{data['email']}|{data['phone_e164']}".encode()).hexdigest()[:16]
    data["day"] = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    return data


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/qualify", dependencies=[Depends(require_service_token)])
def qualify_lead(lead: LeadIn) -> dict:
    normalized = normalize(lead)
    return {"lead": normalized, "qualification": qualify(normalized).model_dump()}


class MapRequest(BaseModel):
    lead: dict
    qualification: dict


@app.post("/map/{buyer_id}", dependencies=[Depends(require_service_token)])
def map_lead(buyer_id: str, body: MapRequest) -> dict:
    mapper = BUYERS.get(buyer_id)
    if mapper is None:
        raise HTTPException(status_code=404, detail=f"unknown buyer {buyer_id}")
    try:
        return mapper(body.lead, body.qualification)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/mock-buyer/{buyer_id}/leads", status_code=201)
def mock_buyer(
    buyer_id: str,
    payload: dict,
    authorization: str = Header(""),
    idempotency_key: str = Header(""),
    x_demo_outage: Optional[str] = Header("0"),
) -> dict:
    buyer_key = os.environ.get("BUYER_KEY")
    if not buyer_key or authorization != f"Bearer {buyer_key}":
        raise HTTPException(status_code=401, detail="bad buyer key")
    if x_demo_outage == "1":
        raise HTTPException(status_code=503, detail="installer CRM is down (demo outage)")
    return {"buyer_lead_id": f"IA-{idempotency_key[:8]}", "status": "accepted"}
