#!/usr/bin/env python3
"""Write a simple Word document from a markdown script. Standard library only."""

import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def paragraphs(markdown: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            blocks.append(("Heading1", line[2:].strip()))
        elif line.startswith("## "):
            blocks.append(("Heading2", line[3:].strip()))
        elif line.startswith("### "):
            blocks.append(("Heading3", line[4:].strip()))
        else:
            blocks.append(("Normal", line.lstrip("- ").strip()))
    return blocks or [("Normal", "")]


def right_to_left(text: str) -> bool:
    return any("\u0600" <= char <= "\u06ff" for char in text)


def document_xml(blocks: list[tuple[str, str]]) -> str:
    body = []
    for style, text in blocks:
        rtl = right_to_left(text)
        paragraph_rtl = "<w:bidi/>" if rtl else ""
        run_rtl = '<w:rtl/>' if rtl else ""
        body.append(
            "<w:p>"
            f'<w:pPr><w:pStyle w:val="{style}"/>{paragraph_rtl}</w:pPr>'
            "<w:r><w:rPr>"
            '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
            f"{run_rtl}"
            "</w:rPr>"
            f'<w:t xml:space="preserve">{escape(text)}</w:t>'
            "</w:r></w:p>"
        )
    body.append(
        "<w:sectPr>"
        '<w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        '<w:bidi w:val="1"/>'
        "</w:sectPr>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

WORD_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Normal" w:default="1">
    <w:name w:val="Normal"/>
    <w:rPr><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:rPr><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading3">
    <w:name w:val="heading 3"/>
    <w:rPr><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr>
  </w:style>
</w:styles>
"""


def write_docx(markdown: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    xml = document_xml(paragraphs(markdown))
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", CONTENT_TYPES)
        package.writestr("_rels/.rels", RELS)
        package.writestr("word/_rels/document.xml.rels", WORD_RELS)
        package.writestr("word/styles.xml", STYLES)
        package.writestr("word/document.xml", xml)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: md_to_docx.py INPUT.md OUTPUT.docx")
    source, dest = Path(sys.argv[1]), Path(sys.argv[2])
    write_docx(source.read_text(encoding="utf-8"), dest)


if __name__ == "__main__":
    main()
