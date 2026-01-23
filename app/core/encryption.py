"""
Fernet Encryption Service (Hetzner VPS)
Replaces AWS KMS with Fernet symmetric encryption
"""
import logging
from cryptography.fernet import Fernet
from typing import Optional
import base64
import hashlib
from app.core.config import settings

logger = logging.getLogger(__name__)

# Global Fernet instance
_fernet = None


def get_fernet():
    """Get or create Fernet instance"""
    global _fernet
    
    if _fernet is None:
        encryption_key = getattr(settings, 'ENCRYPTION_KEY', None) or None
        
        if not encryption_key:
            logger.warning("ENCRYPTION_KEY not set. Encryption disabled.")
            return None
        
        try:
            # If key is not 32 bytes, derive from it using SHA256
            if len(encryption_key) != 32:
                key_bytes = hashlib.sha256(encryption_key.encode()).digest()
            else:
                key_bytes = encryption_key.encode()[:32]
            
            # Ensure key is base64 URL-safe (Fernet requirement)
            key = base64.urlsafe_b64encode(key_bytes)
            _fernet = Fernet(key)
            logger.info("Fernet encryption initialized successfully")
        except Exception as e:
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_traceback": traceback.format_exc(),
            }
            logger.error(f"[ENCRYPTION] Failed to create Fernet instance (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            return None
    
    return _fernet


def encrypt_api_key(plaintext: str, key_id: Optional[str] = None) -> Optional[str]:
    """
    Encrypt API key using Fernet (replaces AWS KMS)
    
    Args:
        plaintext: The API key to encrypt
        key_id: Not used (kept for compatibility)
    
    Returns:
        Encrypted ciphertext (base64 encoded) or None if encryption fails
    """
    if not plaintext:
        return None
    
    fernet = get_fernet()
    if not fernet:
        logger.warning("Fernet not available. Storing as plaintext (not recommended for production)")
        return plaintext
    
    try:
        encrypted = fernet.encrypt(plaintext.encode())
        return encrypted.decode()
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "key_id": key_id,
        }
        logger.error(f"[ENCRYPTION] Encryption error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        return None


def decrypt_api_key(ciphertext: str, key_id: Optional[str] = None) -> Optional[str]:
    """
    Decrypt API key using Fernet (replaces AWS KMS)
    
    Args:
        ciphertext: The encrypted API key (base64 encoded)
        key_id: Not used (kept for compatibility)
    
    Returns:
        Decrypted plaintext or None if decryption fails
    """
    if not ciphertext:
        return None
    
    fernet = get_fernet()
    if not fernet:
        # Assume plaintext (backward compatibility for development)
        logger.warning("Fernet not available. Assuming plaintext (development mode)")
        return ciphertext
    
    try:
        decrypted = fernet.decrypt(ciphertext.encode())
        return decrypted.decode()
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "key_id": key_id,
        }
        # Try as plaintext (backward compatibility)
        logger.warning(f"[ENCRYPTION] Decryption error (trying as plaintext) (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        return ciphertext
