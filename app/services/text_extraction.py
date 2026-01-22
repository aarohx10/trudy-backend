"""
Text Extraction Service for Knowledge Base Documents
"""
import logging
from typing import List
from pathlib import Path
import io
import httpx
import re
from html.parser import HTMLParser

logger = logging.getLogger(__name__)


class HTMLTextExtractor(HTMLParser):
    """Extract text from HTML, removing script, style, and navigation elements"""
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.skip_tags = {'script', 'style', 'noscript', 'nav', 'header', 'footer'}
        self.in_skip_tag = False
    
    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.skip_tags:
            self.in_skip_tag = True
    
    def handle_endtag(self, tag):
        if tag.lower() in self.skip_tags:
            self.in_skip_tag = False
    
    def handle_data(self, data):
        if not self.in_skip_tag:
            text = data.strip()
            if text:
                self.text_parts.append(text)
    
    def get_text(self) -> str:
        """Get extracted text, cleaned and normalized"""
        text = ' '.join(self.text_parts)
        # Remove excessive whitespace
        text = re.sub(r'\s+', ' ', text)
        # Remove common UI patterns
        ui_patterns = [
            r'\b(File|Edit|View|Insert|Format|Tools|Extensions|Help|Sign in|Share|Tab|External|Menu|Undo|Redo|Print)\b',
            r'\b(Copy format|Normal text|Arial|Bold|Italic|Underline|Align|Line spacing)\b',
            r'\b(Checklist|Bullet points|Numbered list|Indent|Outdent|Clear formatting)\b',
            r'\b(Version history|Comment|Video call|Editing|Document tabs)\b',
        ]
        for pattern in ui_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        return text.strip()


async def extract_text_from_url(url: str) -> str:
    """
    Extract text from a web URL by fetching HTML and stripping tags.
    
    Args:
        url: The URL to fetch and extract text from
    
    Returns:
        Extracted text content
    """
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                }
            )
            response.raise_for_status()
            html_content = response.text
            
            # Extract text from HTML
            extractor = HTMLTextExtractor()
            extractor.feed(html_content)
            text = extractor.get_text()
            
            if not text or len(text.strip()) < 10:
                raise ValueError(f"Could not extract meaningful text from URL: {url}")
            
            return text
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching URL {url}: {e.response.status_code}")
        raise ValueError(f"Failed to fetch URL: HTTP {e.response.status_code}")
    except Exception as e:
        logger.error(f"Failed to extract text from URL {url}: {e}", exc_info=True)
        raise


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
