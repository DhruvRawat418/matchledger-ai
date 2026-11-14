"""Document Extraction Agent. See docs/EXTRACTION.md."""
from src.extraction.client import FakeModelClient, ModelCallError, ModelClient, ModelReply
from src.extraction.extractor import extract_document
from src.extraction.pages import Page, load_pages
from src.extraction.prompt import PROMPT_VERSION
from src.extraction.schema import ExtractionIssue, ExtractionOutcome, ExtractionRequest, ModelDocument

__all__ = ["FakeModelClient", "ModelCallError", "ModelClient", "ModelReply", "extract_document",
           "Page", "load_pages", "PROMPT_VERSION", "ExtractionIssue", "ExtractionOutcome",
           "ExtractionRequest", "ModelDocument"]
