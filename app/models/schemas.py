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
    service: str = Field(..., description="Service name")
    key_name: str = Field(..., description="User-friendly name")
    api_key: str = Field(..., description="API key value")
    settings: Optional[Dict[str, Any]] = Field(default={})


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
# Agent Models
# ============================================

class EndBehavior(str, Enum):
    UNSPECIFIED = "END_BEHAVIOR_UNSPECIFIED"
    HANG_UP_SOFT = "END_BEHAVIOR_HANG_UP_SOFT"
    HANG_UP_STRICT = "END_BEHAVIOR_HANG_UP_STRICT"


class InactivityMessage(BaseModel):
    duration: str = Field(..., description="Duration in string format (e.g., '30s')")
    message: str = Field(..., description="Message to play when inactive")
    endBehavior: Optional[EndBehavior] = Field(EndBehavior.UNSPECIFIED, description="Behavior when message finishes")


class AgentTool(BaseModel):
    tool_id: str
    enabled: bool = True
    parameters: Optional[Dict[str, Any]] = None


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    voice_id: Optional[str] = None
    system_prompt: str = Field(..., min_length=10, max_length=5000)
    model: str = Field(default="fixie-ai/ultravox-v0_4-8k")
    tools: Optional[List[AgentTool]] = Field(default=[])
    # New fields for Ultravox API integration
    agentLanguage: Optional[str] = Field(None, description="Agent language: english, spanish, french")
    firstMessage: Optional[str] = Field(None, description="First message/greeting")
    disableInterruptions: Optional[bool] = Field(False, description="Disable interruptions during first message")
    selectedLLM: Optional[str] = Field(None, description="Selected LLM model: glm-4.5-air, gpt-4, claude-3")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="Temperature (0.0-2.0)")
    stability: Optional[float] = Field(None, ge=0.0, le=1.0, description="Voice stability (0.0-1.0)")
    similarity: Optional[float] = Field(None, ge=0.0, le=1.0, description="Voice similarity boost (0.0-1.0)")
    speed: Optional[float] = Field(None, ge=0.25, le=4.0, description="Voice speed (0.25-4.0, OpenAI only)")
    turnTimeout: Optional[int] = Field(None, ge=10, le=300, description="Turn timeout in seconds (10-300)")
    maxConversationDuration: Optional[int] = Field(None, ge=1, description="Max conversation duration in seconds")
    # New fields from Ultravox UI
    firstMessageDelay: Optional[int] = Field(None, ge=0, description="First message delay in seconds")
    firstSpeaker: Optional[str] = Field(None, description="First speaker: agent or user")
    joinTimeout: Optional[int] = Field(None, ge=0, description="Join timeout in seconds")
    timeExceededMessage: Optional[str] = Field(None, description="Time exceeded message")
    # Voice Activity Detection parameters
    turnEndpointDelay: Optional[int] = Field(None, ge=0, description="Turn endpoint delay in milliseconds")
    minimumTurnDuration: Optional[int] = Field(None, ge=0, description="Minimum turn duration in milliseconds")
    minimumInterruptionDuration: Optional[int] = Field(None, ge=0, description="Minimum interruption duration in milliseconds")
    frameActivationThreshold: Optional[float] = Field(None, ge=0.1, le=1.0, description="Frame activation threshold (0.1-1.0)")
    # Additional Ultravox API fields
    confidenceThreshold: Optional[float] = Field(None, ge=0.0, le=1.0, description="Confidence threshold (0.0-1.0)")
    fallbackResponse: Optional[str] = Field(None, description="Fallback response message")
    timezone: Optional[str] = Field(None, description="Timezone (e.g., 'UTC', 'America/New_York')")
    personality: Optional[str] = Field(None, description="Personality: professional, friendly, casual, expert, empathetic")
    voicePitch: Optional[float] = Field(None, ge=-20, le=20, description="Voice pitch in semitones (-20 to 20)")
    voiceStyle: Optional[float] = Field(None, ge=0.0, le=1.0, description="Voice style (0.0-1.0, ElevenLabs only)")
    useSpeakerBoost: Optional[bool] = Field(None, description="Use speaker boost (ElevenLabs only)")
    # Additional ElevenLabs fields
    elevenLabsModel: Optional[str] = Field(None, description="ElevenLabs model (e.g., 'eleven_multilingual_v2')")
    pronunciationDictionaries: Optional[List[Dict[str, str]]] = Field(None, description="Pronunciation dictionaries for ElevenLabs")
    optimizeStreamingLatency: Optional[int] = Field(None, ge=0, le=4, description="Optimize streaming latency (0-4, ElevenLabs)")
    maxSampleRate: Optional[int] = Field(None, description="Max sample rate for ElevenLabs")
    # Cartesia voice provider fields
    cartesiaModel: Optional[str] = Field(None, description="Cartesia model")
    cartesiaSpeed: Optional[float] = Field(None, description="Cartesia speed")
    cartesiaEmotion: Optional[str] = Field(None, description="Cartesia emotion")
    cartesiaEmotions: Optional[List[str]] = Field(None, description="Cartesia emotions list")
    cartesiaGenerationConfig: Optional[Dict[str, Any]] = Field(None, description="Cartesia generation config (volume, speed, emotion)")
    # LMNT voice provider fields
    lmntModel: Optional[str] = Field(None, description="LMNT model")
    lmntSpeed: Optional[float] = Field(None, description="LMNT speed")
    lmntConversational: Optional[bool] = Field(None, description="LMNT conversational mode")
    # First speaker settings additional fields
    userFallbackPrompt: Optional[str] = Field(None, description="User fallback prompt for firstSpeakerSettings")
    userFallbackText: Optional[str] = Field(None, description="User fallback text for firstSpeakerSettings")
    agentPrompt: Optional[str] = Field(None, description="Agent prompt for firstSpeakerSettings")
    # Recording and output settings
    recordingEnabled: Optional[bool] = Field(None, description="Enable call recording")
    initialOutputMedium: Optional[str] = Field(None, description="Initial output medium (e.g., 'MESSAGE_MEDIUM_VOICE')")
    # Inactivity messages
    inactivityMessages: Optional[List[Dict[str, Any]]] = Field(None, description="Inactivity messages with duration, message, and endBehavior")


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    system_prompt: Optional[str] = Field(None, min_length=10, max_length=5000)
    voice_id: Optional[str] = None
    tools: Optional[List[AgentTool]] = None
    # New fields for Ultravox API integration
    agentLanguage: Optional[str] = Field(None, description="Agent language: english, spanish, french")
    firstMessage: Optional[str] = Field(None, description="First message/greeting")
    disableInterruptions: Optional[bool] = Field(None, description="Disable interruptions during first message")
    selectedLLM: Optional[str] = Field(None, description="Selected LLM model: glm-4.5-air, gpt-4, claude-3")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="Temperature (0.0-2.0)")
    stability: Optional[float] = Field(None, ge=0.0, le=1.0, description="Voice stability (0.0-1.0)")
    similarity: Optional[float] = Field(None, ge=0.0, le=1.0, description="Voice similarity boost (0.0-1.0)")
    speed: Optional[float] = Field(None, ge=0.25, le=4.0, description="Voice speed (0.25-4.0, OpenAI only)")
    turnTimeout: Optional[int] = Field(None, ge=10, le=300, description="Turn timeout in seconds (10-300)")
    maxConversationDuration: Optional[int] = Field(None, ge=1, description="Max conversation duration in seconds")
    # New fields from Ultravox UI
    firstMessageDelay: Optional[int] = Field(None, ge=0, description="First message delay in seconds")
    firstSpeaker: Optional[str] = Field(None, description="First speaker: agent or user")
    joinTimeout: Optional[int] = Field(None, ge=0, description="Join timeout in seconds")
    timeExceededMessage: Optional[str] = Field(None, description="Time exceeded message")
    # Voice Activity Detection parameters
    turnEndpointDelay: Optional[int] = Field(None, ge=0, description="Turn endpoint delay in milliseconds")
    minimumTurnDuration: Optional[int] = Field(None, ge=0, description="Minimum turn duration in milliseconds")
    minimumInterruptionDuration: Optional[int] = Field(None, ge=0, description="Minimum interruption duration in milliseconds")
    frameActivationThreshold: Optional[float] = Field(None, ge=0.1, le=1.0, description="Frame activation threshold (0.1-1.0)")
    # Additional Ultravox API fields
    confidenceThreshold: Optional[float] = Field(None, ge=0.0, le=1.0, description="Confidence threshold (0.0-1.0)")
    fallbackResponse: Optional[str] = Field(None, description="Fallback response message")
    timezone: Optional[str] = Field(None, description="Timezone (e.g., 'UTC', 'America/New_York')")
    personality: Optional[str] = Field(None, description="Personality: professional, friendly, casual, expert, empathetic")
    voicePitch: Optional[float] = Field(None, ge=-20, le=20, description="Voice pitch in semitones (-20 to 20)")
    voiceStyle: Optional[float] = Field(None, ge=0.0, le=1.0, description="Voice style (0.0-1.0, ElevenLabs only)")
    useSpeakerBoost: Optional[bool] = Field(None, description="Use speaker boost (ElevenLabs only)")
    # Additional ElevenLabs fields
    elevenLabsModel: Optional[str] = Field(None, description="ElevenLabs model (e.g., 'eleven_multilingual_v2')")
    pronunciationDictionaries: Optional[List[Dict[str, str]]] = Field(None, description="Pronunciation dictionaries for ElevenLabs")
    optimizeStreamingLatency: Optional[int] = Field(None, ge=0, le=4, description="Optimize streaming latency (0-4, ElevenLabs)")
    maxSampleRate: Optional[int] = Field(None, description="Max sample rate for ElevenLabs")
    # Cartesia voice provider fields
    cartesiaModel: Optional[str] = Field(None, description="Cartesia model")
    cartesiaSpeed: Optional[float] = Field(None, description="Cartesia speed")
    cartesiaEmotion: Optional[str] = Field(None, description="Cartesia emotion")
    cartesiaEmotions: Optional[List[str]] = Field(None, description="Cartesia emotions list")
    cartesiaGenerationConfig: Optional[Dict[str, Any]] = Field(None, description="Cartesia generation config (volume, speed, emotion)")
    # LMNT voice provider fields
    lmntModel: Optional[str] = Field(None, description="LMNT model")
    lmntSpeed: Optional[float] = Field(None, description="LMNT speed")
    lmntConversational: Optional[bool] = Field(None, description="LMNT conversational mode")
    # First speaker settings additional fields
    userFallbackPrompt: Optional[str] = Field(None, description="User fallback prompt for firstSpeakerSettings")
    userFallbackText: Optional[str] = Field(None, description="User fallback text for firstSpeakerSettings")
    agentPrompt: Optional[str] = Field(None, description="Agent prompt for firstSpeakerSettings")
    # Recording and output settings
    recordingEnabled: Optional[bool] = Field(None, description="Enable call recording")
    initialOutputMedium: Optional[str] = Field(None, description="Initial output medium (e.g., 'MESSAGE_MEDIUM_VOICE')")
    # Inactivity messages
    inactivityMessages: Optional[List[InactivityMessage]] = Field(None, description="Inactivity messages with duration, message, and endBehavior")


class AgentResponse(BaseModel):
    id: str
    client_id: str
    ultravox_agent_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    voice_id: Optional[str] = None
    system_prompt: str
    model: str
    tools: List[Dict[str, Any]]
    status: str
    created_at: datetime
    updated_at: datetime


# ============================================
# Call Models
# ============================================

class CallSettings(BaseModel):
    recording_enabled: bool = True
    transcription_enabled: bool = True
    greeting: Optional[str] = None


class CallCreate(BaseModel):
    agent_id: str
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
    agent_id: str
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
    agent_id: str
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
    agent_id: str
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
