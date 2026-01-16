"""
Retry Logic with Exponential Backoff
"""
import asyncio
import random
import logging
from typing import Callable, TypeVar, Optional
import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_with_backoff(
    func: Callable[[], T],
    max_attempts: int = 5,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    retryable_status_codes: list[int] = [429, 500, 502, 503, 504],
) -> T:
    """
    Retry function with exponential backoff and jitter
    
    Args:
        func: Async function to retry
        max_attempts: Maximum number of retry attempts
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        retryable_status_codes: HTTP status codes that should trigger retry
    
    Returns:
        Result of function call
    
    Raises:
        Last exception if all retries fail
    """
    last_exception = None
    
    for attempt in range(1, max_attempts + 1):
        try:
            return await func()
        except (httpx.HTTPStatusError, httpx.TimeoutException) as e:
            last_exception = e
            
            # Don't retry on last attempt
            if attempt == max_attempts:
                raise
            
            # Check if error is retryable
            if isinstance(e, httpx.HTTPStatusError):
                if e.response.status_code not in retryable_status_codes:
                    raise  # Don't retry on non-retryable errors
            
            # Calculate delay with exponential backoff
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            
            # Add jitter (0-30% random variation)
            jitter = delay * random.uniform(0, 0.3)
            final_delay = delay + jitter
            
            logger.warning(
                f"Retry attempt {attempt}/{max_attempts} after {final_delay:.2f}s",
                extra={"attempt": attempt, "delay": final_delay},
            )
            
            await asyncio.sleep(final_delay)
        except Exception as e:
            # Non-HTTP errors: don't retry
            logger.error(f"Non-retryable error: {e}")
            raise
    
    # Should not reach here, but just in case
    if last_exception:
        raise last_exception

