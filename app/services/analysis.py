"""
Post-Call Intelligence Analysis Service
Analyzes call transcripts to extract summaries, sentiment, structured data, and success criteria
"""
import logging
from typing import Dict, Any, Optional
import json
from app.core.config import settings
from app.core.database import DatabaseService, DatabaseAdminService
from app.core.exceptions import ProviderError

logger = logging.getLogger(__name__)

# Try to import OpenAI client
try:
    import openai
    openai_available = True
except ImportError:
    openai_available = False
    logger.warning("OpenAI library not available. Analysis features will be disabled.")


async def analyze_call_transcript(
    call_id: str,
    transcript_text: str,
    agent_id: str,
    success_criteria: Optional[str] = None,
    extraction_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Analyze a call transcript using LLM to extract:
    - Summary (2 sentences)
    - Sentiment (positive/neutral/negative)
    - Structured data (based on extraction_schema)
    - Success flag (based on success_criteria)
    
    Returns analysis results dictionary.
    """
    if not openai_available or not settings.OPENAI_API_KEY:
        logger.warning("OpenAI not configured. Skipping call analysis.")
        return {
            "summary": None,
            "sentiment": "neutral",
            "structured_data": {},
            "is_success": False,
            "analysis_status": "failed",
            "analysis_error": "OpenAI not configured",
        }
    
    if not transcript_text or not transcript_text.strip():
        logger.warning(f"Empty transcript for call {call_id}. Skipping analysis.")
        return {
            "summary": "No transcript available (call may have ended immediately).",
            "sentiment": "neutral",
            "structured_data": {},
            "is_success": False,
            "analysis_status": "completed",
            "analysis_error": None,
        }
    
    try:
        # Get agent info for context
        admin_db = DatabaseAdminService()
        agent = admin_db.select_one("agents", {"id": agent_id})
        agent_name = agent.get("name", "Agent") if agent else "Agent"
        
        # Build analysis prompt
        analysis_prompt = f"""Analyze the following call transcript and extract key information.

Call Transcript:
{transcript_text}

Agent: {agent_name}

Please provide:
1. A brief 2-sentence summary of the call
2. Sentiment analysis: "positive", "neutral", or "negative"
3. Extract structured data based on the schema provided
4. Determine if the call met the success criteria

"""
        
        # Add extraction schema if provided
        if extraction_schema:
            schema_description = json.dumps(extraction_schema, indent=2)
            analysis_prompt += f"""
Extraction Schema (extract these fields from the transcript):
{schema_description}

"""
        
        # Add success criteria if provided
        if success_criteria:
            analysis_prompt += f"""
Success Criteria: {success_criteria}

Determine if this call met the success criteria. Return true only if the criteria was clearly met.
"""
        
        analysis_prompt += """
Respond with a JSON object in this exact format:
{
  "summary": "Two sentence summary of the call",
  "sentiment": "positive" | "neutral" | "negative",
  "structured_data": {
    // Extracted fields based on schema, or empty object if no schema
  },
  "is_success": true | false
}
"""
        
        # Call OpenAI API
        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        
        # Use a fast, cost-effective model for analysis
        model = "gpt-4o-mini"  # Fast and cheap for analysis
        
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a call analysis assistant. Analyze call transcripts and extract structured information. Always respond with valid JSON only, no additional text.",
                },
                {
                    "role": "user",
                    "content": analysis_prompt,
                },
            ],
            temperature=0.2,  # Low temperature for consistent extraction
            response_format={"type": "json_object"},  # Force JSON response
        )
        
        # Parse response
        response_text = response.choices[0].message.content
        analysis_result = json.loads(response_text)
        
        # Validate and normalize results
        summary = analysis_result.get("summary", "No summary available.")
        sentiment = analysis_result.get("sentiment", "neutral").lower()
        if sentiment not in ["positive", "neutral", "negative"]:
            sentiment = "neutral"
        
        structured_data = analysis_result.get("structured_data", {})
        if not isinstance(structured_data, dict):
            structured_data = {}
        
        is_success = bool(analysis_result.get("is_success", False))
        
        logger.info(f"Analysis completed for call {call_id}: sentiment={sentiment}, success={is_success}")
        
        return {
            "summary": summary,
            "sentiment": sentiment,
            "structured_data": structured_data,
            "is_success": is_success,
            "analysis_status": "completed",
            "analysis_error": None,
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse analysis JSON for call {call_id}: {e}", exc_info=True)
        return {
            "summary": None,
            "sentiment": "neutral",
            "structured_data": {},
            "is_success": False,
            "analysis_status": "failed",
            "analysis_error": f"Failed to parse analysis response: {str(e)}",
        }
    except openai.APIStatusError as e:
        logger.error(f"OpenAI API error during call analysis: {e}", exc_info=True)
        return {
            "summary": None,
            "sentiment": "neutral",
            "structured_data": {},
            "is_success": False,
            "analysis_status": "failed",
            "analysis_error": f"OpenAI API error: {e.status_code} - {str(e)}",
        }
    except Exception as e:
        logger.error(f"Unexpected error during call analysis: {e}", exc_info=True)
        return {
            "summary": None,
            "sentiment": "neutral",
            "structured_data": {},
            "is_success": False,
            "analysis_status": "failed",
            "analysis_error": f"Unexpected error: {str(e)}",
        }


async def process_call_metadata(
    call_id: str,
    transcript_text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Process call metadata after call ends.
    Fetches call and agent data, analyzes transcript, and updates database.
    Returns analysis results.
    """
    admin_db = DatabaseAdminService()
    
    # Get call data
    call = admin_db.select_one("calls", {"id": call_id})
    if not call:
        logger.error(f"Call {call_id} not found for analysis")
        return {"error": "Call not found"}
    
    # Update status to processing
    admin_db.update(
        "calls",
        {"id": call_id},
        {"analysis_status": "processing"},
    )
    
    # Get transcript if not provided
    if not transcript_text:
        transcript = call.get("transcript")
        if isinstance(transcript, dict):
            # Extract text from structured transcript
            transcript_text = transcript.get("text") or transcript.get("transcript") or ""
            if not transcript_text and isinstance(transcript.get("messages"), list):
                # If it's a list of messages, join them
                transcript_text = "\n".join([
                    msg.get("text", "") for msg in transcript.get("messages", [])
                    if msg.get("text")
                ])
        elif isinstance(transcript, str):
            transcript_text = transcript
        else:
            transcript_text = ""
    
    # Get agent data for success criteria and extraction schema
    agent_id = call.get("agent_id")
    if not agent_id:
        logger.error(f"Call {call_id} has no agent_id")
        admin_db.update(
            "calls",
            {"id": call_id},
            {
                "analysis_status": "failed",
                "analysis_error": "Call has no agent_id",
            },
        )
        return {"error": "Call has no agent_id"}
    
    agent = admin_db.select_one("agents", {"id": agent_id})
    if not agent:
        logger.error(f"Agent {agent_id} not found for call {call_id}")
        admin_db.update(
            "calls",
            {"id": call_id},
            {
                "analysis_status": "failed",
                "analysis_error": "Agent not found",
            },
        )
        return {"error": "Agent not found"}
    
    success_criteria = agent.get("success_criteria")
    extraction_schema = agent.get("extraction_schema") or {}
    
    # Analyze transcript
    analysis_result = await analyze_call_transcript(
        call_id=call_id,
        transcript_text=transcript_text,
        agent_id=agent_id,
        success_criteria=success_criteria,
        extraction_schema=extraction_schema,
    )
    
    # Update call with analysis results
    update_data = {
        "summary": analysis_result.get("summary"),
        "sentiment": analysis_result.get("sentiment"),
        "structured_data": analysis_result.get("structured_data"),
        "is_success": analysis_result.get("is_success"),
        "analysis_status": analysis_result.get("analysis_status"),
        "analysis_error": analysis_result.get("analysis_error"),
    }
    
    admin_db.update("calls", {"id": call_id}, update_data)
    
    logger.info(f"Call {call_id} analysis completed: sentiment={analysis_result.get('sentiment')}, success={analysis_result.get('is_success')}")
    
    return analysis_result
