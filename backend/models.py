from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field

# ─────────────────────── Attendees ───────────────────────
class AttendeeBase(BaseModel):
    name: str = Field(..., description="Full name of attendee", example="Aarav Sharma")
    role: str = Field(..., description="Ticket tier role (General, Early Bird, VIP, Organizer, Sponsor)", example="VIP")
    ticket_type: str = Field(..., description="Specific pass type", example="Platinum")
    fest_coins: int = Field(1000, description="Current Fest Coin cashless balance", example=1000)

class AttendeeCreate(AttendeeBase):
    initial_coins: Optional[int] = Field(None, description="Optional initial Fest Coins override", example=500)

class AttendeeResponse(AttendeeBase):
    id: str = Field(..., alias="_id", description="Unique attendee ID")
    engagement_score: int = Field(0, description="Cumulative engagement points")
    fest_coins: int = Field(1000, description="Current Fest Coin balance")
    qr_code_hash: str = Field(..., description="Unique hexadecimal QR pass identifier")
    status: str = Field("active", description="Status: 'active' or 'terminated'")
    terminated_at: Optional[str] = Field(None, description="ISO timestamp if terminated")
    termination_reason: Optional[str] = Field(None, description="Reason for pass revocation")
    registered_at: str = Field(..., description="ISO registration timestamp")

    class Config:
        populate_by_name = True

class AttendeeRegisterResult(BaseModel):
    message: str
    qr_code_hash: str
    attendee: Dict[str, Any]

class TerminateQrRequest(BaseModel):
    reason: Optional[str] = Field("Administrative revocation", description="Reason for revoking pass")

class ReinstateQrRequest(BaseModel):
    regenerate: Optional[bool] = Field(False, description="Whether to generate a fresh QR token hash")

# ─────────────────────── Scans & Checkpoints ───────────────────────
class ScanRequest(BaseModel):
    qr_code_hash: str = Field(..., description="Attendee QR code hash", example="a9f4c3b2810e746d")
    zone_name: str = Field(..., description="Target festival zone", example="Main Stage")

class ZoneLogResponse(BaseModel):
    id: str = Field(..., alias="_id")
    attendee_id: str
    qr_code_hash: str
    role: str
    zone_name: str
    timestamp: str
    seeded: Optional[bool] = False
    simulated: Optional[bool] = False

    class Config:
        populate_by_name = True

class ScanResponse(BaseModel):
    message: str
    attendee: Dict[str, Any]
    zone_log: Dict[str, Any]

# ─────────────────────── Fest Coin Cashless Transactions ───────────────────────
class TransactionRequest(BaseModel):
    qr_code_hash: Optional[str] = Field(None, description="Attendee QR hash")
    customer_id: Optional[str] = Field(None, description="Customer or Visitor ID from retail dataset")
    attendee_id: Optional[str] = Field(None, description="Internal Attendee ID")
    amount: int = Field(..., gt=0, description="Amount of Fest Coins to deduct")
    stall_name: Optional[str] = Field("Vendor Stall", description="Name of vendor stall")
    zone_name: Optional[str] = Field("Food Court", description="Festival zone where transaction occurred")
    category: Optional[str] = Field(None, description="Transaction retail category")

class TransactionResponse(BaseModel):
    message: str
    transaction: Dict[str, Any]
    attendee: Dict[str, Any]

# ─────────────────────── Live Social Sentiment & Triage ───────────────────────
class SocialPost(BaseModel):
    id: str
    text: str
    sentiment: str = Field("neutral", description="positive, neutral, or negative")
    author: Optional[str] = "Festival Guest"
    timestamp: str

class FrictionAlert(BaseModel):
    active: bool = False
    negative_count_60s: int = 0
    threshold: int = 8
    window_seconds: int = 60
    severity: str = "NORMAL"
    message: str = "Sentiment is nominal across festival grounds."
    triggered_at: Optional[str] = None
    cooldown_until: Optional[str] = None

# ─────────────────────── Rupee-to-Coin Cashless Top-up ───────────────────────
class RechargeRequest(BaseModel):
    attendee_id: Optional[str] = Field(None, description="Attendee ID")
    qr_code_hash: Optional[str] = Field(None, description="Attendee QR hash")
    inr_amount: int = Field(..., gt=0, description="Amount in Indian Rupees to top up")
    payment_method: Optional[str] = Field("UPI", description="Payment method: UPI, Card, NetBanking, Cash Desk")
    operator_mode: Optional[str] = Field("self_service", description="'self_service' or 'staff_desk'")
    operator_name: Optional[str] = Field(None, description="Name of desk cashier or portal")
    notes: Optional[str] = Field(None, description="Optional payment reference or cashier note")

class RechargeResponse(BaseModel):
    message: str
    recharge: Dict[str, Any]
    attendee: Dict[str, Any]

class FinancialSummary(BaseModel):
    total_inr_collected: int
    total_coins_issued: int
    conversion_ratio: str = "1 Rupee = 1 Fest Coin"
    total_transactions: int
    method_counts: Dict[str, int]
    mode_counts: Dict[str, int]

class RechargesListResponse(BaseModel):
    recharges: List[Dict[str, Any]]
    summary: FinancialSummary

# ─────────────────────── Emergency Incidents & Triage ───────────────────────
class IncidentCreate(BaseModel):
    type: str = Field(..., description="Medical, Security, or Logistics", example="Medical")
    zone_name: Optional[str] = Field("Main Stage", description="Festival zone", example="Cultural Pavilion")
    notes: Optional[str] = Field(None, description="Specific incident notes", example="Heat exhaustion near gate 2")
    coordinator_name: Optional[str] = Field("Field Coordinator", description="Reporting coordinator or dispatch unit")

class IncidentStatusUpdate(BaseModel):
    status: str = Field(..., description="Under Processing or Resolved")
    coordinator_name: Optional[str] = Field(None, description="Coordinator handling or resolving the incident")
    resolution_notes: Optional[str] = Field(None, description="Notes on resolution steps taken")

class IncidentResponse(BaseModel):
    message: str
    incident: Dict[str, Any]

# ─────────────────────── Crowd Crushing Prevention & Diversion ───────────────────────
class DiversionCreate(BaseModel):
    zone_name: str = Field(..., description="Congested origin zone", example="Cultural Pavilion")
    target_zone: Optional[str] = Field("Food Court", description="Destination relief zone", example="Food Court")
    message: Optional[str] = Field(None, description="Broadcast push offer message", example="50% discount on artisan snacks at Food Court!")

class DiversionResponse(BaseModel):
    message: str
    diversion: Dict[str, Any]

class ThrottleRequest(BaseModel):
    zone_name: str = Field(..., description="Overloaded zone to divert")
    target_zone: Optional[str] = Field(None, description="Target destination zone")
    count: Optional[int] = Field(150, description="Number of scans to re-route")

# ─────────────────────── NLP & AI Assistant ───────────────────────
class NLPQueryRequest(BaseModel):
    query: str = Field(..., description="Natural language question or command", example="Which zone has the biggest bottleneck right now?")

class MetricItem(BaseModel):
    label: str
    value: str
    alert: Optional[bool] = False

class QuickActionItem(BaseModel):
    label: str
    url: Optional[str] = None
    action: Optional[str] = None

class NLPQueryResponse(BaseModel):
    query: str
    response: str
    answer: str
    category: str
    metrics: List[MetricItem] = []
    quick_actions: List[QuickActionItem] = []
    result_count: int = 0

# ─────────────────────── Admin Stats ───────────────────────
class ZoneStatItem(BaseModel):
    zone_name: str
    total_scans: int
    recent_scans: int
    capacity_warning: bool
    active_diversion: Optional[Dict[str, Any]] = None

class AdminStatsResponse(BaseModel):
    zones: List[ZoneStatItem]
    recent_logs: List[Dict[str, Any]]
    total_attendees: int
    total_engagement: int
    financials: Dict[str, Any]
    active_incidents: List[Dict[str, Any]]
    active_diversions: List[Dict[str, Any]]

# ─────────────────────── Attendee History ───────────────────────
class AttendeeHistoryResponse(BaseModel):
    attendee: Dict[str, Any]
    recharges: List[Dict[str, Any]]
    transactions: List[Dict[str, Any]]
    scans: List[Dict[str, Any]]
    summary: Dict[str, Any]

# ─────────────────────── System Health & Config ───────────────────────
class HealthCheckResponse(BaseModel):
    status: str
    framework: str = "FastAPI (Python 3.11)"
    server: str = "Uvicorn ASGI"
    validation: str = "Pydantic v2"
    database_driver: str = "Motor (async MongoDB Atlas) with Resilient Fallback"
    database_status: str
    database_uri_configured: bool
    active_connections: int
    version: str = "2.0.0"
