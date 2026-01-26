"""
API Key Generation Utility
Generates cryptographically secure random API keys
"""
import secrets
import string
import logging

logger = logging.getLogger(__name__)


def generate_random_api_key(length: int = 32, prefix: str = "truedy_") -> str:
    """
    Generate a cryptographically secure random API key
    
    Args:
        length: Length of the random part (default: 32)
        prefix: Prefix to add before the random part (default: "truedy_")
    
    Returns:
        Generated API key string in format: prefix + random_alphanumeric_string
        Example: "truedy_Ab3xY9mK2pQ7vN4wR8tL5sF1gH6jD0"
    """
    # Use secrets module for cryptographically secure random generation
    # Generate alphanumeric string (uppercase, lowercase, digits)
    alphabet = string.ascii_letters + string.digits  # A-Z, a-z, 0-9
    
    # Generate random string using secrets.choice for cryptographically secure randomness
    random_part = ''.join(secrets.choice(alphabet) for _ in range(length))
    
    # Combine prefix with random part
    api_key = prefix + random_part
    
    logger.debug(f"Generated API key with length {length}, prefix '{prefix}'")
    
    return api_key
