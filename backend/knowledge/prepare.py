"""Create auditable, PostgreSQL-ready JSONL staging files from downloaded sources.

Preserves source offsets and table row/column relationships. Does not publish a
knowledge release or manufacture embeddings. All inputs are untrusted documents.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def table_markdown(table):
    grid, carry = [], {}
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        values = {col: value for col, (value, _remaining) in carry.items()}
        following = {col: (value, remaining - 1) for col, (value, remaining) in carry.items() if remaining > 1}
        cursor = 0
        for cell in row.find_all(["td", "th"], recursive=False):
            while cursor in values:
                cursor += 1
            value = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).replace("|", "\\|")
            colspan = max(1, min(40, int(cell.get("colspan", 1))))
            rowspan = max(1, min(100, int(cell.get("rowspan", 1))))
            for column in range(cursor, cursor + colspan):
                values[column] = value
                if rowspan > 1:
                    following[column] = (value, rowspan - 1)
            cursor += colspan
        carry = following
        if values:
            grid.append([values.get(i, "") for i in range(max(values) + 1)])
    if not grid:
        return ""
    width = max(map(len, grid))
    grid = [row + [""] * (width - len(row)) for row in grid]
    # Neutral generated labels avoid mistaking the first data row for a header.
    rows = [["Column " + str(i + 1) for i in range(width)], ["---"] * width] + grid
    return "\n\n" + "\n".join("| " + " | ".join(row) + " |" for row in rows) + "\n\n"


def render(node, url):
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node))
    if node.name in ["script", "style", "nav", "footer", "form", "button", "noscript", "svg", "img"]:
        return ""
    if node.name == "table":
        return table_markdown(node)
    if node.name == "br":
        return "\n"
    body = "".join(render(child, url) for child in node.children)
    if node.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
        return "\n\n" + "#" * int(node.name[1]) + " " + body.strip() + "\n\n"
    if node.name == "li":
        return "\n- " + body.strip() + "\n"
    if node.name == "a":
        href = node.get("href", "")
        absolute = urljoin(url, href)
        if body.strip() and href and absolute.startswith(("https://", "http://")):
            return "[" + body.strip() + "](" + absolute + ")"
        return body
    if node.name in ["p", "div", "section", "article", "ul", "ol"]:
        return "\n\n" + body.strip() + "\n\n"
    return body


def clean_document(record, directory):
    original = (directory / "content.md").read_text(encoding="utf-8")
    if record.get("format") == "jats":
        from jats import article_text
        return article_text((directory / "source.xml").read_bytes()), "repository_jats_full_text_tables_and_captions"
    if record.get("format") == "pdf":
        if record["id"] != "sda-exercise-fuel-pdf":
            raise ValueError("New PDF requires verified reading-order adapter")
        import pymupdf
        pages = []
        with pymupdf.open(directory / "source.pdf") as pdf:
            for index, page in enumerate(pdf):
                blocks = page.get_text("blocks")
                ordered, omitted = [], []
                # This selected factsheet was visually checked: two columns on
                # all four pages. Preserve block coordinates and unmodified PDF.
                for block in blocks:
                    x0, y0, x1, y1, text = block[:5]
                    if not text.strip():
                        continue
                    item = {"bbox": [x0, y0, x1, y1], "text": text}
                    if y0 > 810 or (index == 0 and y0 < 310):
                        item["reason"] = "repeated_footer_or_title"
                        omitted.append(item)
                    elif index == 2 and x0 > 300 and y0 > 400:
                        item["reason"] = "illustration_has_overlapping_hidden_text_keep_pdf_for_review"
                        omitted.append(item)
                    else:
                        item["column"] = 0 if x0 < 300 else 1
                        ordered.append(item)
                ordered.sort(key=lambda item: (item["column"], item["bbox"][1]))
                pages.append({"page": index + 1, "ordered_blocks": ordered, "omitted_blocks": omitted})
        (directory / "reading_order.json").write_text(json.dumps(pages, ensure_ascii=False, indent=2) + "\n")
        text = "# " + record["title"] + "\n\n" + "\n\n".join(
            "## Page " + str(p["page"]) + "\n\n" + "\n\n".join(b["text"].strip() for b in p["ordered_blocks"]) for p in pages)
        return text.strip() + "\n", "pdf_visually_checked_two_columns_graphic_text_excluded"
    soup = BeautifulSoup((directory / "source.html").read_bytes(), "html.parser")
    root = None
    if record["id"].startswith("bda-"):
        root = soup.select_one("section.resource-detail")
    elif record["id"].startswith("chp-"):
        root = soup.select_one("#mainContent")
    elif record["id"].startswith("nhs-"):
        root = soup.select_one("main article")
    elif record.get("body_selector"):
        root = soup.select_one(record["body_selector"])
        if root is None:
            raise ValueError("Configured article body not found: " + record["id"])
    elif record["id"] == "nhlbi-sleep-habits":
        root = soup.select_one("main article .field--name-field-component-sections")
    if root:
        for noisy in root.select(".resource-detail-meta, .resource-related, .related-resources, .language-switcher-language-url, .addtoany_list"):
            noisy.decompose()
        text = render(root, record.get("final_url", record["url"]))
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text.startswith("# "):
            text = "# " + record.get("html_title", record.get("title", record["id"])) + "\n\n" + text
        method = "anysearch_crosschecked_with_dom_tables"
    else:
        text = original
        method = "anysearch_cleaned"
    if record["id"].startswith("chp-healthy-eating"):
        title = "健康飲食" if record["language"] == "zh-Hant" else "Healthy Eating"
        match = re.search(r"(?m)^# " + re.escape(title) + r"\s*$", text)
        if match:
            text = text[match.start():]
        for marker in ["## Healthy Eating Topics", "## 健康飲食專題", "## 健康飲食主題", "## 健康飲食題目"]:
            if marker in text:
                text = text.split(marker)[0]
    for pattern in [r"(?m)^#### Related topics", r"(?m)^## Help us improve our website", r"(?m)^### Categories",
                    r"(?m)^\[返回\]", r"(?m)^\[回頁頂\]", r"(?m)^\[Back\]\(javascript:", r"(?m)^## Related information",
                    r"(?m)^## 相關資料", r"(?m)^\[Return to listing\]"]:
        match = re.search(pattern, text)
        if match:
            text = text[:match.start()]
    return text.strip() + "\n", method


def spans(text, limit=2600):
    """Paragraph/section-aware character spans; never split a table or a paragraph."""
    parts = list(re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", text))
    heading_path = []
    start, end, current_headings = None, None, []
    for part in parts:
        heading = re.match(r"(#{1,6}) (.+)", part.group())
        if heading:
            if start is not None:
                yield start, end, current_headings
                start = None
            level = len(heading[1])
            heading_path = heading_path[:level - 1] + [heading[2]]
        if start is not None and part.end() - start > limit:
            yield start, end, current_headings
            start = None
        if start is None:
            start = part.start()
            current_headings = list(heading_path)
        end = part.end()
    if start is not None:
        yield start, end, current_headings


def jsonl(path, values):
    path.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.root / "manifest.json").read_text())
    docs, chunks, failures, landing = [], [], [], []
    for record in manifest["documents"]:
        if record["status"] == "failed" or record.get("included") is False:
            failures.append({"id": record["id"], "url": record["url"], "reason": record.get("error", record.get("exclusion_reason"))})
            continue
        directory = args.root / "documents" / record["id"]
        if record.get("retrieval_enabled") is False:
            landing.append(record["id"])
            continue
        text, method = clean_document(record, directory)
        (directory / "clean.md").write_text(text, encoding="utf-8")
        headings = re.findall(r"(?m)^#{1,6} (.+)$", text)
        title = headings[0] if headings else record.get("title")
        # Page-provided dates stay raw; never infer publication from copyright years.
        dates = re.findall(r"(?:\(?Revised[^\n]*|Page last reviewed:[^\n]*|\([^\n]*\d{4}年[^\n]*修[訂定][^\n]*\))", text, re.I)
        document = {"id": record["id"], "document_version": digest(text), "title": title,
                    "source_url": record["url"], "final_url": record.get("final_url", record["url"]),
                    "publisher": record["publisher"], "author_reported": record.get("author_reported"),
                    "language": record["language"], "topics": record["topics"], "population": record["population"],
                    "fetched_at": record["fetched_at"], "published_at_reported": record.get("published_at_reported"),
                    "updated_at_reported": record.get("updated_at_reported"), "body_date_labels": dates,
                    "raw_sha256": record.get("raw_sha256"), "text": text, "cleaning_method": method,
                    "raw_file": str(Path("documents") / record["id"] / record["files"]["raw"]),
                    "clean_file": str(Path("documents") / record["id"] / "clean.md"),
                    "rights_status": record["rights_status"], "review_status": "technical_extraction_checked_not_dietitian_reviewed",
                    "publication_status": "staging", "embedding_status": "not_generated",
                    "source_type": record["source_type"], "page_count": record.get("page_count")}
        document["jurisdiction"] = record.get("jurisdiction", "HK" if record["id"].startswith("chp-") else "AU" if record["id"].startswith("sda-") else "UK")
        for field in ["doi", "pmcid", "limitations", "demo_scenarios", "license_reported", "identifiers_reported", "jats_inventory", "download_url"]:
            if field in record:
                document[field] = record[field]
        document["population_screening_required"] = True
        document["equivalence_group"] = record.get("equivalence_group", re.sub(r"-(en|tc)$", "", document["id"]))
        docs.append(document)
        for index, (start, end, path) in enumerate(spans(text)):
            body = text[start:end]
            if len(re.sub(r"[#\s]", "", body)) < 45:
                continue
            chunk = {"id": record["id"] + "-" + document["document_version"][:12] + "-" + str(index).zfill(3),
                     "document_id": record["id"], "document_version": document["document_version"],
                     "ordinal": index, "heading_path": path, "char_start": start, "char_end": end,
                     "text": body, "text_sha256": digest(body), "language": record["language"], "topics": record["topics"],
                     "embedding": None, "review_status": "pending_applicability_review", "quality_flags": [],
                     "parent_context_required": True}
            if len(body) > 4000:
                chunk["quality_flags"].append("LONG_ATOMIC_BLOCK_KEEP_CONTEXT")
            if any(re.search(r"(?i)source|reference|bibliograph", item) for item in path):
                chunk["quality_flags"].append("BIBLIOGRAPHY_NOT_STANDALONE_ADVICE")
            # Applicability travels with retrieved evidence, not only the parent.
            for field in ["population", "jurisdiction", "source_type", "limitations", "demo_scenarios", "equivalence_group"]:
                if field in document:
                    chunk[field] = document[field]
            if record.get("format") == "pdf":
                page = next((re.match(r"Page (\d+)", item) for item in path if re.match(r"Page (\d+)", item)), None)
                chunk["page"] = int(page[1]) if page else None
            chunks.append(chunk)
    jsonl(args.root / "documents.jsonl", docs)
    jsonl(args.root / "chunks.jsonl", chunks)
    summary = {"attempted_urls": len(manifest["documents"]), "captured_resources": len(docs) + len(landing),
               "staging_documents": len(docs), "independent_document_groups": len({d["equivalence_group"] for d in docs}),
               "chunks": len(chunks), "characters": sum(len(d["text"]) for d in docs), "landing_pages": landing,
               "excluded": failures, "embeddings_generated": 0, "database_imported": False,
               "rag_connected": False, "publishable_release": False}
    (args.root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
