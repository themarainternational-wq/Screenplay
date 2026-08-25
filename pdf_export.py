"""
pdf_export.py
-------------
Turns a list of (block_type, text) blocks into a properly formatted PDF.
Automatically embeds the (free, open-source) Noto Sans Tamil font
whenever the document contains Tamil script, so Tamil text renders
correctly instead of showing boxes/garbage. Falls back to the built-in
Helvetica for pure-English documents.
"""

import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
_TAMIL_FONT_PATH = os.path.join(_FONT_DIR, "NotoSansTamil.ttf")
_TAMIL_FONT_NAME = "NotoSansTamil"
_registered = False


def _contains_tamil(blocks):
    for _, text in blocks:
        for ch in text:
            if 0x0B80 <= ord(ch) <= 0x0BFF:
                return True
    return False


def _ensure_font_registered():
    global _registered
    if not _registered:
        if os.path.exists(_TAMIL_FONT_PATH):
            pdfmetrics.registerFont(TTFont(_TAMIL_FONT_NAME, _TAMIL_FONT_PATH))
        _registered = True


def export_pdf(blocks, out_path, title=None):
    use_tamil = _contains_tamil(blocks) and os.path.exists(_TAMIL_FONT_PATH)
    if use_tamil:
        _ensure_font_registered()
        base_font = _TAMIL_FONT_NAME
    else:
        base_font = "Helvetica"

    styles = {
        "SCENE_HEADING": ParagraphStyle("scene", fontName=base_font, fontSize=12,
                                         leading=16, spaceBefore=14, spaceAfter=6,
                                         alignment=TA_LEFT),
        "CHARACTER": ParagraphStyle("char", fontName=base_font, fontSize=11,
                                     leading=15, leftIndent=2.2 * inch,
                                     spaceBefore=10, alignment=TA_LEFT),
        "PARENTHETICAL": ParagraphStyle("paren", fontName=base_font, fontSize=10,
                                         leading=13, leftIndent=1.8 * inch,
                                         alignment=TA_LEFT),
        "DIALOGUE": ParagraphStyle("dialog", fontName=base_font, fontSize=11,
                                    leading=15, leftIndent=1.3 * inch,
                                    rightIndent=1.3 * inch, alignment=TA_LEFT),
        "TRANSITION": ParagraphStyle("trans", fontName=base_font, fontSize=11,
                                      leading=15, alignment=TA_RIGHT),
        "ACTION": ParagraphStyle("action", fontName=base_font, fontSize=11,
                                  leading=15, spaceAfter=6, alignment=TA_LEFT),
        "TITLE": ParagraphStyle("title", fontName=base_font, fontSize=18,
                                 leading=22, alignment=TA_CENTER, spaceAfter=20),
    }

    doc = SimpleDocTemplate(out_path, pagesize=letter,
                             leftMargin=1 * inch, rightMargin=1 * inch,
                             topMargin=1 * inch, bottomMargin=1 * inch)
    story = []
    if title:
        story.append(Paragraph(_escape(title), styles["TITLE"]))

    for btype, text in blocks:
        if not text.strip():
            continue
        style = styles.get(btype, styles["ACTION"])
        story.append(Paragraph(_escape(text), style))
        story.append(Spacer(1, 2))

    doc.build(story)
    return out_path


def _escape(text):
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
