"""Concrete attachment parsers."""

from .doc import DocParser
from .docx import DocxParser
from .excel import ExcelParser
from .html import HtmlParser
from .markdown import MarkdownParser
from .pdf import PdfParser
from .pptx import PptxParser
from .text import TextParser

__all__ = [
    "DocParser",
    "DocxParser",
    "ExcelParser",
    "HtmlParser",
    "MarkdownParser",
    "PdfParser",
    "PptxParser",
    "TextParser",
]
