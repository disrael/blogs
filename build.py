import os
import re
import time
import shutil
import hashlib
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

EXPORT_DIR = Path("medium-export")
CACHE_ASSETS_DIR = Path("assets")
DIST_DIR = Path("dist")
ASSETS_DIR = DIST_DIR / "assets"
POSTS_HTML_DIR = DIST_DIR / "posts"
RAW_MD_DIR = DIST_DIR / "raw"

SITE_URL = os.environ.get("SITE_URL", "https://disrael.github.io/blogs").rstrip("/")
BLOG_TITLE = "The Polis — David Israel"

MAX_CHARS = 8500
MIN_BODY_LENGTH = 600

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://medium.com/"
})


class MediumMarkdownConverter(MarkdownConverter):
    def convert_figure(self, el, text, *args, **kwargs):
        img = el.find("img")
        caption = el.find("figcaption")
        if img and img.get("src"):
            src = img["src"]
            alt = img.get("alt", "").strip()
            cap_text = caption.get_text(" ", strip=True) if caption else ""
            combined_alt = f"{alt} — {cap_text}".strip(" —") or "Image"
            res = f"\n\n![{combined_alt}]({src})\n"
            if cap_text:
                res += f"*{cap_text}*\n"
            return res + "\n"
        return text


def md(html, **options):
    return MediumMarkdownConverter(**options).convert(html)


def parse_medium_filename(filepath: Path):
    stem = filepath.stem
    if stem.startswith("draft_"):
        return None, None

    match = re.match(r"^(\d{4}-\d{2}-\d{2})_(.*)-[a-f0-9]{8,16}$", stem)
    if match:
        date_str, raw_slug = match.groups()
    else:
        date_str = "Unknown"
        raw_slug = stem

    slug = re.sub(r"[^a-z0-9]+", "-", raw_slug.lower()).strip("-")
    return date_str, slug


def get_candidate_urls(url: str) -> list[str]:
    urls = [url]
    if "cdn-images-1.medium.com/max/" in url:
        miro_url = url.replace(
            "https://cdn-images-1.medium.com/max/",
            "https://miro.medium.com/v2/resize:fit:"
        )
        urls.append(miro_url)
    return urls


def download_image(url: str, slug: str) -> str:
    if not url or not url.startswith("http"):
        return url

    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
    parsed = urlparse(url)
    ext = os.path.splitext(parsed.path)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"]:
        ext = ".png"

    filename = f"{slug}-{url_hash}{ext}"
    cached_path = CACHE_ASSETS_DIR / filename
    dist_path = ASSETS_DIR / filename

    if not cached_path.exists():
        downloaded = False
        for attempt in range(1, 5):
            for candidate_url in get_candidate_urls(url):
                try:
                    time.sleep(1.25)
                    resp = SESSION.get(candidate_url, timeout=20)
                    if resp.status_code == 200 and resp.content:
                        cached_path.write_bytes(resp.content)
                        print(f"  [+] Downloaded: {filename}")
                        downloaded = True
                        break
                    elif resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", 8 * attempt))
                        print(f"  [~] Rate limited (429), sleeping {retry_after}s...")
                        time.sleep(retry_after)
                except Exception as e:
                    print(f"  [!] Error downloading {candidate_url}: {e}")
            if downloaded:
                break

    if cached_path.exists() and not dist_path.exists():
        shutil.copy2(cached_path, dist_path)

    return f"{SITE_URL}/assets/{filename}"


def split_oversized_block(block: str, budget: int) -> list[str]:
    lines = block.split("\n")
    chunks, cur, cur_len = [], [], 0
    for line in lines:
        if cur_len + len(line) + 1 > budget and cur:
            chunks.append("\n".join(cur))
            cur, cur_len = [line], len(line) + 1
        else:
            cur.append(line)
            cur_len += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def chunk_markdown(title: str, date_str: str, slug: str, full_md: str) -> list[str]:
    paragraphs = full_md.split("\n\n")
    raw_chunks = []
    current_chunk = []
    current_len = 0
    budget = MAX_CHARS - 500

    for para in paragraphs:
        if len(para) > budget:
            if current_chunk:
                raw_chunks.append("\n\n".join(current_chunk))
                current_chunk, current_len = [], 0
            raw_chunks.extend(split_oversized_block(para, budget))
            continue

        para_len = len(para) + 2
        if current_len + para_len > budget and current_chunk:
            raw_chunks.append("\n\n".join(current_chunk))
            current_chunk = [para]
            current_len = para_len
        else:
            current_chunk.append(para)
            current_len += para_len

    if current_chunk:
        raw_chunks.append("\n\n".join(current_chunk))

    total_parts = len(raw_chunks)
    chunk_urls = []

    for idx, body in enumerate(raw_chunks, start=1):
        filename = f"{slug}.md" if total_parts == 1 else f"{slug}.part{idx}.md"
        url = f"{SITE_URL}/raw/{filename}"
        chunk_urls.append(url)

        header = [
            f"# {title} (Part {idx} of {total_parts})" if total_parts > 1 else f"# {title}",
            f"Date: {date_str}",
        ]
        if idx < total_parts:
            header.append(f"Next Part: {SITE_URL}/raw/{slug}.part{idx + 1}.md")
        if idx > 1:
            header.append(f"Previous Part: {SITE_URL}/raw/{slug}.part{idx - 1}.md")

        footer = []
        if idx < total_parts:
            footer.append(f"---\n**[Continue reading Part {idx + 1} of {total_parts}]({SITE_URL}/raw/{slug}.part{idx + 1}.md)**")

        final_text = "\n".join(header) + "\n\n---\n\n" + body + ("\n\n" + "\n".join(footer) if footer else "")
        (RAW_MD_DIR / filename).write_text(final_text, encoding="utf-8")

    return chunk_urls


def build_html_page(title: str, content_html: str, date_str: str = "", chunk_urls: list[str] = None) -> str:
    raw_links = ""
    if chunk_urls:
        links_html = " | ".join(
            [f'<a href="{u}">Part {i+1} (.md)</a>' for i, u in enumerate(chunk_urls)]
        )
        raw_links = f'<p class="meta">Date: {date_str} &bull; Raw AI/Markdown: {links_html}</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ max-width: 720px; margin: 2rem auto; padding: 0 1rem; font-family: system-ui, -apple-system, sans-serif; line-height: 1.65; color: #1a1a1a; }}
  img {{ max-width: 100%; height: auto; border-radius: 4px; }}
  figure {{ margin: 1.5rem 0; }}
  figcaption {{ font-size: 0.9rem; color: #555; text-align: center; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1.5rem 0; }}
  th, td {{ border: 1px solid #ccc; padding: 0.5rem; text-align: left; }}
  blockquote {{ border-left: 3px solid #444; margin: 1.5rem 0; padding-left: 1rem; color: #444; }}
  .meta {{ font-size: 0.9rem; color: #555; background: #f4f4f4; padding: 0.5rem 0.75rem; border-radius: 4px; }}
  a {{ color: #0056b3; }}
</style>
</head>
<body>
<nav><a href="{SITE_URL}/">&larr; All Essays</a> | <a href="{SITE_URL}/llms.txt">llms.txt</a></nav>
<main>
{raw_links}
{content_html}
</main>
</body>
</html>"""


def main():
    CACHE_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    for d in [ASSETS_DIR, POSTS_HTML_DIR, RAW_MD_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    (DIST_DIR / ".nojekyll").write_text("", encoding="utf-8")

    # Automatically copy any root Google/Bing verification HTML files into dist/
    for html_v in Path(".").glob("google*.html"):
        shutil.copy2(html_v, DIST_DIR / html_v.name)

    html_files = sorted(EXPORT_DIR.glob("**/*.html"), reverse=True)
    posts_index = []

    for filepath in html_files:
        date_str, slug = parse_medium_filename(filepath)
        if not slug:
            continue

        soup = BeautifulSoup(filepath.read_text(encoding="utf-8"), "html.parser")
        body_el = soup.find("section", {"data-field": "body"}) or soup.find("body")
        if not body_el:
            continue

        plain_text = body_el.get_text(" ", strip=True)
        if len(plain_text) < MIN_BODY_LENGTH:
            continue

        title_el = soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else slug.replace("-", " ").title()
        if title_el:
            title_el.decompose()

        print(f"Processing: {date_str} | {title}")

        for img in body_el.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if src:
                img["src"] = download_image(src, slug)

        markdown_body = md(str(body_el), heading_style="ATX").strip()
        markdown_body = re.sub(r"\n{3,}", "\n\n", markdown_body)

        chunk_urls = chunk_markdown(title, date_str, slug, markdown_body)

        html_out = build_html_page(
            title, f"<h1>{title}</h1>\n{str(body_el)}", date_str, chunk_urls
        )
        html_filename = f"{slug}.html"
        (POSTS_HTML_DIR / html_filename).write_text(html_out, encoding="utf-8")

        posts_index.append({
            "title": title,
            "date": date_str,
            "slug": slug,
            "html_url": f"{SITE_URL}/posts/{html_filename}",
            "chunk_urls": chunk_urls,
            "char_count": len(markdown_body)
        })

    # 1. Generate /llms.txt
    llms_lines = [
        f"# {BLOG_TITLE}",
        "",
        "> Analytical essays on political economy, hierarchy, automation, and history.",
        "> Every essay below is split into raw Markdown parts guaranteed to be <= 8,500 characters so AI web-browsing tools can read 100% of every post without truncation.",
        "",
        "## Essays",
    ]
    for p in posts_index:
        llms_lines.append(f"- **{p['title']}** ({p['date']}, {p['char_count']:,} chars)")
        for idx, c_url in enumerate(p["chunk_urls"], start=1):
            part_label = f"Part {idx}/{len(p['chunk_urls'])}" if len(p["chunk_urls"]) > 1 else "Full Markdown"
            llms_lines.append(f"  - [{part_label}]({c_url})")

    (DIST_DIR / "llms.txt").write_text("\n".join(llms_lines), encoding="utf-8")

    # 2. Generate /sitemap.xml
    sitemap_urls = [f"{SITE_URL}/", f"{SITE_URL}/llms.txt"]
    for p in posts_index:
        sitemap_urls.append(p["html_url"])
        sitemap_urls.extend(p["chunk_urls"])

    sitemap_xml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    for u in sitemap_urls:
        sitemap_xml.append(f"  <url><loc>{u}</loc></url>")
    sitemap_xml.append("</urlset>")
    (DIST_DIR / "sitemap.xml").write_text("\n".join(sitemap_xml), encoding="utf-8")

    # 3. Generate /robots.txt with Sitemap pointer
    robots_txt = f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n"
    (DIST_DIR / "robots.txt").write_text(robots_txt, encoding="utf-8")

    # 4. Generate /index.html
    index_items = []
    for p in posts_index:
        parts_str = " &bull; ".join(
            [f'<a href="{u}">MD Part {i+1}</a>' for i, u in enumerate(p["chunk_urls"])]
        )
        cc = f"{p['char_count']:,}"
        index_items.append(
            f'<li><strong><a href="{p["html_url"]}">{p["title"]}</a></strong> '
            f'<br><small>{p["date"]} ({cc} chars) &bull; {parts_str}</small></li>'
        )
    index_html = build_html_page(
        BLOG_TITLE,
        f"<h1>{BLOG_TITLE}</h1>\n<p>AI-readable index: <a href='{SITE_URL}/llms.txt'>/llms.txt</a></p>\n<ul>\n"
        + "\n".join(index_items)
        + "\n</ul>"
    )
    (DIST_DIR / "index.html").write_text(index_html, encoding="utf-8")
    print(f"\nDone! Built {len(posts_index)} essays into {DIST_DIR}/ with sitemap.xml.")


if __name__ == "__main__":
    main()
