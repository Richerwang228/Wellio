"""Collect the explicit source manifest. No DB, embeddings, or model execution.

Runtime: Python with requests and beautifulsoup4; AnySearch CLI is passed explicitly.
Raw and cleaned third-party content belongs in the ignored .data directory.
"""
import argparse
import concurrent.futures
import hashlib
import json
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "WellioKnowledgeCollector/1.0"
LOCKS = {}
LOCKS_GUARD = threading.Lock()
ROBOTS = {}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_extraction(text):
    # Keep the original external extraction separately; remove its wrapper only.
    if "\n---\n" in text:
        text = text.split("\n---\n", 1)[1]
    match = re.search(r"(?m)^# [^\n]+", text)
    if match:
        text = text[match.start():]
    stops = [r"(?m)^### You may also be interested in", r"(?m)^## Find a Nutrition Expert",
             r"(?m)^\[Back\]\(javascript:history", r"(?m)^## Related links\s*$",
             r"(?m)^## Related fact sheets\s*$", r"(?m)^\[Return to listing\]"]
    for pattern in stops:
        match = re.search(pattern, text)
        if match:
            text = text[:match.start()]
    text = re.sub(r"(?m)^> \*\*External page content.*\n?", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def metadata(html):
    soup = BeautifulSoup(html, "html.parser")
    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
            if tag and tag.get("content"):
                return tag["content"]
        return None
    title = soup.find("h1")
    title = title.get_text(" ", strip=True) if title else None
    if not title:
        title = soup.title.get_text(" ", strip=True) if soup.title else None
    tables = []
    for table in soup.find_all("table"):
        rows = [[cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"], recursive=False)]
                for row in table.find_all("tr")]
        rows = [row for row in rows if row]
        if rows:
            tables.append(rows)
    return {"html_title": title, "author_reported": meta("author", "article:author"),
            "published_at_reported": meta("article:published_time", "datePublished"),
            "updated_at_reported": meta("article:modified_time", "dateModified"),
            "html_language": soup.html.get("lang") if soup.html else None,
            "tables": tables}


def check_robots(url, output):
    host = urlparse(url).netloc
    if host not in ROBOTS:
        robots_url = "https://" + host + "/robots.txt"
        try:
            response = requests.get(robots_url, timeout=20, headers={"User-Agent": USER_AGENT})
            (output / "robots" / (host + ".txt")).write_bytes(response.content)
            parser = RobotFileParser()
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
            ROBOTS[host] = (response.status_code, parser)
        except requests.RequestException:
            ROBOTS[host] = (None, None)
    status, parser = ROBOTS[host]
    allowed = parser.can_fetch(USER_AGENT, url) if status == 200 else None
    return {"http_status": status, "allowed": allowed}


def collect_one(source, args):
    retrieval_url = source.get("download_url", source["url"])
    host = urlparse(retrieval_url).netloc
    with LOCKS_GUARD:
        lock = LOCKS.setdefault(host, threading.Lock())
    root = args.output / "documents" / source["id"]
    root.mkdir(parents=True, exist_ok=True)
    record_path = root / "metadata.json"
    if record_path.exists() and not args.refresh:
        saved = json.loads(record_path.read_text())
        if saved.get("status") in ["captured", "needs_review"]:
            return {**saved, **source}
    record = {**source, "fetched_at": datetime.now(timezone.utc).isoformat(),
              "status": "failed", "embedding_status": "not_generated", "files": {}}
    if source.get("included") is False and record_path.exists():
        saved = json.loads(record_path.read_text())
        return {**saved, **source}
    with lock:
        record["robots"] = check_robots(retrieval_url, args.output)
        if record["robots"]["allowed"] is False:
            record["error"] = "ROBOTS_DISALLOWED"
            dump(record_path, record)
            return record
        try:
            response = requests.get(retrieval_url, timeout=35, headers={"User-Agent": USER_AGENT})
            record.update(http_status=response.status_code, final_url=response.url,
                          content_type=response.headers.get("Content-Type"))
            response.raise_for_status()
            if len(response.content) > 25_000_000:
                raise ValueError("DOCUMENT_TOO_LARGE")
            if source.get("format") == "jats":
                from jats import article_metadata, article_text
                parsed = article_metadata(response.content)
                clean = article_text(response.content)
                (root / "source.xml").write_bytes(response.content)
                (root / "content.md").write_text(clean, encoding="utf-8")
                record.update(parsed)
                record.update(raw_sha256=sha(response.content), content_sha256=sha(clean.encode()),
                              characters=len(clean), status="captured", quality_flags=[],
                              files={"raw": "source.xml", "content": "content.md"})
                dump(record_path, record)
                time.sleep(0.5)
                return record
            if source.get("format") == "pdf":
                if not response.content.startswith(b"%PDF-"):
                    raise ValueError("INVALID_PDF_SIGNATURE")
                import pymupdf
                (root / "source.pdf").write_bytes(response.content)
                record["files"]["raw"] = "source.pdf"
                record["raw_sha256"] = sha(response.content)
                with pymupdf.open(stream=response.content, filetype="pdf") as pdf:
                    pages = [{"page": i + 1, "text": page.get_text(sort=True)} for i, page in enumerate(pdf)]
                    record["pdf_metadata_reported"] = pdf.metadata
                record["page_count"] = len(pages)
                record["title"] = source.get("title", "What to eat before, during and post exercise")
                record["author_reported"] = None
                record["published_at_reported"] = None
                record["updated_at_reported"] = None
                dump(root / "pages.json", pages)
                clean = "# " + record["title"] + "\n\n" + "\n\n".join("## Page " + str(p["page"]) + "\n\n" + p["text"] for p in pages)
                (root / "content.md").write_text(clean, encoding="utf-8")
                record["files"].update(content="content.md", pages="pages.json")
                record["content_sha256"] = sha(clean.encode())
                record["characters"] = len(clean)
                record["status"] = "captured" if all(len(p["text"].strip()) > 50 for p in pages) else "needs_review"
                record["quality_flags"] = [] if record["status"] == "captured" else ["PDF_TEXT_REVIEW_REQUIRED"]
                dump(record_path, record)
                return record
            if "html" not in response.headers.get("Content-Type", ""):
                raise ValueError("NON_HTML_SOURCE_REQUIRES_SEPARATE_EXTRACTION")
            (root / "source.html").write_bytes(response.content)
            record["files"]["raw"] = "source.html"
            record["raw_sha256"] = sha(response.content)
            parsed = metadata(response.content)
            tables = parsed.pop("tables")
            record.update(parsed)
            if tables:
                dump(root / "tables.json", tables)
                record["files"]["tables"] = "tables.json"
            result = subprocess.run(["node", str(args.anysearch_cli), "extract", source["url"]],
                                    capture_output=True, text=True, timeout=100)
            if result.returncode or "**Source**:" not in result.stdout:
                raise ValueError("ANYSEARCH_EXTRACTION_FAILED")
            (root / "extracted.md").write_text(result.stdout, encoding="utf-8")
            record["files"]["extraction"] = "extracted.md"
            clean = clean_extraction(result.stdout)
            (root / "content.md").write_text(clean, encoding="utf-8")
            record["files"]["content"] = "content.md"
            record["content_sha256"] = sha(clean.encode())
            record["characters"] = len(clean)
            record["words_approx"] = len(clean.split())
            record["headings"] = re.findall(r"(?m)^#{1,6} (.+)$", clean)
            record["title"] = record["headings"][0] if record["headings"] else record["html_title"]
            record["status"] = "captured" if len(clean) >= 500 else "needs_review"
            record["quality_flags"] = []
            if len(clean) < 500:
                record["quality_flags"].append("SHORT_BODY")
            if re.search(r"(?i)captcha|verify you are human|access denied", clean):
                record["quality_flags"].append("POSSIBLE_BLOCK_PAGE")
                record["status"] = "needs_review"
            if len(clean) > 45000:
                record["quality_flags"].append("CHECK_SERVICE_TRUNCATION")
                record["status"] = "needs_review"
        except (requests.RequestException, subprocess.SubprocessError, ValueError) as error:
            record["error"] = type(error).__name__ + ": " + str(error)[:200]
        dump(record_path, record)
        time.sleep(0.5)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("sources.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--anysearch-cli", type=Path, required=True)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "robots").mkdir(exist_ok=True)
    manifest = json.loads(args.manifest.read_text())
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(collect_one, source, args): source["id"] for source in manifest["sources"]}
        for future in concurrent.futures.as_completed(futures):
            record = future.result()
            results.append(record)
            print(record["id"], record["status"], record.get("characters", 0), flush=True)
    results.sort(key=lambda item: item["id"])
    dump(args.output / "manifest.json", {"collection": manifest["collection"], "documents": results})
    print(json.dumps({"selected": len(results), "captured": sum(r["status"] == "captured" for r in results),
                      "needs_review": sum(r["status"] == "needs_review" for r in results),
                      "failed": sum(r["status"] == "failed" for r in results)}), flush=True)


if __name__ == "__main__":
    main()
