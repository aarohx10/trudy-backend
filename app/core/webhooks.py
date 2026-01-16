"""
Webhook Signature Verification and Delivery
"""
import hmac
import hashlib
import time
import json
from typing import Dict, Any, Optional
import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


def verify_ultravox_signature(
    signature: str,
    timestamp: str,
    body: str,
    secret: str,
) -> bool:
    """Verify Ultravox webhook signature"""
    try:
        # Reconstruct message
        message = f"{timestamp}.{body}"
        
        # Calculate expected signature
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        
        # Constant-time comparison
        return hmac.compare_digest(signature, expected_signature)
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False


def verify_timestamp(timestamp: str, max_age_seconds: int = 300) -> bool:
    """Verify webhook timestamp (prevent replay attacks)"""
    try:
        ts = int(timestamp)
        current_time = int(time.time())
        
        # Check if timestamp is too old
        if current_time - ts > max_age_seconds:
            return False
        
        # Check if timestamp is too far in future (clock skew)
        if ts > current_time + 60:
            return False
        
        return True
    except (ValueError, TypeError):
        return False


def generate_webhook_signature(
    payload: Dict[str, Any],
    secret: str,
    timestamp: Optional[str] = None,
) -> tuple[str, str]:
    """Generate webhook signature for egress"""
    if timestamp is None:
        timestamp = str(int(time.time()))
    
    body = json.dumps(payload, sort_keys=True)
    message = f"{timestamp}.{body}"
    
    signature = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    
    return signature, timestamp


def verify_telnyx_signature(
    signature: str,
    timestamp: str,
    body: str,
    secret: str,
) -> bool:
    """
    Verify Telnyx webhook signature using HMAC SHA256
    
    Args:
        signature: Telnyx-Signature header value
        timestamp: Timestamp from header (if included) or current time
        body: Raw request body as string
        secret: Telnyx webhook signing secret
    
    Returns:
        True if signature is valid, False otherwise
    """
    try:
        # Telnyx uses HMAC SHA256 similar to Ultravox
        # Format: signature is HMAC of (timestamp + body) or just body
        # We'll use the same format as Ultravox: timestamp.body
        message = f"{timestamp}.{body}" if timestamp else body
        
        # Calculate expected signature
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        
        # Constant-time comparison
        return hmac.compare_digest(signature, expected_signature)
    except Exception as e:
        logger.error(f"Telnyx signature verification error: {e}")
        return False


def verify_stripe_signature(
    payload: str,
    signature: str,
    secret: str,
    tolerance: int = 300,
) -> bool:
    """
    Verify Stripe webhook signature
    
    Args:
        payload: Raw request body as string
        signature: Stripe-Signature header value
        secret: Stripe webhook signing secret
        tolerance: Maximum age of timestamp in seconds (default 5 minutes)
    
    Returns:
        True if signature is valid, False otherwise
    """
    try:
        import time
        
        # Parse signature header (format: t=timestamp,v1=signature,v0=signature)
        sig_parts = {}
        for part in signature.split(','):
            if '=' in part:
                key, value = part.split('=', 1)
                sig_parts[key.strip()] = value.strip()
        
        timestamp = sig_parts.get('t')
        sig = sig_parts.get('v1')
        
        if not timestamp or not sig:
            logger.error("Invalid Stripe signature format")
            return False
        
        # Check timestamp age
        current_time = int(time.time())
        if abs(current_time - int(timestamp)) > tolerance:
            logger.error(f"Stripe webhook timestamp too old or too far in future: {timestamp}")
            return False
        
        # Reconstruct signed payload
        signed_payload = f"{timestamp}.{payload}"
        
        # Calculate expected signature
        expected_sig = hmac.new(
            secret.encode('utf-8'),
            signed_payload.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        
        # Constant-time comparison
        return hmac.compare_digest(sig, expected_sig)
        
    except Exception as e:
        logger.error(f"Stripe signature verification error: {e}")
        return False


async def deliver_webhook(
    url: str,
    payload: Dict[str, Any],
    secret: str,
    timeout: int = 10,
) -> tuple[bool, Optional[int], Optional[str]]:
    """
    Deliver webhook to client endpoint
    
    Returns:
        (success, status_code, error_message)
    """
    try:
        signature, timestamp = generate_webhook_signature(payload, secret)
        
        headers = {
            "Content-Type": "application/json",
            "X-Trudy-Timestamp": timestamp,
            "X-Trudy-Signature": signature,
        }
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
            
            if 200 <= response.status_code < 300:
                return True, response.status_code, None
            else:
                return False, response.status_code, response.text[:500]
                
    except httpx.TimeoutException:
        return False, None, "Request timeout"
    except Exception as e:
        logger.error(f"Webhook delivery error: {e}")
        return False, None, str(e)

