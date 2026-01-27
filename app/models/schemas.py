"""
Pydantic Models for Request/Response
"""
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


# ============================================
# Enums
# ============================================

class VoiceStrategy(str, Enum):
    AUTO = "auto"
    # NATIVE = "native"  # Voice cloning has been removed
    EXTERNAL = "external"


class VoiceType(str, Enum):
    CUSTOM = "custom"
    REFERENCE = "reference"


class VoiceStatus(str, Enum):
    TRAINING = "training"
    ACTIVE = "active"
    FAILED = "failed"


class CallDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallStatus(str, Enum):
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class CampaignScheduleType(str, Enum):
    IMMEDIATE = "immediate"
    SCHEDULED = "scheduled"


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


# ============================================
# Common Models
# ============================================

class ResponseMeta(BaseModel):
    request_id: str
    ts: datetime


class ErrorResponse(BaseModel):
    error: Dict[str, Any]


# ============================================
# Auth Models
# ============================================

class UserResponse(BaseModel):
    id: str
    auth0_sub: Optional[str] = ""  # Legacy field - empty string for Clerk-only users
    client_id: str
    email: str
    role: str
    created_at: datetime


class ClientResponse(BaseModel):
    id: str
    name: str
    email: str
    subscription_status: str
    credits_balance: int
    credits_ceiling: int
    created_at: datetime


class ApiKeyCreate(BaseModel):
    key_name: str = Field(..., description="User-friendly name")
    generate: bool = Field(True, description="Always generate a random API key")


class ApiKeyResponse(BaseModel):
    id: str
    client_id: str
    service: str
    key_name: str
    is_active: bool
    created_at: datetime


class TTSProviderUpdate(BaseModel):
    provider: str = Field(..., description="Provider: google, azure, openai, elevenlabs")
    api_key: str = Field(..., description="Provider API key")
    voice_id: Optional[str] = None
    settings: Optional[Dict[str, Any]] = Field(default={})


# ============================================
# Telephony Models
# ============================================

class TelephonyProviderType(str, Enum):
    TELNYX = "telnyx"
    TWILIO = "twilio"
    PLIVO = "plivo"
    CUSTOM_SIP = "custom_sip"


class PhoneNumberStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"


class NumberSearchRequest(BaseModel):
    country_code: str = Field(default="US", description="ISO country code")
    locality: Optional[str] = Field(None, description="City/region to search in")
    api_key: Optional[str] = Field(None, description="Optional Telnyx API key (uses master if not provided)")


class NumberPurchaseRequest(BaseModel):
    phone_number: str = Field(..., pattern=r"^\+[1-9]\d{1,14}$", description="Phone number in E.164 format")
    api_key: Optional[str] = Field(None, description="Optional Telnyx API key (uses master if not provided)")


class NumberImportRequest(BaseModel):
    phone_number: str = Field(..., pattern=r"^\+[1-9]\d{1,14}$", description="Phone number in E.164 format")
    provider_type: TelephonyProviderType = Field(..., description="Provider type")
    friendly_name: Optional[str] = Field(None, description="Friendly name for credentials")
    
    # Provider-specific credentials
    api_key: Optional[str] = Field(None, description="API key (for Telnyx/Twilio)")
    account_sid: Optional[str] = Field(None, description="Account SID (for Twilio/Telnyx)")
    auth_token: Optional[str] = Field(None, description="Auth token (for Twilio/Telnyx)")
    
    # SIP credentials
    sip_username: Optional[str] = Field(None, description="SIP username (for custom_sip)")
    sip_password: Optional[str] = Field(None, description="SIP password (for custom_sip)")
    sip_server: Optional[str] = Field(None, description="SIP server (for custom_sip)")


class NumberAssignmentRequest(BaseModel):
    number_id: str = Field(..., description="Phone number UUID")
    agent_id: str = Field(..., description="Agent UUID")
    assignment_type: str = Field(..., description="'inbound' or 'outbound'")


class TelephonyCredentialResponse(BaseModel):
    id: str
    organization_id: str
    provider_type: str
    friendly_name: Optional[str]
    created_at: datetime
    updated_at: datetime


class PhoneNumberResponse(BaseModel):
    id: str
    organization_id: str
    agent_id: Optional[str]
    phone_number: str
    provider_id: Optional[str]
    status: str
    is_trudy_managed: bool
    telephony_credential_id: Optional[str]
    created_at: datetime
    updated_at: datetime


class AvailableNumberResponse(BaseModel):
    phone_number: str
    region_information: Optional[Dict[str, Any]] = None
    features: Optional[List[str]] = None
    cost_information: Optional[Dict[str, Any]] = None


# ============================================
# Voice Models
# ============================================

class VoiceSample(BaseModel):
    text: str
    storage_key: str
    duration_seconds: float = Field(..., ge=3.0, le=10.0)


class VoiceSource(BaseModel):
    type: str
    samples: Optional[List[VoiceSample]] = None
    provider_voice_id: Optional[str] = None


class VoiceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    strategy: VoiceStrategy
    source: VoiceSource
    provider_overrides: Optional[Dict[str, Any]] = None
    
    @validator("source")
    def validate_source(cls, v, values):
        # Voice cloning (native strategy) has been removed
        if values.get("strategy") == VoiceStrategy.NATIVE:
            raise ValueError("Voice cloning (native strategy) has been removed. Please use voice import (external strategy) instead.")
        # Original validation removed - only external strategy is supported now
        if False:
                raise ValueError("Total sample duration must be at least 15 seconds")
        return v


class VoiceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class VoiceResponse(BaseModel):
    id: str
    client_id: str
    name: str
    provider: str
    type: str
    language: str
    status: str
    training_info: Optional[Dict[str, Any]] = None
    provider_voice_id: Optional[str] = None
    ultravox_voice_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PresignFileRequest(BaseModel):
    filename: str
    content_type: str
    file_size: int


class PresignResponse(BaseModel):
    doc_id: str
    storage_key: str
    url: str
    headers: Dict[str, str]


class VoicePresignRequest(BaseModel):
    files: List[PresignFileRequest] = Field(..., min_items=1, max_items=10)


# ============================================
# Inactivity Message Models (used by other features)
# ============================================

class EndBehavior(str, Enum):
    UNSPECIFIED = "END_BEHAVIOR_UNSPECIFIED"
    HANG_UP_SOFT = "END_BEHAVIOR_HANG_UP_SOFT"
    HANG_UP_STRICT = "END_BEHAVIOR_HANG_UP_STRICT"


class InactivityMessage(BaseModel):
    duration: str = Field(..., description="Duration in string format (e.g., '30s')")
    message: str = Field(..., description="Message to play when inactive")
    endBehavior: Optional[EndBehavior] = Field(EndBehavior.UNSPECIFIED, description="Behavior when message finishes")


# ============================================
# Call Models
# ============================================

class CallSettings(BaseModel):
    recording_enabled: bool = True
    transcription_enabled: bool = True
    greeting: Optional[str] = None


class CallCreate(BaseModel):
    agent_id: Optional[str] = None
    phone_number: str = Field(..., pattern=r"^\+[1-9]\d{1,14}$")
    direction: CallDirection
    call_settings: Optional[CallSettings] = None
    context: Optional[Dict[str, Any]] = None


class CallUpdate(BaseModel):
    context: Optional[Dict[str, Any]] = None
    call_settings: Optional[CallSettings] = None


class CallResponse(BaseModel):
    id: str
    client_id: str
    agent_id: Optional[str] = None
    ultravox_call_id: Optional[str] = None
    phone_number: str
    direction: str
    status: str
    context: Optional[Dict[str, Any]] = None
    call_settings: Optional[Dict[str, Any]] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    cost_usd: Optional[float] = None
    recording_url: Optional[str] = None
    transcript: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class TranscriptResponse(BaseModel):
    call_id: str
    transcript: List[Dict[str, Any]]
    summary: Optional[str] = None


class RecordingResponse(BaseModel):
    call_id: str
    recording_url: str
    format: str
    duration_seconds: Optional[int] = None


class BulkDeleteRequest(BaseModel):
    ids: List[str] = Field(..., min_items=1, description="List of IDs to delete")


class BulkDeleteResponse(BaseModel):
    deleted_count: int
    failed_count: int
    deleted_ids: List[str]
    failed_ids: List[str]


# ============================================
# Campaign Models
# ============================================

class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    agent_id: Optional[str] = None
    schedule_type: CampaignScheduleType
    scheduled_at: Optional[datetime] = None
    timezone: str = Field(default="UTC")
    max_concurrent_calls: int = Field(default=10, ge=1, le=100)
    
    @validator("scheduled_at")
    def validate_scheduled_at(cls, v, values):
        if values.get("schedule_type") == CampaignScheduleType.SCHEDULED and not v:
            raise ValueError("scheduled_at is required for scheduled campaigns")
        return v


class CampaignContact(BaseModel):
    phone_number: str = Field(..., pattern=r"^\+[1-9]\d{1,14}$")
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    custom_fields: Optional[Dict[str, Any]] = None


class CampaignContactsUpload(BaseModel):
    storage_key: Optional[str] = None
    contacts: Optional[List[CampaignContact]] = None
    
    @validator("storage_key", "contacts")
    def validate_upload_source(cls, v, values):
        if not values.get("storage_key") and not values.get("contacts"):
            raise ValueError("Either storage_key or contacts must be provided")
        return v


class CampaignUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    agent_id: Optional[str] = None
    schedule_type: Optional[CampaignScheduleType] = None
    scheduled_at: Optional[datetime] = None
    timezone: Optional[str] = None
    max_concurrent_calls: Optional[int] = Field(None, ge=1, le=100)


class CampaignResponse(BaseModel):
    id: str
    client_id: str
    agent_id: Optional[str] = None
    name: str
    schedule_type: str
    scheduled_at: Optional[datetime] = None
    timezone: str
    max_concurrent_calls: int
    status: str
    ultravox_batch_ids: Optional[List[str]] = None
    stats: Dict[str, int]
    created_at: datetime
    updated_at: Optional[datetime] = None


# ============================================
# Webhook Models
# ============================================

class WebhookEndpointCreate(BaseModel):
    url: str = Field(..., pattern=r"^https://")
    event_types: List[str] = Field(..., min_items=1)
    secret: Optional[str] = None
    enabled: bool = True
    retry_config: Optional[Dict[str, Any]] = None


class WebhookEndpointUpdate(BaseModel):
    url: Optional[str] = Field(None, pattern=r"^https://")
    event_types: Optional[List[str]] = Field(None, min_items=1)
    enabled: Optional[bool] = None
    retry_config: Optional[Dict[str, Any]] = None


class WebhookEndpointResponse(BaseModel):
    id: str
    client_id: str
    url: str
    event_types: List[str]
    secret: Optional[str] = None  # Only returned on creation
    enabled: bool
    retry_config: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


# ============================================
# Tool Models
# ============================================

class ToolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    category: Optional[str] = None
    endpoint: str = Field(..., pattern=r"^https://")
    method: str = Field(..., pattern=r"^(GET|POST|PUT|DELETE)$")
    authentication: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None
    response_schema: Optional[Dict[str, Any]] = None


class ToolUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    category: Optional[str] = None
    endpoint: Optional[str] = Field(None, pattern=r"^https://")
    method: Optional[str] = Field(None, pattern=r"^(GET|POST|PUT|DELETE)$")
    authentication: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None
    response_schema: Optional[Dict[str, Any]] = None


class ToolResponse(BaseModel):
    id: str
    client_id: str
    ultravox_tool_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    endpoint: str
    method: str
    authentication: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None
    response_schema: Optional[Dict[str, Any]] = None
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None


class ToolTestRequest(BaseModel):
    url: str
    method: str
    headers: Optional[Dict[str, str]] = None
    body: Optional[Dict[str, Any]] = None
    test_parameters: Optional[Dict[str, Any]] = None


class ToolTestResponse(BaseModel):
    success: bool
    status_code: Optional[int] = None
    response_body: Optional[Any] = None
    response_body_snippet: Optional[str] = None
    response_time_ms: Optional[int] = None
    error_message: Optional[str] = None


# ============================================
# Subscription Tier Models
# ============================================

class SubscriptionTierCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    display_name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    price_usd: float = Field(..., gt=0)
    price_cents: int = Field(..., gt=0)
    minutes_allowance: int = Field(..., ge=0)
    initial_credits: int = Field(default=0, ge=0)
    stripe_price_id: Optional[str] = None
    stripe_product_id: Optional[str] = None
    is_active: bool = Field(default=True)
    display_order: int = Field(default=0)
    features: Optional[List[str]] = Field(default=[])


class SubscriptionTierUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    price_usd: Optional[float] = Field(None, gt=0)
    price_cents: Optional[int] = Field(None, gt=0)
    minutes_allowance: Optional[int] = Field(None, ge=0)
    initial_credits: Optional[int] = Field(None, ge=0)
    stripe_price_id: Optional[str] = None
    stripe_product_id: Optional[str] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None
    features: Optional[List[str]] = None


class SubscriptionTierResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: Optional[str] = None
    price_usd: float
    price_cents: int
    minutes_allowance: int
    initial_credits: int
    stripe_price_id: Optional[str] = None
    stripe_product_id: Optional[str] = None
    is_active: bool
    display_order: int
    features: List[str]
    created_at: datetime
    updated_at: datetime


# ============================================
# Agent Template Models
# ============================================

class AgentTemplateResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    system_prompt: str
    category: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ============================================
# Agent Models
# ============================================

class MessageMedium(str, Enum):
    VOICE = "MESSAGE_MEDIUM_VOICE"
    TEXT = "MESSAGE_MEDIUM_TEXT"
    UNSPECIFIED = "MESSAGE_MEDIUM_UNSPECIFIED"


class FirstSpeaker(str, Enum):
    AGENT = "agent"
    USER = "user"


class GreetingSettings(BaseModel):
    first_speaker: FirstSpeaker = Field(FirstSpeaker.AGENT, description="Who speaks first: agent or user")
    text: Optional[str] = Field(None, description="First message text (if agent speaks first)")
    prompt: Optional[str] = Field(None, description="Prompt for generating first message")
    delay: Optional[str] = Field(None, description="Delay before first message (e.g., '2s')")
    uninterruptible: Optional[bool] = Field(False, description="Whether first message is uninterruptible")
    fallback_delay: Optional[str] = Field(None, description="Fallback delay if user doesn't speak")
    fallback_text: Optional[str] = Field(None, description="Fallback text if user doesn't speak")
    fallback_prompt: Optional[str] = Field(None, description="Fallback prompt if user doesn't speak")


class VADSettings(BaseModel):
    turn_endpoint_delay: Optional[str] = Field(None, description="Turn endpoint delay (e.g., '500ms')")
    minimum_turn_duration: Optional[str] = Field(None, description="Minimum turn duration (e.g., '200ms')")
    minimum_interruption_duration: Optional[str] = Field(None, description="Minimum interruption duration (e.g., '100ms')")
    frame_activation_threshold: Optional[float] = Field(None, ge=0, le=1, description="Frame activation threshold (0-1)")


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    voice_id: str = Field(..., description="UUID of the voice to use")
    system_prompt: str = Field(..., min_length=1)
    model: str = Field(default="ultravox-v0.6")
    tools: Optional[List[str]] = Field(default=[], description="List of tool IDs")
    knowledge_bases: Optional[List[str]] = Field(default=[], description="List of knowledge base IDs")
    template_id: Optional[str] = Field(None, description="Template ID used to create this agent")
    
    # Call template fields
    call_template_name: Optional[str] = None
    greeting_settings: Optional[GreetingSettings] = None
    inactivity_messages: Optional[List[InactivityMessage]] = Field(default=[])
    temperature: Optional[float] = Field(0.3, ge=0, le=1)
    language_hint: Optional[str] = Field("en-US", description="BCP47 language code")
    time_exceeded_message: Optional[str] = None
    recording_enabled: Optional[bool] = False
    join_timeout: Optional[str] = Field("30s")
    max_duration: Optional[str] = Field("3600s")
    initial_output_medium: Optional[MessageMedium] = MessageMedium.VOICE
    vad_settings: Optional[VADSettings] = None
    
    # Legacy fields (kept for compatibility)
    success_criteria: Optional[str] = None
    extraction_schema: Optional[Dict[str, Any]] = None
    crm_webhook_url: Optional[str] = None
    crm_webhook_secret: Optional[str] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    voice_id: Optional[str] = None
    system_prompt: Optional[str] = Field(None, min_length=1)
    model: Optional[str] = None
    tools: Optional[List[str]] = None
    knowledge_bases: Optional[List[str]] = None
    
    # Call template fields
    call_template_name: Optional[str] = None
    greeting_settings: Optional[GreetingSettings] = None
    inactivity_messages: Optional[List[InactivityMessage]] = None
    temperature: Optional[float] = Field(None, ge=0, le=1)
    language_hint: Optional[str] = None
    time_exceeded_message: Optional[str] = None
    recording_enabled: Optional[bool] = None
    join_timeout: Optional[str] = None
    max_duration: Optional[str] = None
    initial_output_medium: Optional[MessageMedium] = None
    vad_settings: Optional[VADSettings] = None
    
    # Legacy fields
    success_criteria: Optional[str] = None
    extraction_schema: Optional[Dict[str, Any]] = None
    crm_webhook_url: Optional[str] = None
    crm_webhook_secret: Optional[str] = None


class AgentResponse(BaseModel):
    id: str
    client_id: str
    ultravox_agent_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    voice_id: str
    system_prompt: str
    model: str
    tools: List[str]
    knowledge_bases: List[str]
    status: str
    created_at: datetime
    updated_at: datetime
    
    # Call template fields
    call_template_name: Optional[str] = None
    greeting_settings: Optional[Dict[str, Any]] = None
    inactivity_messages: Optional[List[Dict[str, Any]]] = None
    temperature: Optional[float] = None
    language_hint: Optional[str] = None
    time_exceeded_message: Optional[str] = None
    recording_enabled: Optional[bool] = None
    join_timeout: Optional[str] = None
    max_duration: Optional[str] = None
    initial_output_medium: Optional[str] = None
    vad_settings: Optional[Dict[str, Any]] = None
    template_id: Optional[str] = None
    
    # Legacy fields
    configuration: Optional[Dict[str, Any]] = None
    success_criteria: Optional[str] = None
    extraction_schema: Optional[Dict[str, Any]] = None
    crm_webhook_url: Optional[str] = None
    crm_webhook_secret: Optional[str] = None


class AgentTestCallRequest(BaseModel):
    pass  # No additional parameters needed, uses agent configuration


class AgentTestCallResponse(BaseModel):
    call_id: str
    join_url: str
    agent_id: str
    created_at: datetime


class AgentAIAssistRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="User's prompt/question")
    context: Optional[Dict[str, Any]] = Field(None, description="Current agent data for context")
    action: Optional[str] = Field(None, description="Action type: improve_prompt, suggest_greeting, etc.")


class AgentAIAssistResponse(BaseModel):
    suggestion: str = Field(..., description="AI-generated suggestion")
    improved_content: Optional[str] = Field(None, description="Improved content if applicable")


# ============================================
# Contact Management Models
# ============================================

class ContactFolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Folder name")
    description: Optional[str] = Field(None, max_length=500, description="Folder description")


class ContactFolderUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="Folder name")
    description: Optional[str] = Field(None, max_length=500, description="Folder description")


class ContactFolderResponse(BaseModel):
    id: str
    client_id: str
    name: str
    description: Optional[str] = None
    contact_count: Optional[int] = 0
    created_at: datetime
    updated_at: datetime


class ContactCreate(BaseModel):
    folder_id: str = Field(..., description="Folder ID to add contact to")
    first_name: Optional[str] = Field(None, max_length=50, description="First name")
    last_name: Optional[str] = Field(None, max_length=50, description="Last name")
    email: Optional[str] = Field(None, description="Email address")
    phone_number: str = Field(..., description="Phone number in E.164 format")
    # New standard fields
    company_name: Optional[str] = Field(None, max_length=100, description="Company name")
    industry: Optional[str] = Field(None, max_length=100, description="Industry")
    location: Optional[str] = Field(None, max_length=100, description="Location")
    pin_code: Optional[str] = Field(None, max_length=20, description="Pin code")
    keywords: Optional[List[str]] = Field(None, description="Keywords (array)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    
    @validator('email')
    def validate_email(cls, v):
        if v and v.strip():
            import re
            email_regex = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
            if not email_regex.match(v.strip()):
                raise ValueError('Invalid email format')
        return v.strip() if v else None
    
    @validator('phone_number')
    def validate_phone(cls, v):
        if not v or not v.strip():
            raise ValueError('phone_number is required')
        # Phone validation will be done in service layer for normalization
        return v


class ContactUpdate(BaseModel):
    folder_id: Optional[str] = Field(None, description="Folder ID")
    first_name: Optional[str] = Field(None, max_length=50, description="First name")
    last_name: Optional[str] = Field(None, max_length=50, description="Last name")
    email: Optional[str] = Field(None, description="Email address")
    phone_number: Optional[str] = Field(None, description="Phone number in E.164 format")
    # New standard fields
    company_name: Optional[str] = Field(None, max_length=100, description="Company name")
    industry: Optional[str] = Field(None, max_length=100, description="Industry")
    location: Optional[str] = Field(None, max_length=100, description="Location")
    pin_code: Optional[str] = Field(None, max_length=20, description="Pin code")
    keywords: Optional[List[str]] = Field(None, description="Keywords (array)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    
    @validator('email')
    def validate_email(cls, v):
        if v and v.strip():
            import re
            email_regex = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
            if not email_regex.match(v.strip()):
                raise ValueError('Invalid email format')
        return v.strip() if v else None


class ContactResponse(BaseModel):
    id: str
    client_id: str
    folder_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: str
    # New standard fields
    company_name: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    pin_code: Optional[str] = None
    keywords: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime


class ContactBulkCreate(BaseModel):
    contacts: List[ContactCreate] = Field(..., min_items=1, description="List of contacts to create")


class ContactBulkDelete(BaseModel):
    contact_ids: List[str] = Field(..., min_items=1, description="List of contact IDs to delete")


class ContactImportRequest(BaseModel):
    folder_id: str = Field(..., description="Folder ID to import contacts into")
    file_key: Optional[str] = Field(None, description="Storage key for uploaded CSV file (legacy)")
    contacts: Optional[List[ContactCreate]] = Field(None, description="Direct contact data array (legacy)")
    # New: Base64 CSV file upload
    base64_file: Optional[str] = Field(None, description="Base64 encoded CSV file content")
    filename: Optional[str] = Field(None, description="Original filename of the CSV")
    # New: Mapping configuration for dynamic field mapping
    mapping_config: Optional[Dict[str, str]] = Field(
        None, 
        description="Mapping from CSV headers to standard fields. Format: {'csv_header': 'standard_field'}. Unmapped fields go to metadata."
    )


class ContactImportResponse(BaseModel):
    successful: int = Field(..., description="Number of successfully imported contacts")
    failed: int = Field(..., description="Number of failed imports")
    errors: Optional[List[Dict[str, Any]]] = Field(None, description="List of errors with row numbers")


class ContactExportResponse(BaseModel):
    csv_content: Optional[str] = Field(None, description="CSV content as string")
    download_url: Optional[str] = Field(None, description="Presigned URL for download")
    file_key: Optional[str] = Field(None, description="Storage key for exported file")
