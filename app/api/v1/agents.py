"""
Agent Endpoints
"""
from fastapi import APIRouter, Header, Depends
from starlette.requests import Request
from typing import Optional
from datetime import datetime
import uuid
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ForbiddenError, ValidationError, ProviderError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.core.events import emit_agent_created, emit_agent_updated
from app.services.ultravox import ultravox_client
import logging

logger = logging.getLogger(__name__)
from app.models.schemas import (
    AgentCreate,
    AgentUpdate,
    AgentResponse,
    BulkDeleteRequest,
    BulkDeleteResponse,
    ResponseMeta,
)

router = APIRouter()


def build_call_template_from_agent_data(
    agent_data,  # Can be AgentCreate or AgentUpdate
    voice: dict,
    corpus_ids: list,
    mapped_language: str,
) -> dict:
    """
    Build callTemplate structure for Ultravox API from agent data.
    This function maps our agent data structure to the new Ultravox callTemplate-based API.
    """
    # Build externalVoice object based on provider
    external_voice = {}
    provider = voice.get("provider", "elevenlabs")
    voice_id = voice.get("ultravox_voice_id")
    
    if provider == "elevenlabs":
        elevenlabs_voice = {
            "voiceId": voice_id,
        }
        if agent_data.stability is not None:
            elevenlabs_voice["stability"] = agent_data.stability
        if agent_data.similarity is not None:
            elevenlabs_voice["similarityBoost"] = agent_data.similarity
        if agent_data.voiceStyle is not None:
            elevenlabs_voice["style"] = agent_data.voiceStyle
        if agent_data.useSpeakerBoost is not None:
            elevenlabs_voice["useSpeakerBoost"] = agent_data.useSpeakerBoost
        if agent_data.speed is not None:
            elevenlabs_voice["speed"] = agent_data.speed
        if agent_data.voicePitch is not None:
            elevenlabs_voice["pitch"] = agent_data.voicePitch
        if hasattr(agent_data, 'elevenLabsModel') and agent_data.elevenLabsModel is not None:
            elevenlabs_voice["model"] = agent_data.elevenLabsModel
        if hasattr(agent_data, 'pronunciationDictionaries') and agent_data.pronunciationDictionaries is not None:
            elevenlabs_voice["pronunciationDictionaries"] = agent_data.pronunciationDictionaries
        if hasattr(agent_data, 'optimizeStreamingLatency') and agent_data.optimizeStreamingLatency is not None:
            elevenlabs_voice["optimizeStreamingLatency"] = agent_data.optimizeStreamingLatency
        if hasattr(agent_data, 'maxSampleRate') and agent_data.maxSampleRate is not None:
            elevenlabs_voice["maxSampleRate"] = agent_data.maxSampleRate
        external_voice["elevenLabs"] = elevenlabs_voice
    elif provider == "openai":
        openai_voice = {
            "voiceId": voice_id,
        }
        if agent_data.speed is not None:
            openai_voice["speed"] = agent_data.speed
        if agent_data.voicePitch is not None:
            openai_voice["pitch"] = agent_data.voicePitch
        external_voice["openai"] = openai_voice
    elif provider == "google":
        google_voice = {
            "voiceId": voice_id,
        }
        if agent_data.speed is not None:
            google_voice["speakingRate"] = agent_data.speed
        # Note: Google voice does not support pitch according to API docs
        external_voice["google"] = google_voice
    elif provider == "cartesia":
        cartesia_voice = {
            "voiceId": voice_id,
        }
        if hasattr(agent_data, 'cartesiaModel') and agent_data.cartesiaModel is not None:
            cartesia_voice["model"] = agent_data.cartesiaModel
        if agent_data.speed is not None:
            cartesia_voice["speed"] = agent_data.speed
        if hasattr(agent_data, 'cartesiaEmotion') and agent_data.cartesiaEmotion is not None:
            cartesia_voice["emotion"] = agent_data.cartesiaEmotion
        if hasattr(agent_data, 'cartesiaEmotions') and agent_data.cartesiaEmotions is not None:
            cartesia_voice["emotions"] = agent_data.cartesiaEmotions
        if hasattr(agent_data, 'cartesiaGenerationConfig') and agent_data.cartesiaGenerationConfig is not None:
            cartesia_voice["generationConfig"] = agent_data.cartesiaGenerationConfig
        external_voice["cartesia"] = cartesia_voice
    elif provider == "lmnt":
        lmnt_voice = {
            "voiceId": voice_id,
        }
        if hasattr(agent_data, 'lmntModel') and agent_data.lmntModel is not None:
            lmnt_voice["model"] = agent_data.lmntModel
        if agent_data.speed is not None:
            lmnt_voice["speed"] = agent_data.speed
        if hasattr(agent_data, 'lmntConversational') and agent_data.lmntConversational is not None:
            lmnt_voice["conversational"] = agent_data.lmntConversational
        external_voice["lmnt"] = lmnt_voice
    elif provider == "generic":
        generic_voice = {
            "url": voice_id,  # For generic, voice_id might be the URL
        }
        if hasattr(agent_data, 'genericUrl') and agent_data.genericUrl is not None:
            generic_voice["url"] = agent_data.genericUrl
        if hasattr(agent_data, 'genericHeaders') and agent_data.genericHeaders is not None:
            generic_voice["headers"] = agent_data.genericHeaders
        if hasattr(agent_data, 'genericBody') and agent_data.genericBody is not None:
            generic_voice["body"] = agent_data.genericBody
        if hasattr(agent_data, 'genericResponseSampleRate') and agent_data.genericResponseSampleRate is not None:
            generic_voice["responseSampleRate"] = agent_data.genericResponseSampleRate
        if hasattr(agent_data, 'genericResponseWordsPerMinute') and agent_data.genericResponseWordsPerMinute is not None:
            generic_voice["responseWordsPerMinute"] = agent_data.genericResponseWordsPerMinute
        if hasattr(agent_data, 'genericResponseMimeType') and agent_data.genericResponseMimeType is not None:
            generic_voice["responseMimeType"] = agent_data.genericResponseMimeType
        if hasattr(agent_data, 'genericJsonAudioFieldPath') and agent_data.genericJsonAudioFieldPath is not None:
            generic_voice["jsonAudioFieldPath"] = agent_data.genericJsonAudioFieldPath
        if hasattr(agent_data, 'genericJsonByteEncoding') and agent_data.genericJsonByteEncoding is not None:
            generic_voice["jsonByteEncoding"] = agent_data.genericJsonByteEncoding
        external_voice["generic"] = generic_voice
    else:
        # Generic fallback - use voice_id as string
        external_voice = None
    
    # Build firstSpeakerSettings
    first_speaker_settings = {}
    if agent_data.firstSpeaker:
        if agent_data.firstSpeaker.lower() == "user":
            first_speaker_settings["user"] = {}
            user_fallback = {}
            if agent_data.firstMessageDelay is not None:
                user_fallback["delay"] = f"{agent_data.firstMessageDelay}s"
            if hasattr(agent_data, 'userFallbackText') and agent_data.userFallbackText is not None:
                user_fallback["text"] = agent_data.userFallbackText
            if hasattr(agent_data, 'userFallbackPrompt') and agent_data.userFallbackPrompt is not None:
                user_fallback["prompt"] = agent_data.userFallbackPrompt
            if user_fallback:
                first_speaker_settings["user"]["fallback"] = user_fallback
        elif agent_data.firstSpeaker.lower() == "agent":
            first_speaker_settings["agent"] = {}
            if agent_data.disableInterruptions is not None:
                first_speaker_settings["agent"]["uninterruptible"] = agent_data.disableInterruptions
            if agent_data.firstMessage:
                first_speaker_settings["agent"]["text"] = agent_data.firstMessage
            if hasattr(agent_data, 'agentPrompt') and agent_data.agentPrompt is not None:
                first_speaker_settings["agent"]["prompt"] = agent_data.agentPrompt
            if agent_data.firstMessageDelay is not None:
                first_speaker_settings["agent"]["delay"] = f"{agent_data.firstMessageDelay}s"
    
    # Build VAD settings
    vad_settings = {}
    if agent_data.turnEndpointDelay is not None:
        vad_settings["turnEndpointDelay"] = f"{agent_data.turnEndpointDelay}ms"
    if agent_data.minimumTurnDuration is not None:
        vad_settings["minimumTurnDuration"] = f"{agent_data.minimumTurnDuration}ms"
    if agent_data.minimumInterruptionDuration is not None:
        vad_settings["minimumInterruptionDuration"] = f"{agent_data.minimumInterruptionDuration}ms"
    if agent_data.frameActivationThreshold is not None:
        vad_settings["frameActivationThreshold"] = agent_data.frameActivationThreshold
    
    # Build selectedTools from tools
    selected_tools = []
    if agent_data.tools:
        for tool in agent_data.tools:
            tool_dict = tool.dict() if hasattr(tool, 'dict') else tool
            selected_tools.append({
                "toolId": tool_dict.get("tool_id"),
                "toolName": tool_dict.get("tool_name", tool_dict.get("tool_id")),
            })
    
    # Auto-load Knowledge Base tools if agent has linked KBs
    # Note: This requires knowledge_bases to be passed to this function
    # The caller should pass it from agent_data.knowledge_bases
    if hasattr(agent_data, 'knowledge_bases') and agent_data.knowledge_bases:
        # Import here to avoid circular dependency
        from app.core.database import DatabaseService
        # Note: We need db and current_user context - this will be handled by the caller
        # For now, we'll add a placeholder that the caller can populate
        pass  # Will be handled by caller with proper DB context
    
    # Convert maxConversationDuration to maxDuration string format
    max_duration = None
    if agent_data.maxConversationDuration is not None:
        max_duration = f"{agent_data.maxConversationDuration}s"
    elif agent_data.turnTimeout is not None:
        # Fallback: use turnTimeout * 20 as rough estimate
        max_duration = f"{agent_data.turnTimeout * 20}s"
    else:
        max_duration = "3600s"  # Default 1 hour
    
    # Convert joinTimeout to string format
    join_timeout = None
    if agent_data.joinTimeout is not None:
        join_timeout = f"{agent_data.joinTimeout}s"
    else:
        join_timeout = "30s"  # Default
    
    # Build callTemplate object
    call_template = {
        "systemPrompt": agent_data.system_prompt,
        "temperature": agent_data.temperature if agent_data.temperature is not None else 0.7,
        "model": agent_data.selectedLLM if agent_data.selectedLLM is not None else agent_data.model,
    }
    
    # Add voice (either externalVoice or voice string)
    if external_voice:
        call_template["externalVoice"] = external_voice
    elif voice_id:
        call_template["voice"] = voice_id
    
    # Add languageHint
    if mapped_language:
        call_template["languageHint"] = mapped_language
    
    # Add timeExceededMessage
    if agent_data.timeExceededMessage:
        call_template["timeExceededMessage"] = agent_data.timeExceededMessage
    
    # Add joinTimeout
    call_template["joinTimeout"] = join_timeout
    
    # Add maxDuration
    call_template["maxDuration"] = max_duration
    
    # Add VAD settings
    if vad_settings:
        call_template["vadSettings"] = vad_settings
    
    # Add firstSpeakerSettings
    if first_speaker_settings:
        call_template["firstSpeakerSettings"] = first_speaker_settings
    
    # Add recordingEnabled
    if hasattr(agent_data, 'recordingEnabled') and agent_data.recordingEnabled is not None:
        call_template["recordingEnabled"] = agent_data.recordingEnabled
    else:
        call_template["recordingEnabled"] = True  # Default to True
    
    # Add initialOutputMedium
    if hasattr(agent_data, 'initialOutputMedium') and agent_data.initialOutputMedium is not None:
        call_template["initialOutputMedium"] = agent_data.initialOutputMedium
    else:
        call_template["initialOutputMedium"] = "MESSAGE_MEDIUM_VOICE"  # Default to VOICE
    
    # Add inactivityMessages if provided
    if hasattr(agent_data, 'inactivityMessages') and agent_data.inactivityMessages is not None:
        # Convert InactivityMessage objects to dicts for API
        inactivity_messages = []
        for msg in agent_data.inactivityMessages:
            if hasattr(msg, 'dict'):
                # Pydantic model - convert to dict
                msg_dict = msg.dict()
                # Ensure endBehavior is a string (enum value)
                if 'endBehavior' in msg_dict and hasattr(msg_dict['endBehavior'], 'value'):
                    msg_dict['endBehavior'] = msg_dict['endBehavior'].value
                inactivity_messages.append(msg_dict)
            else:
                # Already a dict
                inactivity_messages.append(msg)
        call_template["inactivityMessages"] = inactivity_messages
    
    # Add selectedTools
    if selected_tools:
        call_template["selectedTools"] = selected_tools
    
    # Note: Knowledge base is not part of callTemplate in the new Ultravox API
    # Knowledge bases are handled separately through corpus management
    
    return call_template


def build_partial_call_template_from_update(
    agent_data: AgentUpdate,
    voice: Optional[dict] = None,
    corpus_ids: Optional[list] = None,
    mapped_language: Optional[str] = None,
) -> dict:
    """
    Build partial callTemplate structure for Ultravox API PATCH updates.
    Only includes fields that are being updated (not None).
    """
    call_template = {}
    
    # Map systemPrompt if provided
    if agent_data.system_prompt is not None:
        call_template["systemPrompt"] = agent_data.system_prompt
    
    # Map temperature if provided
    if agent_data.temperature is not None:
        call_template["temperature"] = agent_data.temperature
    
    # Map model if provided
    if agent_data.selectedLLM is not None:
        call_template["model"] = agent_data.selectedLLM
    elif agent_data.model is not None:
        call_template["model"] = agent_data.model
    
    # Build externalVoice if voice is provided and voice settings are being updated
    if voice and voice.get("ultravox_voice_id"):
        external_voice = {}
        provider = voice.get("provider", "elevenlabs")
        voice_id = voice.get("ultravox_voice_id")
        
        # Only build externalVoice if voice settings are being updated
        voice_settings_updated = (
            agent_data.stability is not None or
            agent_data.similarity is not None or
            agent_data.voiceStyle is not None or
            agent_data.useSpeakerBoost is not None or
            agent_data.speed is not None or
            agent_data.voicePitch is not None or
            (hasattr(agent_data, 'elevenLabsModel') and agent_data.elevenLabsModel is not None) or
            (hasattr(agent_data, 'pronunciationDictionaries') and agent_data.pronunciationDictionaries is not None) or
            (hasattr(agent_data, 'optimizeStreamingLatency') and agent_data.optimizeStreamingLatency is not None) or
            (hasattr(agent_data, 'maxSampleRate') and agent_data.maxSampleRate is not None) or
            (hasattr(agent_data, 'cartesiaModel') and agent_data.cartesiaModel is not None) or
            (hasattr(agent_data, 'cartesiaEmotion') and agent_data.cartesiaEmotion is not None) or
            (hasattr(agent_data, 'cartesiaEmotions') and agent_data.cartesiaEmotions is not None) or
            (hasattr(agent_data, 'cartesiaGenerationConfig') and agent_data.cartesiaGenerationConfig is not None) or
            (hasattr(agent_data, 'lmntModel') and agent_data.lmntModel is not None) or
            (hasattr(agent_data, 'lmntConversational') and agent_data.lmntConversational is not None) or
            (hasattr(agent_data, 'genericUrl') and agent_data.genericUrl is not None) or
            (hasattr(agent_data, 'genericHeaders') and agent_data.genericHeaders is not None) or
            (hasattr(agent_data, 'genericBody') and agent_data.genericBody is not None) or
            (hasattr(agent_data, 'genericResponseSampleRate') and agent_data.genericResponseSampleRate is not None) or
            (hasattr(agent_data, 'genericResponseWordsPerMinute') and agent_data.genericResponseWordsPerMinute is not None) or
            (hasattr(agent_data, 'genericResponseMimeType') and agent_data.genericResponseMimeType is not None) or
            (hasattr(agent_data, 'genericJsonAudioFieldPath') and agent_data.genericJsonAudioFieldPath is not None) or
            (hasattr(agent_data, 'genericJsonByteEncoding') and agent_data.genericJsonByteEncoding is not None)
        )
        
        if voice_settings_updated:
            if provider == "elevenlabs":
                elevenlabs_voice = {"voiceId": voice_id}
                if agent_data.stability is not None:
                    elevenlabs_voice["stability"] = agent_data.stability
                if agent_data.similarity is not None:
                    elevenlabs_voice["similarityBoost"] = agent_data.similarity
                if agent_data.voiceStyle is not None:
                    elevenlabs_voice["style"] = agent_data.voiceStyle
                if agent_data.useSpeakerBoost is not None:
                    elevenlabs_voice["useSpeakerBoost"] = agent_data.useSpeakerBoost
                if agent_data.speed is not None:
                    elevenlabs_voice["speed"] = agent_data.speed
                if agent_data.voicePitch is not None:
                    elevenlabs_voice["pitch"] = agent_data.voicePitch
                if hasattr(agent_data, 'elevenLabsModel') and agent_data.elevenLabsModel is not None:
                    elevenlabs_voice["model"] = agent_data.elevenLabsModel
                if hasattr(agent_data, 'pronunciationDictionaries') and agent_data.pronunciationDictionaries is not None:
                    elevenlabs_voice["pronunciationDictionaries"] = agent_data.pronunciationDictionaries
                if hasattr(agent_data, 'optimizeStreamingLatency') and agent_data.optimizeStreamingLatency is not None:
                    elevenlabs_voice["optimizeStreamingLatency"] = agent_data.optimizeStreamingLatency
                if hasattr(agent_data, 'maxSampleRate') and agent_data.maxSampleRate is not None:
                    elevenlabs_voice["maxSampleRate"] = agent_data.maxSampleRate
                external_voice["elevenLabs"] = elevenlabs_voice
            elif provider == "openai":
                openai_voice = {"voiceId": voice_id}
                if agent_data.speed is not None:
                    openai_voice["speed"] = agent_data.speed
                if agent_data.voicePitch is not None:
                    openai_voice["pitch"] = agent_data.voicePitch
                external_voice["openai"] = openai_voice
            elif provider == "google":
                google_voice = {"voiceId": voice_id}
                if agent_data.speed is not None:
                    google_voice["speakingRate"] = agent_data.speed
                # Note: Google voice does not support pitch according to API docs
                external_voice["google"] = google_voice
            elif provider == "cartesia":
                cartesia_voice = {"voiceId": voice_id}
                if hasattr(agent_data, 'cartesiaModel') and agent_data.cartesiaModel is not None:
                    cartesia_voice["model"] = agent_data.cartesiaModel
                if agent_data.speed is not None:
                    cartesia_voice["speed"] = agent_data.speed
                if hasattr(agent_data, 'cartesiaEmotion') and agent_data.cartesiaEmotion is not None:
                    cartesia_voice["emotion"] = agent_data.cartesiaEmotion
                if hasattr(agent_data, 'cartesiaEmotions') and agent_data.cartesiaEmotions is not None:
                    cartesia_voice["emotions"] = agent_data.cartesiaEmotions
                if hasattr(agent_data, 'cartesiaGenerationConfig') and agent_data.cartesiaGenerationConfig is not None:
                    cartesia_voice["generationConfig"] = agent_data.cartesiaGenerationConfig
                external_voice["cartesia"] = cartesia_voice
            elif provider == "lmnt":
                lmnt_voice = {"voiceId": voice_id}
                if hasattr(agent_data, 'lmntModel') and agent_data.lmntModel is not None:
                    lmnt_voice["model"] = agent_data.lmntModel
                if agent_data.speed is not None:
                    lmnt_voice["speed"] = agent_data.speed
                if hasattr(agent_data, 'lmntConversational') and agent_data.lmntConversational is not None:
                    lmnt_voice["conversational"] = agent_data.lmntConversational
                external_voice["lmnt"] = lmnt_voice
            elif provider == "generic":
                generic_voice = {}
                if hasattr(agent_data, 'genericUrl') and agent_data.genericUrl is not None:
                    generic_voice["url"] = agent_data.genericUrl
                elif voice_id:
                    generic_voice["url"] = voice_id
                if hasattr(agent_data, 'genericHeaders') and agent_data.genericHeaders is not None:
                    generic_voice["headers"] = agent_data.genericHeaders
                if hasattr(agent_data, 'genericBody') and agent_data.genericBody is not None:
                    generic_voice["body"] = agent_data.genericBody
                if hasattr(agent_data, 'genericResponseSampleRate') and agent_data.genericResponseSampleRate is not None:
                    generic_voice["responseSampleRate"] = agent_data.genericResponseSampleRate
                if hasattr(agent_data, 'genericResponseWordsPerMinute') and agent_data.genericResponseWordsPerMinute is not None:
                    generic_voice["responseWordsPerMinute"] = agent_data.genericResponseWordsPerMinute
                if hasattr(agent_data, 'genericResponseMimeType') and agent_data.genericResponseMimeType is not None:
                    generic_voice["responseMimeType"] = agent_data.genericResponseMimeType
                if hasattr(agent_data, 'genericJsonAudioFieldPath') and agent_data.genericJsonAudioFieldPath is not None:
                    generic_voice["jsonAudioFieldPath"] = agent_data.genericJsonAudioFieldPath
                if hasattr(agent_data, 'genericJsonByteEncoding') and agent_data.genericJsonByteEncoding is not None:
                    generic_voice["jsonByteEncoding"] = agent_data.genericJsonByteEncoding
                if generic_voice:
                    external_voice["generic"] = generic_voice
            
            if external_voice:
                call_template["externalVoice"] = external_voice
        elif voice_id:
            # Voice ID changed but no settings - just use voice string
            call_template["voice"] = voice_id
    
    # Add languageHint if provided
    if mapped_language:
        call_template["languageHint"] = mapped_language
    elif agent_data.agentLanguage is not None:
        language_map = {
            "english": "en-US",
            "spanish": "es-ES",
            "french": "fr-FR"
        }
        call_template["languageHint"] = language_map.get(agent_data.agentLanguage.lower(), "en-US")
    
    # Add timeExceededMessage if provided
    if agent_data.timeExceededMessage is not None:
        call_template["timeExceededMessage"] = agent_data.timeExceededMessage
    
    # Add joinTimeout if provided
    if agent_data.joinTimeout is not None:
        call_template["joinTimeout"] = f"{agent_data.joinTimeout}s"
    
    # Add maxDuration if provided
    if agent_data.maxConversationDuration is not None:
        call_template["maxDuration"] = f"{agent_data.maxConversationDuration}s"
    elif agent_data.turnTimeout is not None:
        # Fallback: use turnTimeout * 20 as rough estimate
        call_template["maxDuration"] = f"{agent_data.turnTimeout * 20}s"
    
    # Build VAD settings if any are provided
    vad_settings = {}
    if agent_data.turnEndpointDelay is not None:
        vad_settings["turnEndpointDelay"] = f"{agent_data.turnEndpointDelay}ms"
    if agent_data.minimumTurnDuration is not None:
        vad_settings["minimumTurnDuration"] = f"{agent_data.minimumTurnDuration}ms"
    if agent_data.minimumInterruptionDuration is not None:
        vad_settings["minimumInterruptionDuration"] = f"{agent_data.minimumInterruptionDuration}ms"
    if agent_data.frameActivationThreshold is not None:
        vad_settings["frameActivationThreshold"] = agent_data.frameActivationThreshold
    
    if vad_settings:
        call_template["vadSettings"] = vad_settings
    
    # Build firstSpeakerSettings if provided
    first_speaker_settings = {}
    if agent_data.firstSpeaker is not None:
        if agent_data.firstSpeaker.lower() == "user":
            first_speaker_settings["user"] = {}
            user_fallback = {}
            if agent_data.firstMessageDelay is not None:
                user_fallback["delay"] = f"{agent_data.firstMessageDelay}s"
            if hasattr(agent_data, 'userFallbackText') and agent_data.userFallbackText is not None:
                user_fallback["text"] = agent_data.userFallbackText
            if hasattr(agent_data, 'userFallbackPrompt') and agent_data.userFallbackPrompt is not None:
                user_fallback["prompt"] = agent_data.userFallbackPrompt
            if user_fallback:
                first_speaker_settings["user"]["fallback"] = user_fallback
        elif agent_data.firstSpeaker.lower() == "agent":
            first_speaker_settings["agent"] = {}
            if agent_data.disableInterruptions is not None:
                first_speaker_settings["agent"]["uninterruptible"] = agent_data.disableInterruptions
            if agent_data.firstMessage is not None:
                first_speaker_settings["agent"]["text"] = agent_data.firstMessage
            if hasattr(agent_data, 'agentPrompt') and agent_data.agentPrompt is not None:
                first_speaker_settings["agent"]["prompt"] = agent_data.agentPrompt
            if agent_data.firstMessageDelay is not None:
                first_speaker_settings["agent"]["delay"] = f"{agent_data.firstMessageDelay}s"
    
    if first_speaker_settings:
        call_template["firstSpeakerSettings"] = first_speaker_settings
    
    # Add recordingEnabled if provided
    if hasattr(agent_data, 'recordingEnabled') and agent_data.recordingEnabled is not None:
        call_template["recordingEnabled"] = agent_data.recordingEnabled
    
    # Add initialOutputMedium if provided
    if hasattr(agent_data, 'initialOutputMedium') and agent_data.initialOutputMedium is not None:
        call_template["initialOutputMedium"] = agent_data.initialOutputMedium
    
    # Add inactivityMessages if provided
    if hasattr(agent_data, 'inactivityMessages') and agent_data.inactivityMessages is not None:
        # Convert InactivityMessage objects to dicts for API
        inactivity_messages = []
        for msg in agent_data.inactivityMessages:
            if hasattr(msg, 'dict'):
                # Pydantic model - convert to dict
                msg_dict = msg.dict()
                # Ensure endBehavior is a string (enum value)
                if 'endBehavior' in msg_dict and hasattr(msg_dict['endBehavior'], 'value'):
                    msg_dict['endBehavior'] = msg_dict['endBehavior'].value
                inactivity_messages.append(msg_dict)
            else:
                # Already a dict
                inactivity_messages.append(msg)
        call_template["inactivityMessages"] = inactivity_messages
    
    # Build selectedTools if tools are provided
    if agent_data.tools is not None:
        selected_tools = []
        for tool in agent_data.tools:
            tool_dict = tool.dict() if hasattr(tool, 'dict') else tool
            selected_tools.append({
                "toolId": tool_dict.get("tool_id"),
                "toolName": tool_dict.get("tool_name", tool_dict.get("tool_id")),
            })
        call_template["selectedTools"] = selected_tools
    
    return call_template


def build_call_template_from_agent_dict(
    agent: dict,
    voice: dict,
    corpus_ids: list,
    mapped_language: str = "en-US",
) -> dict:
    """
    Build callTemplate structure from agent dict (for sync operations).
    Uses defaults for fields not in the database.
    """
    # Build externalVoice object based on provider
    external_voice = {}
    provider = voice.get("provider", "elevenlabs")
    voice_id = voice.get("ultravox_voice_id")
    
    if provider == "elevenlabs":
        elevenlabs_voice = {
            "voiceId": voice_id,
        }
        # Use agent data if available, otherwise defaults
        if agent.get("stability") is not None:
            elevenlabs_voice["stability"] = agent.get("stability")
        else:
            elevenlabs_voice["stability"] = 0.75  # Default
        if agent.get("similarity") is not None:
            elevenlabs_voice["similarityBoost"] = agent.get("similarity")
        else:
            elevenlabs_voice["similarityBoost"] = 0.75  # Default
        if agent.get("voiceStyle") is not None:
            elevenlabs_voice["style"] = agent.get("voiceStyle")
        if agent.get("useSpeakerBoost") is not None:
            elevenlabs_voice["useSpeakerBoost"] = agent.get("useSpeakerBoost")
        if agent.get("speed") is not None:
            elevenlabs_voice["speed"] = agent.get("speed")
        if agent.get("voicePitch") is not None:
            elevenlabs_voice["pitch"] = agent.get("voicePitch")
        if agent.get("elevenLabsModel"):
            elevenlabs_voice["model"] = agent.get("elevenLabsModel")
        if agent.get("pronunciationDictionaries"):
            elevenlabs_voice["pronunciationDictionaries"] = agent.get("pronunciationDictionaries")
        if agent.get("optimizeStreamingLatency") is not None:
            elevenlabs_voice["optimizeStreamingLatency"] = agent.get("optimizeStreamingLatency")
        if agent.get("maxSampleRate") is not None:
            elevenlabs_voice["maxSampleRate"] = agent.get("maxSampleRate")
        external_voice["elevenLabs"] = elevenlabs_voice
    elif provider == "openai":
        openai_voice = {
            "voiceId": voice_id,
        }
        if agent.get("speed") is not None:
            openai_voice["speed"] = agent.get("speed")
        if agent.get("voicePitch") is not None:
            openai_voice["pitch"] = agent.get("voicePitch")
        external_voice["openai"] = openai_voice
    elif provider == "google":
        google_voice = {
            "voiceId": voice_id,
        }
        if agent.get("speed") is not None:
            google_voice["speakingRate"] = agent.get("speed")
        # Note: Google voice does not support pitch according to API docs
        external_voice["google"] = google_voice
    elif provider == "cartesia":
        cartesia_voice = {
            "voiceId": voice_id,
        }
        if agent.get("cartesiaModel"):
            cartesia_voice["model"] = agent.get("cartesiaModel")
        if agent.get("speed") is not None:
            cartesia_voice["speed"] = agent.get("speed")
        if agent.get("cartesiaEmotion"):
            cartesia_voice["emotion"] = agent.get("cartesiaEmotion")
        if agent.get("cartesiaEmotions"):
            cartesia_voice["emotions"] = agent.get("cartesiaEmotions")
        if agent.get("cartesiaGenerationConfig"):
            cartesia_voice["generationConfig"] = agent.get("cartesiaGenerationConfig")
        external_voice["cartesia"] = cartesia_voice
    elif provider == "lmnt":
        lmnt_voice = {
            "voiceId": voice_id,
        }
        if agent.get("lmntModel"):
            lmnt_voice["model"] = agent.get("lmntModel")
        if agent.get("speed") is not None:
            lmnt_voice["speed"] = agent.get("speed")
        if agent.get("lmntConversational") is not None:
            lmnt_voice["conversational"] = agent.get("lmntConversational")
        external_voice["lmnt"] = lmnt_voice
    elif provider == "generic":
        generic_voice = {}
        if agent.get("genericUrl"):
            generic_voice["url"] = agent.get("genericUrl")
        elif voice_id:
            generic_voice["url"] = voice_id
        if agent.get("genericHeaders"):
            generic_voice["headers"] = agent.get("genericHeaders")
        if agent.get("genericBody"):
            generic_voice["body"] = agent.get("genericBody")
        if agent.get("genericResponseSampleRate") is not None:
            generic_voice["responseSampleRate"] = agent.get("genericResponseSampleRate")
        if agent.get("genericResponseWordsPerMinute") is not None:
            generic_voice["responseWordsPerMinute"] = agent.get("genericResponseWordsPerMinute")
        if agent.get("genericResponseMimeType"):
            generic_voice["responseMimeType"] = agent.get("genericResponseMimeType")
        if agent.get("genericJsonAudioFieldPath"):
            generic_voice["jsonAudioFieldPath"] = agent.get("genericJsonAudioFieldPath")
        if agent.get("genericJsonByteEncoding"):
            generic_voice["jsonByteEncoding"] = agent.get("genericJsonByteEncoding")
        if generic_voice:
            external_voice["generic"] = generic_voice
    else:
        external_voice = None
    
    # Build callTemplate object with defaults
    call_template = {
        "systemPrompt": agent.get("system_prompt", ""),
        "temperature": agent.get("temperature", 0.7),
        "model": agent.get("selectedLLM") or agent.get("model", "fixie-ai/ultravox-v0_4-8k"),
    }
    
    # Add voice
    if external_voice:
        call_template["externalVoice"] = external_voice
    elif voice_id:
        call_template["voice"] = voice_id
    
    # Add languageHint
    call_template["languageHint"] = mapped_language
    
    # Add timeExceededMessage if available
    if agent.get("timeExceededMessage"):
        call_template["timeExceededMessage"] = agent.get("timeExceededMessage")
    
    # Add joinTimeout
    if agent.get("joinTimeout") is not None:
        call_template["joinTimeout"] = f"{agent.get('joinTimeout')}s"
    else:
        call_template["joinTimeout"] = "30s"
    
    # Add maxDuration
    if agent.get("maxConversationDuration") is not None:
        call_template["maxDuration"] = f"{agent.get('maxConversationDuration')}s"
    else:
        call_template["maxDuration"] = "3600s"
    
    # Add VAD settings if available
    vad_settings = {}
    if agent.get("turnEndpointDelay") is not None:
        vad_settings["turnEndpointDelay"] = f"{agent.get('turnEndpointDelay')}ms"
    if agent.get("minimumTurnDuration") is not None:
        vad_settings["minimumTurnDuration"] = f"{agent.get('minimumTurnDuration')}ms"
    if agent.get("minimumInterruptionDuration") is not None:
        vad_settings["minimumInterruptionDuration"] = f"{agent.get('minimumInterruptionDuration')}ms"
    if agent.get("frameActivationThreshold") is not None:
        vad_settings["frameActivationThreshold"] = agent.get("frameActivationThreshold")
    if vad_settings:
        call_template["vadSettings"] = vad_settings
    
    # Build firstSpeakerSettings if available
    first_speaker_settings = {}
    if agent.get("firstSpeaker"):
        if agent.get("firstSpeaker").lower() == "user":
            first_speaker_settings["user"] = {}
            user_fallback = {}
            if agent.get("firstMessageDelay") is not None:
                user_fallback["delay"] = f"{agent.get('firstMessageDelay')}s"
            if agent.get("userFallbackText"):
                user_fallback["text"] = agent.get("userFallbackText")
            if agent.get("userFallbackPrompt"):
                user_fallback["prompt"] = agent.get("userFallbackPrompt")
            if user_fallback:
                first_speaker_settings["user"]["fallback"] = user_fallback
        elif agent.get("firstSpeaker").lower() == "agent":
            first_speaker_settings["agent"] = {}
            if agent.get("disableInterruptions") is not None:
                first_speaker_settings["agent"]["uninterruptible"] = agent.get("disableInterruptions")
            if agent.get("firstMessage"):
                first_speaker_settings["agent"]["text"] = agent.get("firstMessage")
            if agent.get("agentPrompt"):
                first_speaker_settings["agent"]["prompt"] = agent.get("agentPrompt")
            if agent.get("firstMessageDelay") is not None:
                first_speaker_settings["agent"]["delay"] = f"{agent.get('firstMessageDelay')}s"
    if first_speaker_settings:
        call_template["firstSpeakerSettings"] = first_speaker_settings
    
    # Add recordingEnabled
    call_template["recordingEnabled"] = agent.get("recordingEnabled", True)
    
    # Add initialOutputMedium
    call_template["initialOutputMedium"] = agent.get("initialOutputMedium", "MESSAGE_MEDIUM_VOICE")
    
    # Add inactivityMessages if available
    if agent.get("inactivityMessages"):
        call_template["inactivityMessages"] = agent.get("inactivityMessages")
    
    # Add selectedTools from tools
    if agent.get("tools"):
        selected_tools = []
        for tool in agent.get("tools", []):
            tool_dict = tool if isinstance(tool, dict) else tool.dict() if hasattr(tool, 'dict') else {}
            selected_tools.append({
                "toolId": tool_dict.get("tool_id") or tool_dict.get("id"),
                "toolName": tool_dict.get("tool_name") or tool_dict.get("name") or tool_dict.get("tool_id") or tool_dict.get("id"),
            })
        if selected_tools:
            call_template["selectedTools"] = selected_tools
    # Note: KB tools are added by the caller after this function returns
    
    return call_template


@router.post("")
async def create_agent(
    agent_data: AgentCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """Create agent"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    # Check idempotency key
    body_dict = agent_data.dict() if hasattr(agent_data, 'dict') else json.loads(json.dumps(agent_data, default=str))
    if idempotency_key:
        cached = await check_idempotency_key(
            current_user["client_id"],
            idempotency_key,
            request,
            body_dict,
        )
        if cached:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                content=cached["response_body"],
                status_code=cached["status_code"],
            )
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Normalize voice_id: convert empty string to None
    voice_id = agent_data.voice_id if agent_data.voice_id and agent_data.voice_id.strip() else None
    
    # Check for duplicate agent (same name + voice_id for same client)
    # Only check if voice_id is provided
    if voice_id:
        existing_agents = db.select(
            "agents",
            {
                "client_id": current_user["client_id"],
                "name": agent_data.name,
                "voice_id": voice_id,
            }
        )
        if existing_agents and len(existing_agents) > 0:
            # Return existing agent instead of creating duplicate
            existing_agent = existing_agents[0]
            logger.info(f"Agent with name '{agent_data.name}' and voice_id '{voice_id}' already exists: {existing_agent['id']}")
            return {
                "data": AgentResponse(**existing_agent),
                "meta": ResponseMeta(
                    request_id=str(uuid.uuid4()),
                    ts=datetime.utcnow(),
                ),
            }
    else:
        # Check for duplicate agent by name only if no voice_id
        existing_agents = db.select(
            "agents",
            {
                "client_id": current_user["client_id"],
                "name": agent_data.name,
            }
        )
        # Filter to only agents without voice_id
        existing_agents = [a for a in existing_agents if not a.get("voice_id")]
        if existing_agents and len(existing_agents) > 0:
            existing_agent = existing_agents[0]
            logger.info(f"Agent with name '{agent_data.name}' (no voice) already exists: {existing_agent['id']}")
            return {
                "data": AgentResponse(**existing_agent),
                "meta": ResponseMeta(
                    request_id=str(uuid.uuid4()),
                    ts=datetime.utcnow(),
                ),
            }
    
    # Validate voice only if voice_id is provided
    voice = None
    if voice_id:
        voice = db.get_voice(voice_id, current_user["client_id"])
        if not voice:
            raise NotFoundError("voice", voice_id)
        if voice.get("status") != "active":
            raise ValidationError("Voice must be active", {"voice_id": voice_id, "voice_status": voice.get("status")})
    
    # If voice doesn't have ultravox_voice_id, try to create it in Ultravox (for external voices)
    # This is optional - agent can be created without Ultravox integration
    # Only process if voice is provided
    if voice and not voice.get("ultravox_voice_id"):
        from app.core.config import settings
        if settings.ULTRAVOX_API_KEY:
            # Try to create the voice in Ultravox
            try:
                ultravox_voice_data = {
                    "name": voice.get("name"),
                    "provider": voice.get("provider", "elevenlabs"),
                    "type": "reference",  # External voices are reference type
                }
                # Include provider_voice_id if available (ElevenLabs voice ID)
                if voice.get("provider_voice_id"):
                    ultravox_voice_data["provider_voice_id"] = voice.get("provider_voice_id")
                
                ultravox_response = await ultravox_client.create_voice(ultravox_voice_data)
                if ultravox_response and ultravox_response.get("id"):
                    # Update voice with Ultravox ID
                    db.update(
                        "voices",
                        {"id": voice_id},
                        {"ultravox_voice_id": ultravox_response.get("id")},
                    )
                    voice["ultravox_voice_id"] = ultravox_response.get("id")
                else:
                    logger.warning(f"Failed to sync voice {voice_id} with Ultravox - response missing ID")
            except Exception as e:
                # Log error but don't fail - agent can be created without Ultravox
                logger.warning(f"Failed to create voice in Ultravox for agent (non-critical): {e}")
        else:
            logger.info("Ultravox API key not configured. Agent will be created without Ultravox integration.")
    
    # Validate knowledge bases
    if agent_data.knowledge_bases:
        for kb_id in agent_data.knowledge_bases:
            kb = db.get_knowledge_base(kb_id, current_user["client_id"])
            if not kb:
                raise NotFoundError("knowledge_base", kb_id)
            if kb.get("status") != "ready":
                raise ValidationError("Knowledge base must be ready", {"kb_id": kb_id, "kb_status": kb.get("status")})
    
    # Validate tools - check if they are verified
    if agent_data.tools:
        unverified_tools = []
        for tool in agent_data.tools:
            tool_dict = tool.dict() if hasattr(tool, 'dict') else tool
            tool_id = tool_dict.get("tool_id")
            if tool_id:
                tool_record = db.select_one("tools", {"id": tool_id, "client_id": current_user["client_id"]})
                if not tool_record:
                    raise NotFoundError("tool", tool_id)
                if not tool_record.get("is_verified", False):
                    unverified_tools.append(tool_record.get("name", tool_id))
        
        if unverified_tools:
            raise ValidationError(
                f"Tools must be verified before attaching to agents. Unverified tools: {', '.join(unverified_tools)}",
                {"unverified_tools": unverified_tools}
            )
    
    # ATOMIC RESOURCE CREATION (Saga Pattern)
    # Step 1: Insert record with status='creating' (temporary state)
    agent_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    # Use selectedLLM if provided, otherwise use model field
    model_value = agent_data.selectedLLM if agent_data.selectedLLM is not None else agent_data.model
    agent_db_record = {
        "id": agent_id,
        "client_id": current_user["client_id"],
        "name": agent_data.name,
        "description": agent_data.description,
        "system_prompt": agent_data.system_prompt,
        "model": model_value,
        "tools": [tool.dict() for tool in agent_data.tools] if agent_data.tools else [],
        "knowledge_bases": agent_data.knowledge_bases or [],
        "status": "creating",  # Temporary status - will be updated after Ultravox call
        "success_criteria": agent_data.success_criteria,
        "extraction_schema": agent_data.extraction_schema or {},
        "crm_webhook_url": agent_data.crm_webhook_url,
        "crm_webhook_secret": agent_data.crm_webhook_secret,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    
    # Only include voice_id if it's provided (not None)
    if voice_id:
        agent_db_record["voice_id"] = voice_id
    
    # Insert temporary record
    db.insert("agents", agent_db_record)
    
    # Step 2: Call Ultravox API (if voice is provided and has ultravox_voice_id)
    ultravox_agent_id = None
    provider_error_details = None
    
    try:
        if voice and voice.get("ultravox_voice_id"):
            from app.core.config import settings
            if settings.ULTRAVOX_API_KEY:
                # Get knowledge base corpus IDs
                corpus_ids = []
                if agent_data.knowledge_bases:
                    for kb_id in agent_data.knowledge_bases:
                        kb = db.get_knowledge_base(kb_id, current_user["client_id"])
                        if kb and kb.get("ultravox_corpus_id"):
                            corpus_ids.append(kb["ultravox_corpus_id"])
                
                # Map agentLanguage to BCP47 code
                language_map = {
                    "english": "en-US",
                    "spanish": "es-ES",
                    "french": "fr-FR"
                }
                mapped_language = None
                if agent_data.agentLanguage:
                    mapped_language = language_map.get(agent_data.agentLanguage.lower(), "en-US")
                elif voice.get("language"):
                    mapped_language = voice.get("language")
                else:
                    mapped_language = "en-US"
                
                # Build callTemplate using helper function
                call_template = build_call_template_from_agent_data(
                    agent_data=agent_data,
                    voice=voice,
                    corpus_ids=corpus_ids,
                    mapped_language=mapped_language,
                )
                
                # Auto-load Knowledge Base tools if agent has linked KBs
                if agent_data.knowledge_bases:
                    kb_tools_added = []
                    kb_names = []
                    for kb_id in agent_data.knowledge_bases:
                        kb = db.get_knowledge_base(kb_id, current_user["client_id"])
                        if kb and kb.get("ultravox_tool_id"):
                            # Add KB tool to selectedTools
                            if "selectedTools" not in call_template:
                                call_template["selectedTools"] = []
                            call_template["selectedTools"].append({
                                "toolId": kb["ultravox_tool_id"],
                                "toolName": f"search_kb_{kb_id}",
                            })
                            kb_tools_added.append(kb_id)
                            kb_names.append(kb.get("name", kb_id))
                    
                    # Update systemPrompt to mention KB access
                    if kb_tools_added:
                        kb_mention = f"You have access to {len(kb_tools_added)} knowledge base(s): {', '.join(kb_names)}. Use the search_kb_* tool(s) to find relevant information and answer questions accurately."
                        original_prompt = call_template.get("systemPrompt", "")
                        if kb_mention not in original_prompt:
                            call_template["systemPrompt"] = f"{original_prompt}\n\n{kb_mention}".strip()
                        
                        # Add authTokens for KB tool authentication
                        from app.core.config import settings
                        if settings.ULTRAVOX_TOOL_SECRET:
                            call_template["authTokens"] = {
                                "toolSecret": settings.ULTRAVOX_TOOL_SECRET
                            }
                
                # Build ultravox_data with new callTemplate structure
                ultravox_data = {
                    "name": agent_data.name,
                    "callTemplate": call_template,
                }
                
                # Add description if provided (at top level)
                if agent_data.description:
                    ultravox_data["description"] = agent_data.description
                
                logger.info(f"Creating agent in Ultravox for agent {agent_id} with data: {ultravox_data}")
                ultravox_response = await ultravox_client.create_agent(ultravox_data)
                logger.info(f"Ultravox response for agent {agent_id}: {ultravox_response}")
                
                if ultravox_response and ultravox_response.get("id"):
                    ultravox_agent_id = ultravox_response.get("id")
                    logger.info(f"Successfully created agent in Ultravox. Agent ID: {agent_id}, Ultravox Agent ID: {ultravox_agent_id}")
                else:
                    raise ValueError("Ultravox response missing agent ID")
        
        # Step 3: Update record to 'active' with ultravox_id (success path)
        update_data = {
            "status": "active",
            "updated_at": now.isoformat(),
        }
        if ultravox_agent_id:
            update_data["ultravox_agent_id"] = ultravox_agent_id
        
        db.update("agents", {"id": agent_id}, update_data)
        agent_db_record.update(update_data)
        
    except Exception as e:
        # Step 4: Rollback - delete the temporary record and return error
        logger.error(f"Failed to create agent in Ultravox: {e}", exc_info=True)
        
        # Rollback: Delete the temporary record
        db.delete("agents", {"id": agent_id, "client_id": current_user["client_id"]})
        
        # Re-raise ProviderError as-is, otherwise wrap in ProviderError
        if isinstance(e, ProviderError):
            raise
        else:
            error_msg = str(e)
            raise ProviderError(
                provider="ultravox",
                message=f"Failed to create agent in Ultravox: {error_msg}",
                http_status=500,
                details={"error": error_msg},
            )
    
    # Prepare agent record for response (use datetime objects for Pydantic)
    agent_record = agent_db_record.copy()
    agent_record["created_at"] = now
    agent_record["updated_at"] = now
    if ultravox_agent_id:
        agent_record["ultravox_agent_id"] = ultravox_agent_id
    
    response_data = {
        "data": AgentResponse(**agent_record),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }
    
    # Emit event
    await emit_agent_created(
        agent_id=agent_id,
        client_id=current_user["client_id"],
        ultravox_agent_id=agent_record.get("ultravox_agent_id"),
    )
    
    # Store idempotency response
    if idempotency_key:
        await store_idempotency_response(
            current_user["client_id"],
            idempotency_key,
            request,
            body_dict,
            response_data,
            201,
        )
    
    return response_data


@router.patch("/{agent_id}")
async def update_agent(
    agent_id: str,
    agent_data: AgentUpdate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update agent"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    agent = db.get_agent(agent_id, current_user["client_id"])
    if not agent:
        raise NotFoundError("agent", agent_id)
    
    # Validate voice if changed, or get current voice if voice settings are being updated
    voice = None
    voice_changed = False
    
    if agent_data.voice_id:
        # Voice ID is being changed
        voice = db.get_voice(agent_data.voice_id, current_user["client_id"])
        if not voice or voice.get("status") != "active":
            raise ValidationError("Voice must be active")
        voice_changed = True
        
        # If voice doesn't have ultravox_voice_id, try to create it in Ultravox (for external voices)
        if not voice.get("ultravox_voice_id"):
            from app.core.config import settings
            if settings.ULTRAVOX_API_KEY:
                try:
                    ultravox_voice_data = {
                        "name": voice.get("name"),
                        "provider": voice.get("provider", "elevenlabs"),
                        "type": "reference",
                    }
                    if voice.get("provider_voice_id"):
                        ultravox_voice_data["provider_voice_id"] = voice.get("provider_voice_id")
                    
                    ultravox_response = await ultravox_client.create_voice(ultravox_voice_data)
                    if ultravox_response and ultravox_response.get("id"):
                        db.update(
                            "voices",
                            {"id": agent_data.voice_id},
                            {"ultravox_voice_id": ultravox_response.get("id")},
                        )
                        voice["ultravox_voice_id"] = ultravox_response.get("id")
                except Exception as e:
                    logger.warning(f"Failed to sync voice {agent_data.voice_id} with Ultravox during agent update (non-critical): {e}")
    elif agent.get("voice_id"):
        # Voice ID not changed, but we might need current voice for voice settings update
        # Check if voice settings are being updated
        if (agent_data.stability is not None or 
            agent_data.similarity is not None or 
            agent_data.speed is not None or
            agent_data.voicePitch is not None or
            agent_data.voiceStyle is not None or
            agent_data.useSpeakerBoost is not None):
            # Voice settings are being updated, get current voice
            voice = db.get_voice(agent.get("voice_id"), current_user["client_id"])
    
    # Update local database - only include fields that are stored in database
    # Filter out Ultravox-specific fields that are only sent to Ultravox API
    update_data = agent_data.dict(exclude_unset=True)
    # Fields that should NOT be stored in database (only sent to Ultravox)
    ultravox_only_fields = {
        'agentLanguage', 'firstMessage', 'disableInterruptions', 'selectedLLM',
        'temperature', 'stability', 'similarity', 'speed', 'turnTimeout',
        'maxConversationDuration', 'firstMessageDelay', 'firstSpeaker',
        'joinTimeout', 'timeExceededMessage', 'turnEndpointDelay',
        'minimumTurnDuration', 'minimumInterruptionDuration', 'frameActivationThreshold',
        'confidenceThreshold', 'fallbackResponse', 'timezone', 'personality',
        'voicePitch', 'voiceStyle', 'useSpeakerBoost', 'knowledgeBaseSearchEnabled',
        'knowledgeBaseContextWindow'
    }
    # Filter out Ultravox-only fields from database update
    db_update_data = {k: v for k, v in update_data.items() if k not in ultravox_only_fields}
    # Note: selectedLLM maps to 'model' field in database, so handle it separately
    if 'selectedLLM' in update_data and update_data['selectedLLM'] is not None:
        db_update_data['model'] = update_data['selectedLLM']
    
    if db_update_data:
        db.update("agents", {"id": agent_id}, db_update_data)
    
    # Update Ultravox - transform update_data to Ultravox API format
    if agent.get("ultravox_agent_id"):
        try:
            from app.core.config import settings
            if settings.ULTRAVOX_API_KEY:
                # Get knowledge base corpus IDs if knowledge_bases are being updated
                update_corpus_ids = None
                if agent_data.knowledge_bases is not None:
                    update_corpus_ids = []
                    for kb_id in agent_data.knowledge_bases:
                        kb = db.get_knowledge_base(kb_id, current_user["client_id"])
                        if kb and kb.get("ultravox_corpus_id"):
                            update_corpus_ids.append(kb["ultravox_corpus_id"])
                elif agent_data.knowledgeBaseSearchEnabled is not None or agent_data.knowledgeBaseContextWindow is not None:
                    # Knowledge base settings are being updated, get existing corpus IDs
                    if agent.get("knowledge_bases"):
                        update_corpus_ids = []
                        for kb_id in agent["knowledge_bases"]:
                            kb = db.get_knowledge_base(kb_id, current_user["client_id"])
                            if kb and kb.get("ultravox_corpus_id"):
                                update_corpus_ids.append(kb["ultravox_corpus_id"])
                
                # Map agentLanguage to BCP47 code if provided
                update_mapped_language = None
                if agent_data.agentLanguage is not None:
                    language_map = {
                        "english": "en-US",
                        "spanish": "es-ES",
                        "french": "fr-FR"
                    }
                    update_mapped_language = language_map.get(agent_data.agentLanguage.lower(), "en-US")
                elif voice and voice.get("language"):
                    update_mapped_language = voice.get("language")
                
                # Build partial callTemplate using helper function
                call_template = build_partial_call_template_from_update(
                    agent_data=agent_data,
                    voice=voice if (voice_changed or (voice and (
                        agent_data.stability is not None or
                        agent_data.similarity is not None or
                        agent_data.speed is not None or
                        agent_data.voicePitch is not None or
                        agent_data.voiceStyle is not None or
                        agent_data.useSpeakerBoost is not None
                    ))) else None,
                    corpus_ids=update_corpus_ids,
                    mapped_language=update_mapped_language,
                )
                
                # Build Ultravox update payload with new callTemplate structure
                ultravox_update = {}
                
                # Map basic fields at top level
                if agent_data.name is not None:
                    ultravox_update["name"] = agent_data.name
                if agent_data.description is not None:
                    ultravox_update["description"] = agent_data.description
                
                # Add callTemplate if any fields are being updated
                if call_template:
                    ultravox_update["callTemplate"] = call_template
                
                # Note: Knowledge base handling in new API might be different
                # For now, we'll handle it in callTemplate if needed
                # Tools are now selectedTools in callTemplate, which is handled by the helper
                
                # Only update if there's something to update
                if ultravox_update:
                    await ultravox_client.update_agent(agent["ultravox_agent_id"], ultravox_update)
        except Exception as e:
            # Log error but don't fail the request
            logger.warning(f"Failed to update agent in Ultravox (non-critical): {e}")
    
    # Get updated agent
    updated_agent = db.get_agent(agent_id, current_user["client_id"])
    
    # Emit event
    await emit_agent_updated(
        agent_id=agent_id,
        client_id=current_user["client_id"],
        changes=update_data,
    )
    
    return {
        "data": AgentResponse(**updated_agent),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("")
async def list_agents(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    List agents - returns what is in the DB immediately.
    Use /agents/{agent_id}/sync or /agents/sync-all for status reconciliation.
    """
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Get agents from database - return immediately without polling
    agents = db.select("agents", {"client_id": current_user["client_id"]}, "created_at")
    logger.info(f"Found {len(agents)} agents in database for client_id {current_user['client_id']}")
    
    # Convert agents to response format, handling any validation errors
    agent_responses = []
    for agent in agents:
        try:
            agent_responses.append(AgentResponse(**agent))
        except Exception as e:
            logger.error(f"Failed to create AgentResponse for agent {agent.get('id')}: {e}")
            logger.error(f"Agent data: {agent}")
            # Skip invalid agents but continue processing others
            continue
    
    logger.info(f"Returning {len(agent_responses)} agents out of {len(agents)} total for client_id {current_user['client_id']}")
    
    return {
        "data": agent_responses,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/{agent_id}/sync")
async def sync_agent_with_ultravox(
    agent_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Sync agent with Ultravox - creates agent in Ultravox if not already created"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    agent = db.get_agent(agent_id, current_user["client_id"])
    if not agent:
        raise NotFoundError("agent", agent_id)
    
    # If agent already has ultravox_agent_id, return success
    if agent.get("ultravox_agent_id"):
        return {
            "data": AgentResponse(**agent),
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
            "message": "Agent already synced with Ultravox",
        }
    
    # Get voice
    voice = db.get_voice(agent["voice_id"], current_user["client_id"])
    if not voice:
        raise NotFoundError("voice", agent["voice_id"])
    
    if voice.get("status") != "active":
        raise ValidationError("Voice must be active", {"voice_id": agent["voice_id"], "voice_status": voice.get("status")})
    
    # If voice doesn't have ultravox_voice_id, try to create it first
    if not voice.get("ultravox_voice_id"):
        from app.core.config import settings
        if settings.ULTRAVOX_API_KEY:
            try:
                ultravox_voice_data = {
                    "name": voice.get("name"),
                    "provider": voice.get("provider", "elevenlabs"),
                    "type": "reference",
                }
                if voice.get("provider_voice_id"):
                    ultravox_voice_data["provider_voice_id"] = voice.get("provider_voice_id")
                
                ultravox_response = await ultravox_client.create_voice(ultravox_voice_data)
                if ultravox_response and ultravox_response.get("id"):
                    db.update(
                        "voices",
                        {"id": agent["voice_id"]},
                        {"ultravox_voice_id": ultravox_response.get("id")},
                    )
                    voice["ultravox_voice_id"] = ultravox_response.get("id")
            except Exception as e:
                logger.error(f"Failed to create voice in Ultravox during sync: {e}", exc_info=True)
                error_msg = str(e)
                if "404" in error_msg:
                    error_msg = "Ultravox API endpoint not found. Please check ULTRAVOX_BASE_URL and ULTRAVOX_API_KEY configuration."
                elif "401" in error_msg or "403" in error_msg:
                    error_msg = "Ultravox API authentication failed. Please check your ULTRAVOX_API_KEY."
                elif "Ultravox API key is not configured" in error_msg:
                    error_msg = "Ultravox API key is not configured. Please set ULTRAVOX_API_KEY environment variable."
                raise ValidationError(f"Failed to sync voice with Ultravox: {error_msg}", {"error": str(e)})
        else:
            raise ValidationError("Ultravox API key not configured")
    
    # Now create agent in Ultravox
    from app.core.config import settings
    if not settings.ULTRAVOX_API_KEY:
        raise ValidationError("Ultravox API key not configured")
    
    try:
        # Get knowledge base corpus IDs
        corpus_ids = []
        if agent.get("knowledge_bases"):
            for kb_id in agent["knowledge_bases"]:
                kb = db.get_knowledge_base(kb_id, current_user["client_id"])
                if kb and kb.get("ultravox_corpus_id"):
                    corpus_ids.append(kb["ultravox_corpus_id"])
        
        # Map language
        mapped_language = voice.get("language", "en-US")
        
        # Build callTemplate from agent dict
        call_template = build_call_template_from_agent_dict(
            agent=agent,
            voice=voice,
            corpus_ids=corpus_ids,
            mapped_language=mapped_language,
        )
        
        # Auto-load Knowledge Base tools if agent has linked KBs
        if agent.get("knowledge_bases"):
            kb_tools_added = []
            kb_names = []
            for kb_id in agent["knowledge_bases"]:
                kb = db.get_knowledge_base(kb_id, current_user["client_id"])
                if kb and kb.get("ultravox_tool_id"):
                    # Add KB tool to selectedTools
                    if "selectedTools" not in call_template:
                        call_template["selectedTools"] = []
                    call_template["selectedTools"].append({
                        "toolId": kb["ultravox_tool_id"],
                        "toolName": f"search_kb_{kb_id}",
                    })
                    kb_tools_added.append(kb_id)
                    kb_names.append(kb.get("name", kb_id))
            
            # Update systemPrompt to mention KB access
            if kb_tools_added:
                kb_mention = f"You have access to {len(kb_tools_added)} knowledge base(s): {', '.join(kb_names)}. Use the search_kb_* tool(s) to find relevant information and answer questions accurately."
                original_prompt = call_template.get("systemPrompt", "")
                if kb_mention not in original_prompt:
                    call_template["systemPrompt"] = f"{original_prompt}\n\n{kb_mention}".strip()
                
                # Add authTokens for KB tool authentication
                if settings.ULTRAVOX_TOOL_SECRET:
                    call_template["authTokens"] = {
                        "toolSecret": settings.ULTRAVOX_TOOL_SECRET
                    }
        
        # Build ultravox_data with new callTemplate structure
        ultravox_data = {
            "name": agent["name"],
            "callTemplate": call_template,
        }
        
        # Add description if available
        if agent.get("description"):
            ultravox_data["description"] = agent["description"]
        
        ultravox_response = await ultravox_client.create_agent(ultravox_data)
        
        if ultravox_response and ultravox_response.get("id"):
            ultravox_agent_id = ultravox_response.get("id")
            # Update agent with Ultravox ID
            db.update(
                "agents",
                {"id": agent_id},
                {
                    "ultravox_agent_id": ultravox_agent_id,
                    "status": "active",
                    "updated_at": datetime.utcnow().isoformat(),
                },
            )
            agent["ultravox_agent_id"] = ultravox_agent_id
            agent["status"] = "active"
            
            return {
                "data": AgentResponse(**agent),
                "meta": ResponseMeta(
                    request_id=str(uuid.uuid4()),
                    ts=datetime.utcnow(),
                ),
                "message": "Agent successfully synced with Ultravox",
            }
        else:
            raise ValidationError("Failed to create agent in Ultravox - response missing ID")
    except Exception as e:
        logger.error(f"Failed to sync agent {agent_id} with Ultravox: {e}", exc_info=True)
        raise ValidationError("Failed to sync agent with Ultravox", {"error": str(e)})


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    refresh: bool = False,
):
    """Get single agent with optional Ultravox sync"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    agent = db.get_agent(agent_id, current_user["client_id"])
    if not agent:
        raise NotFoundError("agent", agent_id)
    
    # Optionally refresh from Ultravox if agent has ultravox_agent_id and refresh flag is set
    if refresh and agent.get("ultravox_agent_id"):
        try:
            from app.core.config import settings
            if settings.ULTRAVOX_API_KEY:
                ultravox_agent = await ultravox_client.get_agent(agent["ultravox_agent_id"])
                
                # Update local database with latest Ultravox data
                update_data = {}
                
                # Sync status if different
                if ultravox_agent.get("status"):
                    ultravox_status = ultravox_agent.get("status", "").lower()
                    if ultravox_status in ["active", "ready", "completed"] and agent.get("status") != "active":
                        update_data["status"] = "active"
                    elif ultravox_status in ["inactive", "deleted", "error"] and agent.get("status") != ultravox_status:
                        update_data["status"] = ultravox_status
                
                # Sync name if changed in Ultravox
                if ultravox_agent.get("name") and ultravox_agent.get("name") != agent.get("name"):
                    update_data["name"] = ultravox_agent.get("name")
                
                # Sync description if available
                if ultravox_agent.get("description") and ultravox_agent.get("description") != agent.get("description"):
                    update_data["description"] = ultravox_agent.get("description")
                
                # Update if there are changes
                if update_data:
                    update_data["updated_at"] = datetime.utcnow().isoformat()
                    db.update("agents", {"id": agent_id}, update_data)
                    # Refresh agent data
                    agent = db.get_agent(agent_id, current_user["client_id"])
        except Exception as e:
            # Log error but don't fail the request
            logger.warning(f"Failed to refresh agent {agent_id} from Ultravox: {e}")
    
    return {
        "data": AgentResponse(**agent),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/bulk")
async def bulk_delete_agents(
    request_data: BulkDeleteRequest,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Bulk delete agents"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    deleted_ids = []
    failed_ids = []
    
    for agent_id in request_data.ids:
        try:
            agent = db.get_agent(agent_id, current_user["client_id"])
            if not agent:
                failed_ids.append(agent_id)
                continue
            
            # Delete from Ultravox if it exists there
            if agent.get("ultravox_agent_id"):
                try:
                    from app.core.config import settings
                    if settings.ULTRAVOX_API_KEY:
                        try:
                            # Delete from Ultravox API
                            await ultravox_client.delete_agent(agent["ultravox_agent_id"], force=False)
                            logger.info(f"Successfully deleted agent {agent_id} from Ultravox (Ultravox ID: {agent.get('ultravox_agent_id')})")
                        except Exception as e:
                            # Log error but continue with local deletion
                            logger.warning(f"Failed to delete agent {agent_id} from Ultravox (non-critical): {e}")
                            logger.info(f"Continuing with local database deletion for agent {agent_id}")
                except Exception as e:
                    logger.warning(f"Error during Ultravox deletion for agent {agent_id}: {e}")
            
            # Delete from database
            db.delete("agents", {"id": agent_id, "client_id": current_user["client_id"]})
            deleted_ids.append(agent_id)
        except Exception as e:
            logger.error(f"Failed to delete agent {agent_id}: {e}")
            failed_ids.append(agent_id)
    
    return {
        "data": BulkDeleteResponse(
            deleted_count=len(deleted_ids),
            failed_count=len(failed_ids),
            deleted_ids=deleted_ids,
            failed_ids=failed_ids,
        ),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/{agent_id}/test")
async def test_agent_webrtc(
    agent_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Create a WebRTC test call for agent testing.
    Returns joinUrl for browser WebRTC session initialization.
    This bypasses PSTN billing and allows testing in the browser.
    
    Optional request body:
    {
        "context": {
            "custom_field": "value"
        }
    }
    """
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Validate agent exists and is active
    agent = db.get_agent(agent_id, current_user["client_id"])
    if not agent:
        raise NotFoundError("agent", agent_id)
    
    if agent.get("status") != "active":
        raise ValidationError("Agent must be active to test", {"agent_status": agent.get("status")})
    
    # Check if agent has ultravox_agent_id
    ultravox_agent_id = agent.get("ultravox_agent_id")
    if not ultravox_agent_id:
        raise ValidationError(
            "Agent must be synced with Ultravox to test. Use /agents/{agent_id}/sync first.",
            {"agent_id": agent_id}
        )
    
    # Check if Ultravox is configured
    from app.core.config import settings
    if not settings.ULTRAVOX_API_KEY:
        raise ValidationError("Ultravox API key is not configured")
    
    try:
        # Parse request body for context (optional)
        context = {}
        try:
            if request.method == "POST":
                body = await request.json()
                context = body.get("context", {})
        except (json.JSONDecodeError, ValueError):
            # No body or invalid JSON - use empty context
            pass
        
        # Create WebRTC test call via Ultravox
        test_call_data = await ultravox_client.create_test_call(
            agent_id=ultravox_agent_id,
            context={
                **context,
                "is_test": True,
                "client_id": current_user["client_id"],
                "agent_id": agent_id,
            },
        )
        
        # Extract joinUrl from response
        join_url = test_call_data.get("joinUrl") or test_call_data.get("join_url")
        if not join_url:
            raise ValidationError("Ultravox did not return a joinUrl for WebRTC test call")
        
        # Generate session ID for tool logging
        session_id = f"test_{agent_id}_{uuid.uuid4().hex[:8]}"
        
        return {
            "data": {
                "agent_id": agent_id,
                "ultravox_agent_id": ultravox_agent_id,
                "call_id": test_call_data.get("id"),
                "joinUrl": join_url,
                "test_mode": True,
                "session_id": session_id,  # For tool logging correlation
            },
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"Failed to create WebRTC test call for agent {agent_id}: {e}", exc_info=True)
        if isinstance(e, (ValidationError, NotFoundError, ForbiddenError)):
            raise
        from app.core.exceptions import ProviderError
        if isinstance(e, ProviderError):
            raise
        raise ValidationError(f"Failed to create test call: {str(e)}", {"error": str(e)})


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete agent"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    agent = db.get_agent(agent_id, current_user["client_id"])
    if not agent:
        raise NotFoundError("agent", agent_id)
    
    # Delete from Ultravox if it exists there
    if agent.get("ultravox_agent_id"):
        try:
            from app.core.config import settings
            if settings.ULTRAVOX_API_KEY:
                try:
                    # Delete from Ultravox API
                    await ultravox_client.delete_agent(agent["ultravox_agent_id"], force=False)
                    logger.info(f"Successfully deleted agent {agent_id} from Ultravox (Ultravox ID: {agent.get('ultravox_agent_id')})")
                except Exception as e:
                    # Log error but continue with local deletion
                    # Ultravox deletion failure shouldn't prevent local deletion
                    logger.warning(f"Failed to delete agent {agent_id} from Ultravox (non-critical): {e}")
                    logger.info(f"Continuing with local database deletion for agent {agent_id}")
        except Exception as e:
            logger.warning(f"Error during Ultravox deletion for agent {agent_id}: {e}")
    
    # Delete from database
    db.delete("agents", {"id": agent_id, "client_id": current_user["client_id"]})
    
    return {
        "data": {"id": agent_id, "deleted": True},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }

