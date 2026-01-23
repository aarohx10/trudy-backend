"""
OpenAI Embeddings Service
"""
import logging
from typing import List
from openai import AsyncOpenAI
from app.core.config import settings

logger = logging.getLogger(__name__)

# Global OpenAI client
_openai_client: AsyncOpenAI = None


def get_openai_client() -> AsyncOpenAI:
    """Get or create OpenAI client"""
    global _openai_client
    
    if _openai_client is None:
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY must be configured for embeddings")
        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    
    return _openai_client


async def generate_embedding(text: str) -> List[float]:
    """
    Generate embedding for a single text using OpenAI text-embedding-3-small.
    Returns a 1536-dimensional vector.
    """
    if not text or not text.strip():
        raise ValueError("Text cannot be empty")
    
    client = get_openai_client()
    
    try:
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text.strip(),
        )
        return response.data[0].embedding
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "text_length": len(text) if text else 0,
        }
        logger.error(f"[EMBEDDINGS] Failed to generate embedding (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise


async def generate_embeddings_batch(texts: List[str], batch_size: int = 100) -> List[List[float]]:
    """
    Generate embeddings for multiple texts in batches.
    Returns a list of 1536-dimensional vectors.
    """
    if not texts:
        return []
    
    client = get_openai_client()
    all_embeddings = []
    
    # Process in batches to avoid rate limits
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        # Filter out empty texts
        batch = [t.strip() for t in batch if t and t.strip()]
        
        if not batch:
            continue
        
        try:
            response = await client.embeddings.create(
                model="text-embedding-3-small",
                input=batch,
            )
            # Map embeddings back to original order (accounting for filtered items)
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)
        except Exception as e:
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_traceback": traceback.format_exc(),
                "batch_size": len(batch),
                "batch_index": i,
                "total_texts": len(texts),
            }
            logger.error(f"[EMBEDDINGS] Failed to generate embeddings batch (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            raise
    
    return all_embeddings
