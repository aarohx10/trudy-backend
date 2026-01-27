"""
Agent Service
Modular service for agent operations including Ultravox integration and callTemplate building.
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from app.core.database import DatabaseService
from app.services.ultravox import ultravox_client
from app.core.exceptions import ProviderError

logger = logging.getLogger(__name__)


def build_ultravox_call_template(agent_record: Dict[str, Any], ultravox_voice_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Convert our agent database record to Ultravox callTemplate format.
    
    Args:
        agent_record: Agent record from database
        ultravox_voice_id: Ultravox voice ID (required - will raise error if not provided)
        
    Returns:
        Ultravox callTemplate dictionary
        
    Raises:
        ValueError: If ultravox_voice_id is not provided
    """
    try:
        # Voice is REQUIRED - fail if not provided
        if not ultravox_voice_id:
            raise ValueError("ultravox_voice_id is required for callTemplate. Voice must be synced to Ultravox first.")
        
        # Build base callTemplate with required fields
        system_prompt = agent_record.get("system_prompt", "")
        if not system_prompt or not str(system_prompt).strip():
            raise ValueError("system_prompt is required but is empty or missing")
        
        model = agent_record.get("model", "ultravox-v0.6")
        if not model or not str(model).strip():
            raise ValueError("model is required but is empty or missing")
        
        temperature = agent_record.get("temperature", 0.3)
        try:
            temperature = float(temperature)
            if temperature < 0 or temperature > 1:
                raise ValueError(f"temperature must be between 0 and 1, got {temperature}")
        except (ValueError, TypeError):
            raise ValueError(f"temperature must be a valid float between 0 and 1, got {temperature}")
        
        call_template: Dict[str, Any] = {
            "systemPrompt": str(system_prompt).strip(),
            "model": str(model).strip(),
            "voice": str(ultravox_voice_id).strip(),  # Always set, never empty
            "temperature": temperature,
        }
        
        # Add optional fields
        if agent_record.get("call_template_name"):
            call_template["name"] = agent_record["call_template_name"]
        
        if agent_record.get("language_hint"):
            call_template["languageHint"] = agent_record["language_hint"]
        
        if agent_record.get("time_exceeded_message"):
            call_template["timeExceededMessage"] = agent_record["time_exceeded_message"]
        
        if agent_record.get("recording_enabled") is not None:
            call_template["recordingEnabled"] = agent_record["recording_enabled"]
        
        if agent_record.get("join_timeout"):
            call_template["joinTimeout"] = agent_record["join_timeout"]
        
        if agent_record.get("max_duration"):
            call_template["maxDuration"] = agent_record["max_duration"]
        
        if agent_record.get("initial_output_medium"):
            call_template["initialOutputMedium"] = agent_record["initial_output_medium"]
        
        # Build greeting settings (firstSpeakerSettings)
        greeting_settings = agent_record.get("greeting_settings") or {}
        if greeting_settings:
            first_speaker_settings: Dict[str, Any] = {}
            
            if greeting_settings.get("first_speaker") == "agent":
                agent_settings: Dict[str, Any] = {}
                if greeting_settings.get("text"):
                    agent_settings["text"] = greeting_settings["text"]
                if greeting_settings.get("prompt"):
                    agent_settings["prompt"] = greeting_settings["prompt"]
                if greeting_settings.get("delay"):
                    agent_settings["delay"] = greeting_settings["delay"]
                if greeting_settings.get("uninterruptible") is not None:
                    agent_settings["uninterruptible"] = greeting_settings["uninterruptible"]
                if agent_settings:
                    first_speaker_settings["agent"] = agent_settings
            
            elif greeting_settings.get("first_speaker") == "user":
                user_settings: Dict[str, Any] = {}
                fallback: Dict[str, Any] = {}
                if greeting_settings.get("fallback_delay"):
                    fallback["delay"] = greeting_settings["fallback_delay"]
                if greeting_settings.get("fallback_text"):
                    fallback["text"] = greeting_settings["fallback_text"]
                if greeting_settings.get("fallback_prompt"):
                    fallback["prompt"] = greeting_settings["fallback_prompt"]
                if fallback:
                    user_settings["fallback"] = fallback
                if user_settings:
                    first_speaker_settings["user"] = user_settings
            
            if first_speaker_settings:
                call_template["firstSpeakerSettings"] = first_speaker_settings
        
        # Build inactivity messages
        inactivity_messages = agent_record.get("inactivity_messages") or []
        if inactivity_messages:
            call_template["inactivityMessages"] = [
                {
                    "duration": msg.get("duration", ""),
                    "message": msg.get("message", ""),
                    "endBehavior": msg.get("endBehavior", "END_BEHAVIOR_UNSPECIFIED"),
                }
                for msg in inactivity_messages
            ]
        
        # Build VAD settings
        vad_settings = agent_record.get("vad_settings") or {}
        if vad_settings:
            vad_config: Dict[str, Any] = {}
            if vad_settings.get("turn_endpoint_delay"):
                vad_config["turnEndpointDelay"] = vad_settings["turn_endpoint_delay"]
            if vad_settings.get("minimum_turn_duration"):
                vad_config["minimumTurnDuration"] = vad_settings["minimum_turn_duration"]
            if vad_settings.get("minimum_interruption_duration"):
                vad_config["minimumInterruptionDuration"] = vad_settings["minimum_interruption_duration"]
            if vad_settings.get("frame_activation_threshold") is not None:
                vad_config["frameActivationThreshold"] = float(vad_settings["frame_activation_threshold"])
            if vad_config:
                call_template["vadSettings"] = vad_config
        
        # Build selectedTools (from tools array)
        tools = agent_record.get("tools") or []
        if tools:
            selected_tools = []
            for tool_id in tools:
                # Get tool details from database to get ultravox_tool_id
                db = DatabaseService()
                tool_record = db.select_one("tools", {"id": tool_id, "client_id": agent_record.get("client_id")})
                if tool_record and tool_record.get("ultravox_tool_id"):
                    selected_tools.append({
                        "toolId": tool_record["ultravox_tool_id"],
                    })
            if selected_tools:
                call_template["selectedTools"] = selected_tools
        
        # Build selectedKnowledgeBases (from knowledge_bases array)
        knowledge_bases = agent_record.get("knowledge_bases") or []
        if knowledge_bases:
            # Knowledge bases are referenced via tools, so they should already be included
            # But we can add them explicitly if needed
            pass
        
        # Don't add medium field - let Ultravox use defaults
        # The medium field is optional and Ultravox will default to webRtc
        # Only add it if explicitly needed for specific configurations
        
        return call_template
        
    except Exception as e:
        logger.error(f"[AGENT_SERVICE] Failed to build callTemplate: {e}", exc_info=True)
        raise


async def get_voice_ultravox_id(voice_id: str, client_id: str) -> Optional[str]:
    """
    Get Ultravox voice ID from our voice record.
    
    Args:
        voice_id: Our voice UUID
        client_id: Client UUID
        
    Returns:
        Ultravox voice ID or None
    """
    try:
        db = DatabaseService()
        voice_record = db.select_one("voices", {"id": voice_id, "client_id": client_id})
        if voice_record:
            return voice_record.get("ultravox_voice_id")
        return None
    except Exception as e:
        logger.error(f"[AGENT_SERVICE] Failed to get voice Ultravox ID: {e}", exc_info=True)
        return None


async def validate_agent_for_ultravox_sync(agent_data: Dict[str, Any], client_id: str) -> Dict[str, Any]:
    """
    Validate agent data is ready for Ultravox sync.
    
    Args:
        agent_data: Agent data dictionary
        client_id: Client UUID
        
    Returns:
        {
            "can_sync": bool,
            "reason": str,  # If can_sync is False, explains why
            "errors": List[str]
        }
    """
    errors = []
    
    # Check required fields
    name = agent_data.get("name")
    if not name or not str(name).strip():
        errors.append("Agent name is required")
    
    system_prompt = agent_data.get("system_prompt")
    if not system_prompt or not str(system_prompt).strip():
        errors.append("System prompt is required")
    
    # Check voice
    voice_id = agent_data.get("voice_id")
    if not voice_id:
        errors.append("Voice is required")
        return {"can_sync": False, "reason": "voice_required", "errors": errors}
    
    # Validate voice exists and has Ultravox ID
    ultravox_voice_id = await get_voice_ultravox_id(voice_id, client_id)
    if not ultravox_voice_id:
        errors.append(f"Voice {voice_id} is not synced to Ultravox. Voice must be synced to Ultravox first.")
        return {"can_sync": False, "reason": "voice_not_synced", "errors": errors}
    
    if errors:
        return {"can_sync": False, "reason": "validation_failed", "errors": errors}
    
    return {"can_sync": True, "reason": None, "errors": []}


async def create_agent_in_ultravox(agent_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create agent in Ultravox API.
    
    Args:
        agent_data: Agent data dictionary (from database record)
        
    Returns:
        Ultravox agent response
        
    Raises:
        ValueError: If voice is invalid or missing
        ProviderError: If Ultravox API call fails
    """
    try:
        # Get voice Ultravox ID - REQUIRED
        voice_id = agent_data.get("voice_id")
        client_id = agent_data.get("client_id")
        
        if not voice_id:
            raise ValueError("voice_id is required to create agent in Ultravox")
        if not client_id:
            raise ValueError("client_id is required to create agent in Ultravox")
        
        ultravox_voice_id = await get_voice_ultravox_id(voice_id, client_id)
        if not ultravox_voice_id:
            raise ValueError(f"Voice {voice_id} does not have an Ultravox voice ID. Voice must be synced to Ultravox first.")
        
        # Build callTemplate with validated voice
        call_template = build_ultravox_call_template(agent_data, ultravox_voice_id)
        
        # Create agent in Ultravox
        agent_name = agent_data.get("name", "Untitled Agent")
        response = await ultravox_client.create_agent(agent_name, call_template)
        
        logger.info(f"[AGENT_SERVICE] Created agent in Ultravox: {response.get('agentId')}")
        return response
        
    except ValueError:
        # Re-raise validation errors as-is
        raise
    except Exception as e:
        import traceback
        import json
        error_details = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
            "agent_data": {
                "name": agent_data.get("name"),
                "voice_id": agent_data.get("voice_id"),
                "client_id": agent_data.get("client_id"),
            },
        }
        logger.error(f"[AGENT_SERVICE] Failed to create agent in Ultravox (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to create agent in Ultravox: {str(e)}",
            http_status=500,
        )


async def update_agent_in_ultravox(ultravox_agent_id: str, agent_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update agent in Ultravox API.
    
    Args:
        ultravox_agent_id: Ultravox agent ID
        agent_data: Updated agent data dictionary
        
    Returns:
        Ultravox agent response
        
    Raises:
        ValueError: If voice is invalid or missing
        ProviderError: If Ultravox API call fails
    """
    try:
        # Get voice Ultravox ID - REQUIRED
        voice_id = agent_data.get("voice_id")
        client_id = agent_data.get("client_id")
        
        if not voice_id:
            raise ValueError("voice_id is required to update agent in Ultravox")
        if not client_id:
            raise ValueError("client_id is required to update agent in Ultravox")
        
        ultravox_voice_id = await get_voice_ultravox_id(voice_id, client_id)
        if not ultravox_voice_id:
            raise ValueError(f"Voice {voice_id} does not have an Ultravox voice ID. Voice must be synced to Ultravox first.")
        
        # Build callTemplate with validated voice
        call_template = build_ultravox_call_template(agent_data, ultravox_voice_id)
        
        # Update agent in Ultravox
        agent_name = agent_data.get("name", "Untitled Agent")
        response = await ultravox_client.update_agent(ultravox_agent_id, agent_name, call_template)
        
        logger.info(f"[AGENT_SERVICE] Updated agent in Ultravox: {ultravox_agent_id}")
        return response
        
    except ValueError:
        # Re-raise validation errors as-is
        raise
    except Exception as e:
        import traceback
        import json
        error_details = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
            "ultravox_agent_id": ultravox_agent_id,
            "agent_data": {
                "name": agent_data.get("name"),
                "voice_id": agent_data.get("voice_id"),
                "client_id": agent_data.get("client_id"),
            },
        }
        logger.error(f"[AGENT_SERVICE] Failed to update agent in Ultravox (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to update agent in Ultravox: {str(e)}",
            http_status=500,
        )


async def delete_agent_from_ultravox(ultravox_agent_id: str) -> None:
    """
    Delete agent from Ultravox API.
    
    Args:
        ultravox_agent_id: Ultravox agent ID
    """
    try:
        await ultravox_client.delete_agent(ultravox_agent_id)
        logger.info(f"[AGENT_SERVICE] Deleted agent from Ultravox: {ultravox_agent_id}")
    except Exception as e:
        logger.error(f"[AGENT_SERVICE] Failed to delete agent from Ultravox: {e}", exc_info=True)
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to delete agent from Ultravox: {str(e)}",
            http_status=500,
        )


async def get_agent_from_ultravox(ultravox_agent_id: str) -> Dict[str, Any]:
    """
    Get agent details from Ultravox API.
    
    Args:
        ultravox_agent_id: Ultravox agent ID
        
    Returns:
        Ultravox agent response
    """
    try:
        response = await ultravox_client.get_agent(ultravox_agent_id)
        return response
    except Exception as e:
        logger.error(f"[AGENT_SERVICE] Failed to get agent from Ultravox: {e}", exc_info=True)
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to get agent from Ultravox: {str(e)}",
            http_status=500,
        )


def normalize_agent_name(name: str) -> str:
    """
    Normalize agent name to match Ultravox requirements: ^[a-zA-Z0-9_-]{1,64}$
    - Replace spaces with underscores
    - Remove invalid characters
    - Limit to 64 characters
    """
    import re
    # Replace spaces and special chars with underscores
    normalized = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
    # Limit to 64 characters
    normalized = normalized[:64]
    # Ensure it's not empty
    if not normalized:
        normalized = "untitled_agent"
    return normalized


async def create_agent_ultravox_first(agent_data: Dict[str, Any], client_id: str) -> Dict[str, Any]:
    """
    Create agent in Ultravox FIRST (before database).
    This is the primary source of truth.
    
    Args:
        agent_data: Agent data dictionary (not yet in database)
        client_id: Client UUID
        
    Returns:
        Ultravox agent response (includes agentId)
        
    Raises:
        ValueError: If validation fails
        ProviderError: If Ultravox API call fails
    """
    # Validate agent can be created in Ultravox
    validation_result = await validate_agent_for_ultravox_sync(agent_data, client_id)
    if not validation_result["can_sync"]:
        error_msg = "; ".join(validation_result["errors"])
        raise ValueError(f"Agent cannot be created in Ultravox: {error_msg}")
    
    # Get voice Ultravox ID
    voice_id = agent_data.get("voice_id")
    ultravox_voice_id = await get_voice_ultravox_id(voice_id, client_id)
    if not ultravox_voice_id:
        raise ValueError(f"Voice {voice_id} does not have an Ultravox voice ID")
    
    # Build callTemplate
    call_template = build_ultravox_call_template(agent_data, ultravox_voice_id)
    
    # Log the callTemplate for debugging
    import json
    logger.debug(f"[AGENT_SERVICE] CallTemplate to send to Ultravox: {json.dumps(call_template, indent=2, default=str)}")
    
    # Validate required fields are present
    if not call_template.get("systemPrompt"):
        raise ValueError("systemPrompt is required in callTemplate but is empty")
    if not call_template.get("voice"):
        raise ValueError("voice is required in callTemplate but is empty")
    if call_template.get("temperature") is None:
        raise ValueError("temperature is required in callTemplate but is missing")
    
    # Create agent in Ultravox FIRST
    agent_name = agent_data.get("name", "untitled_agent")
    if not agent_name or not str(agent_name).strip():
        raise ValueError("Agent name is required but is empty or missing")
    
    # Normalize name to match Ultravox requirements (no spaces, only alphanumeric, underscore, hyphen)
    normalized_name = normalize_agent_name(agent_name)
    if normalized_name != agent_name:
        logger.warning(f"[AGENT_SERVICE] Agent name normalized from '{agent_name}' to '{normalized_name}' for Ultravox")
    
    response = await ultravox_client.create_agent(normalized_name, call_template)
    
    logger.info(f"[AGENT_SERVICE] Created agent in Ultravox FIRST: {response.get('agentId')}")
    return response


async def update_agent_ultravox_first(ultravox_agent_id: str, agent_data: Dict[str, Any], client_id: str) -> Dict[str, Any]:
    """
    Update agent in Ultravox FIRST (before database).
    This is the primary source of truth.
    
    Args:
        ultravox_agent_id: Ultravox agent ID
        agent_data: Updated agent data dictionary
        client_id: Client UUID
        
    Returns:
        Ultravox agent response
        
    Raises:
        ValueError: If validation fails
        ProviderError: If Ultravox API call fails
    """
    # Validate agent can be updated in Ultravox
    validation_result = await validate_agent_for_ultravox_sync(agent_data, client_id)
    if not validation_result["can_sync"]:
        error_msg = "; ".join(validation_result["errors"])
        raise ValueError(f"Agent cannot be updated in Ultravox: {error_msg}")
    
    # Get voice Ultravox ID
    voice_id = agent_data.get("voice_id")
    ultravox_voice_id = await get_voice_ultravox_id(voice_id, client_id)
    if not ultravox_voice_id:
        raise ValueError(f"Voice {voice_id} does not have an Ultravox voice ID")
    
    # Build callTemplate
    call_template = build_ultravox_call_template(agent_data, ultravox_voice_id)
    
    # Log the callTemplate for debugging
    import json
    logger.debug(f"[AGENT_SERVICE] CallTemplate to send to Ultravox: {json.dumps(call_template, indent=2, default=str)}")
    
    # Validate required fields are present
    if not call_template.get("systemPrompt"):
        raise ValueError("systemPrompt is required in callTemplate but is empty")
    if not call_template.get("voice"):
        raise ValueError("voice is required in callTemplate but is empty")
    if call_template.get("temperature") is None:
        raise ValueError("temperature is required in callTemplate but is missing")
    
    # Update agent in Ultravox FIRST
    agent_name = agent_data.get("name", "untitled_agent")
    if not agent_name or not str(agent_name).strip():
        raise ValueError("Agent name is required but is empty or missing")
    
    # Normalize name to match Ultravox requirements (no spaces, only alphanumeric, underscore, hyphen)
    normalized_name = normalize_agent_name(agent_name)
    if normalized_name != agent_name:
        logger.warning(f"[AGENT_SERVICE] Agent name normalized from '{agent_name}' to '{normalized_name}' for Ultravox")
    
    response = await ultravox_client.update_agent(ultravox_agent_id, normalized_name, call_template)
    
    logger.info(f"[AGENT_SERVICE] Updated agent in Ultravox FIRST: {ultravox_agent_id}")
    return response


async def sync_agent_to_ultravox(agent_id: str, client_id: str) -> Dict[str, Any]:
    """
    Sync local agent to Ultravox (create or update).
    
    Args:
        agent_id: Local agent UUID
        client_id: Client UUID
        
    Returns:
        Ultravox agent response
        
    Raises:
        ValueError: If agent data is invalid for Ultravox sync
    """
    try:
        db = DatabaseService()
        agent_record = db.select_one("agents", {"id": agent_id, "client_id": client_id})
        
        if not agent_record:
            raise ValueError(f"Agent not found: {agent_id}")
        
        # Validate agent can be synced
        validation_result = await validate_agent_for_ultravox_sync(agent_record, client_id)
        if not validation_result["can_sync"]:
            error_msg = "; ".join(validation_result["errors"])
            raise ValueError(f"Agent cannot be synced to Ultravox: {error_msg}")
        
        ultravox_agent_id = agent_record.get("ultravox_agent_id")
        
        if ultravox_agent_id:
            # Update existing agent
            response = await update_agent_in_ultravox(ultravox_agent_id, agent_record)
        else:
            # Create new agent
            response = await create_agent_in_ultravox(agent_record)
            # Update database with Ultravox agent ID
            ultravox_agent_id = response.get("agentId")
            db.update("agents", {"id": agent_id, "client_id": client_id}, {
                "ultravox_agent_id": ultravox_agent_id,
                "status": "active",
            })
        
        # Sync phone number assignments to Ultravox
        if ultravox_agent_id:
            from app.services.telephony import TelephonyService
            telephony_service = TelephonyService(db)
            phone_numbers = await telephony_service.get_agent_phone_numbers(client_id, agent_id)
            
            # Update Ultravox with inbound numbers using inboundConfig API
            if phone_numbers["inbound"]:
                inbound_numbers = [n["phone_number"] for n in phone_numbers["inbound"]]
                await ultravox_client.update_agent_inbound_config(
                    agent_id=ultravox_agent_id,
                    phone_numbers=inbound_numbers,
                )
                logger.info(f"[AGENT_SERVICE] Synced {len(inbound_numbers)} inbound numbers to Ultravox agent {ultravox_agent_id}")
        
        return response
            
    except ValueError:
        # Re-raise validation errors as-is
        raise
    except Exception as e:
        import traceback
        import json
        error_details = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
            "agent_id": agent_id,
            "client_id": client_id,
        }
        logger.error(f"[AGENT_SERVICE] Failed to sync agent to Ultravox (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
        raise
