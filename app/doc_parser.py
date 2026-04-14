from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.models import ArticleDocument, Section, normalize_text


def extract_tab_id(document_url: str) -> str | None:
    parsed = urlparse(document_url)
    query = parse_qs(parsed.query)
    tab_values = query.get("tab")
    return tab_values[0] if tab_values else None


def _paragraph_text(paragraph: dict) -> str:
    parts: list[tuple[str, bool]] = []
    for element in paragraph.get("elements", []):
        text_run = element.get("textRun")
        if text_run:
            parts.append(
                (
                    text_run.get("content", ""),
                    bool(text_run.get("textStyle", {}).get("bold")),
                )
            )

    normalized_parts: list[str] = []
    previous_was_bold = False
    for index, (raw_text, is_bold) in enumerate(parts):
        text = raw_text.replace("\u000b", " ")
        if not text:
            continue
        if (
            index > 0
            and previous_was_bold
            and not is_bold
            and not text.startswith((" ", "\n"))
            and normalized_parts
            and not normalized_parts[-1].endswith((" ", "\n"))
        ):
            normalized_parts.append("\n")
        normalized_parts.append(text)
        previous_was_bold = is_bold

    text = "".join(normalized_parts).strip()
    if paragraph.get("bullet") and text:
        return f"• {text}"
    return text


def _flatten_content(elements: list[dict]) -> list[dict[str, str]]:
    flattened: list[dict[str, str]] = []

    for element in elements:
        paragraph = element.get("paragraph")
        if paragraph:
            text = _paragraph_text(paragraph)
            if text:
                flattened.append(
                    {
                        "style": paragraph.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT"),
                        "text": text,
                    }
                )
            continue

        table = element.get("table")
        if table:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    flattened.extend(_flatten_content(cell.get("content", [])))
            continue

        toc = element.get("tableOfContents")
        if toc:
            flattened.extend(_flatten_content(toc.get("content", [])))

    return flattened


def _pick_body_content(document: dict, preferred_tab_id: str | None) -> list[dict]:
    tabs = document.get("tabs") or []
    if tabs:
        chosen_tab = next(
            (tab for tab in tabs if tab.get("tabId") == preferred_tab_id),
            tabs[0],
        )
        return chosen_tab.get("body", {}).get("content", [])
    return document.get("body", {}).get("content", [])


def parse_google_document(
    document: dict,
    document_url: str,
    excluded_titles: tuple[str, ...] = (),
) -> ArticleDocument:
    doc_id = document.get("documentId", "")
    preferred_tab_id = extract_tab_id(document_url)
    content = _pick_body_content(document, preferred_tab_id)
    paragraphs = _flatten_content(content)

    excluded = {normalize_text(title) for title in excluded_titles}
    intro_parts: list[str] = []
    sections: list[Section] = []
    current_title: str | None = None
    current_body: list[str] = []
    title = document.get("title", "").strip()

    def flush_section() -> None:
        nonlocal current_title, current_body
        if not current_title:
            return
        if normalize_text(current_title) in excluded:
            current_title = None
            current_body = []
            return
        section_body = "\n\n".join(part for part in current_body if part).strip()
        sections.append(
            Section(
                index=len(sections) + 1,
                title=current_title,
                body=section_body,
                illustrations=(),
            )
        )
        current_title = None
        current_body = []

    for item in paragraphs:
        style = item["style"]
        text = item["text"]

        if style == "HEADING_1":
            if not title:
                title = text
            continue

        if style == "HEADING_2":
            flush_section()
            current_title = text
            current_body = []
            continue

        if current_title is None:
            intro_parts.append(text)
        else:
            current_body.append(text)

    flush_section()

    if not sections:
        fallback_body = "\n\n".join(part for part in intro_parts if part).strip()
        sections = [Section(index=1, title="Текст статьи", body=fallback_body)]
        intro_parts = []

    return ArticleDocument(
        doc_id=doc_id,
        title=title or "Без названия",
        intro="\n\n".join(part for part in intro_parts if part).strip(),
        intro_illustrations=(),
        document_url=document_url,
        sections=sections,
    )
