"""Normalize repository JATS full text, keeping sections, tables and provenance."""
import re
from bs4 import BeautifulSoup, NavigableString


def parse_article(raw):
    soup = BeautifulSoup(raw, "xml")
    article = soup.find("article")
    meta = article.find("article-meta") if article else None
    if not meta or not article.find("body") or not meta.find("article-title"):
        raise ValueError("JATS_FULL_ARTICLE_REQUIRED")
    return article, meta


def article_metadata(raw):
    article, meta = parse_article(raw)
    authors = []
    for contributor in meta.find_all("contrib", attrs={"contrib-type": "author"}):
        name = contributor.find("name")
        if name:
            authors.append(" ".join(name.find(t).get_text(" ", strip=True) for t in
                                    ["given-names", "surname"] if name.find(t)))
        elif contributor.find("collab"):
            authors.append(contributor.find("collab").get_text(" ", strip=True))
    dates = [{"type": d.get("pub-type", d.get("publication-format")),
              "year": d.year.get_text() if d.year else None,
              "month": d.month.get_text() if d.month else None,
              "day": d.day.get_text() if d.day else None}
             for d in meta.find_all("pub-date", recursive=False)]
    licenses = [{"url": d.get("xlink:href"), "text": d.get_text(" ", strip=True)}
                for d in meta.find_all("license")]
    identifiers = {d.get("pub-id-type"): d.get_text(strip=True)
                   for d in meta.find_all("article-id", recursive=False)}
    return {"title": meta.find("article-title").get_text(" ", strip=True),
            "author_reported": authors, "published_at_reported": dates,
            "updated_at_reported": None, "license_reported": licenses,
            "identifiers_reported": identifiers,
            "jats_inventory": {name: len(article.find_all(name)) for name in
                               ["sec", "table", "fig", "ref", "supplementary-material"]}}


def article_text(raw):
    # Import at call time to keep prepare -> jats -> prepare acyclic at import.
    from prepare import table_markdown
    article, meta = parse_article(raw)

    def render(node, depth=2):
        if isinstance(node, NavigableString):
            return re.sub(r"\s+", " ", str(node))
        if node.name in ["graphic", "inline-graphic"]:
            return " [Graphic retained by source; not interpreted] "
        if node.name == "table":
            return table_markdown(node)
        if node.name == "sec":
            return "\n\n" + "".join(render(c, depth + 1 if c.name == "sec" else depth)
                                     for c in node.children) + "\n\n"
        if node.name == "title":
            return "\n\n" + "#" * min(depth, 6) + " " + node.get_text(" ", strip=True) + "\n\n"
        if node.name == "alternatives":
            preferred = node.find("tex-math") or node.find("math") or node.find("table")
            return render(preferred, depth) if preferred else node.get_text(" ", strip=True)
        body = "".join(render(c, depth) for c in node.children)
        if node.name == "xref":
            return " [" + body.strip() + "] "
        if node.name in ["ext-link", "uri"]:
            href = node.get("xlink:href", "")
            return "[" + body.strip() + "](" + href + ")" if href.startswith(("http://", "https://")) else body
        if node.name in ["p", "abstract", "caption", "table-wrap", "table-wrap-foot", "fn", "fig", "ref", "supplementary-material"]:
            return "\n\n" + body.strip() + "\n\n"
        if node.name == "list-item":
            return "\n\n- " + body.strip() + "\n\n"
        if node.name == "label":
            return "\n\n" + body.strip() + "\n\n"
        return body

    text = "# " + meta.find("article-title").get_text(" ", strip=True) + "\n\n"
    for abstract in meta.find_all("abstract", recursive=False):
        # Repositories sometimes nest a duplicate Abstract heading; retain the
        # content once and keep its subsections under the abstract context.
        for title in abstract.find_all("title"):
            if title.get_text(strip=True).lower() == "abstract":
                title.decompose()
        text += "## Abstract\n\n" + render(abstract, 3)
    text += render(article.find("body"))
    for name, heading in [("back", "Back matter and References"), ("floats-group", "Tables and figure captions")]:
        for section in article.find_all(name, recursive=False):
            text += "\n\n## " + heading + "\n\n" + render(section, 3)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
