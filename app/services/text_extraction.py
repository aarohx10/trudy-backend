"""
Text Extraction Service for Knowledge Base Documents
"""
import logging
from typing import List
from pathlib import Path
import io

logger = logging.getLogger(__name__)


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    """
    Split text into chunks with overlap (standard RAG practice).
    
    Args:
        text: The text to chunk
        chunk_size: Target size for each chunk (characters)
        overlap: Number of characters to overlap between chunks
    
    Returns:
        List of text chunks
    """
    if not text or len(text) <= chunk_size:
        return [text] if text else []
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        
        # Try to break at sentence boundary if possible
        if end < len(text):
            # Look for sentence endings in the last 100 chars
            last_period = chunk.rfind('.')
            last_newline = chunk.rfind('\n')
            break_point = max(last_period, last_newline)
            
            if break_point > chunk_size - 200:  # If we found a good break point
                chunk = chunk[:break_point + 1]
                end = start + break_point + 1
        
        chunks.append(chunk.strip())
        start = end - overlap  # Overlap for context
    
    return chunks


async def extract_text_from_file(file_path: str, content_type: str) -> str:
    """
    Extract text from uploaded file based on content type.
    Supports PDF, DOCX, and plain text files.
    """
    file_ext = Path(file_path).suffix.lower()
    
    try:
        if file_ext == '.pdf' or content_type == 'application/pdf':
            return await _extract_pdf(file_path)
        elif file_ext in ['.docx', '.doc'] or 'wordprocessingml' in content_type or 'msword' in content_type:
            return await _extract_docx(file_path)
        elif file_ext in ['.txt', '.md'] or 'text/plain' in content_type:
            return await _extract_text(file_path)
        else:
            logger.warning(f"Unsupported file type: {file_ext} ({content_type})")
            return ""
    except Exception as e:
        logger.error(f"Failed to extract text from {file_path}: {e}", exc_info=True)
        raise


async def _extract_pdf(file_path: str) -> str:
    """Extract text from PDF file"""
    try:
        import PyPDF2
        
        text_parts = []
        with open(file_path, 'rb') as f:
            pdf_reader = PyPDF2.PdfReader(f)
            for page in pdf_reader.pages:
                text_parts.append(page.extract_text())
        
        return '\n\n'.join(text_parts)
    except ImportError:
        logger.error("PyPDF2 is not installed. Install with: pip install PyPDF2")
        raise
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}", exc_info=True)
        raise


async def _extract_docx(file_path: str) -> str:
    """Extract text from DOCX file"""
    try:
        from docx import Document
        
        doc = Document(file_path)
        text_parts = []
        
        for paragraph in doc.paragraphs:
            if paragraph.text.strip():
                text_parts.append(paragraph.text)
        
        return '\n\n'.join(text_parts)
    except ImportError:
        logger.error("python-docx is not installed. Install with: pip install python-docx")
        raise
    except Exception as e:
        logger.error(f"DOCX extraction failed: {e}", exc_info=True)
        raise


async def _extract_text(file_path: str) -> str:
    """Extract text from plain text file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        # Try with different encoding
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Text extraction failed: {e}", exc_info=True)
            raise
    except Exception as e:
        logger.error(f"Text extraction failed: {e}", exc_info=True)
        raise
