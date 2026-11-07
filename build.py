import os
import re
import time
import shutil
import hashlib
from html import escape
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

EXPORT_DIR = Path("medium-export")
CACHE_ASSETS_DIR = Path("assets")
DIST_DIR = Path("dist")
ASSETS_DIR = DIST_DIR / "assets"
POSTS_HTML_DIR = DIST_DIR / "posts"

SITE_URL = "https://blog.uclude.com"
BLOG_TITLE = "The Polis — David Israel"

MAX_CHARS = 8500
MIN_BODY_LENGTH = 600

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://medium.com/"
})

ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}


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


def split_oversized_text(text: str, budget: int) -> list[str]:
    words = text.split(" ")
    chunks, cur, cur_len = [], [], 0
    for word in words:
        if cur_len + len(word) + 1 > budget and cur:
            chunks.append(f"<p>{escape(' '.join(cur))}</p>")
            cur, cur_len = [word], len(word)
        else:
            cur.append(word)
            cur_len += len(word) + 1
    if cur:
        chunks.append(f"<p>{escape(' '.join(cur))}</p>")
    return chunks


def extract_clean_html_chunks(body_el) -> list[str]:
    # Flatten Medium's nested section/div wrappers so content tags sit at top level
    for wrapper in body_el.find_all(["section", "div"]):
        wrapper.unwrap()

    # Strip Medium's noisy attributes (graf classes, hashes, data-* attributes)
    for tag in body_el.find_all(True):
        allowed = ALLOWED_ATTRS.get(tag.name, set())
        tag.attrs = {k: v for k, v in tag.attrs.items() if k in allowed}

    budget = MAX_CHARS - 500
    blocks = []

    for child in body_el.children:
        if getattr(child, "name", None):
            block_html = str(child).strip()
            if not block_html:
                continue
            if len(block_html) > budget and child.name != "figure":
                blocks.extend(split_oversized_text(child.get_text(" ", strip=True), budget - 10))
            else:
                blocks.append(block_html)
        else:
            text = str(child).strip()
            if text:
                blocks.extend(split_oversized_text(text, budget - 10))

    chunks = []
    current_chunk = []
    current_len = 0

    for block in blocks:
        block_len = len(block) + 1
        if current_len + block_len > budget and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [block]
            current_len = block_len
        else:
            current_chunk.append(block)
            current_len += block_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def build_html_page(
    title: str,
    content_html: str,
    date_str: str = "",
    part_urls: list[str] = None,
    current_part: int = 1,
    canonical_url: str = "",
) -> str:
    meta_bar = ""
    footer_nav = ""

    if date_str:
        if part_urls and len(part_urls) > 1:
            total = len(part_urls)
            links_html = " | ".join(
                [
                    f"<strong>Part {i+1}</strong>" if (i + 1) == current_part else f'<a href="{u}">Part {i+1}</a>'
                    for i, u in enumerate(part_urls)
                ]
            )
            meta_bar = f'<p class="meta">Date: {date_str} &bull; Part {current_part} of {total}: {links_html}</p>'

            nav_parts = []
            if current_part > 1:
                nav_parts.append(f'<a href="{part_urls[current_part - 2]}">&larr; Previous Part (Part {current_part - 1})</a>')
            if current_part < total:
                nav_parts.append(f'<strong><a href="{part_urls[current_part]}">Continue reading Part {current_part + 1} of {total} &rarr;</a></strong>')
            if nav_parts:
                footer_nav = f'\n<hr>\n<p class="meta">{" &bull; ".join(nav_parts)}</p>'
        else:
            meta_bar = f'<p class="meta">Date: {date_str}</p>'

    canonical_tag = f'\n<link rel="canonical" href="{canonical_url}">' if canonical_url else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>{canonical_tag}
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
<nav><a href="{SITE_URL}/">&larr; All Essays</a></nav>
<main>
{meta_bar}
{content_html}{footer_nav}
</main>
</body>
</html>"""


def main():
    CACHE_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    for d in [ASSETS_DIR, POSTS_HTML_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    (DIST_DIR / ".nojekyll").write_text("", encoding="utf-8")
    (DIST_DIR / "CNAME").write_text("blog.uclude.com", encoding="utf-8")

    # Copy ai-instructions.txt to dist/ if it exists at the workspace root
    instructions_file = Path("ai-instructions.txt")
    has_instructions = False
    if instructions_file.exists():
        shutil.copy2(instructions_file, DIST_DIR / "ai-instructions.txt")
        print("  [+] Copied: ai-instructions.txt")
        has_instructions = True

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

        html_chunks = extract_clean_html_chunks(body_el)
        total_parts = len(html_chunks)

        if total_parts == 1:
            part_filenames = [f"{slug}.html"]
        else:
            part_filenames = [f"{slug}-part-{idx}.html" for idx in range(1, total_parts + 1)]

        part_urls = [f"{SITE_URL}/posts/{fn}" for fn in part_filenames]

        for idx, (chunk_body, fn, part_url) in enumerate(zip(html_chunks, part_filenames, part_urls), start=1):
            page_title = f"{title} (Part {idx} of {total_parts})" if total_parts > 1 else title
            html_out = build_html_page(
                title=page_title,
                content_html=f"<h1>{page_title}</h1>\n{chunk_body}",
                date_str=date_str,
                part_urls=part_urls,
                current_part=idx,
                canonical_url=part_url,
            )
            (POSTS_HTML_DIR / fn).write_text(html_out, encoding="utf-8")

        posts_index.append({
            "title": title,
            "date": date_str,
            "slug": slug,
            "part_urls": part_urls,
            "char_count": len(plain_text),
        })

    # 1. Generate /sitemap.xml (HTML pages and instructions file only)
    sitemap_urls = [f"{SITE_URL}/"]
    if has_instructions:
         sitemap_urls.append(f"{SITE_URL}/ai-instructions.txt")
    for p in posts_index:
        sitemap_urls.extend(p["part_urls"])

    sitemap_xml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    for u in sitemap_urls:
        sitemap_xml.append(f"  <url><loc>{u}</loc></url>")
    sitemap_xml.append("</urlset>")
    (DIST_DIR / "sitemap.xml").write_text("\n".join(sitemap_xml), encoding="utf-8")

    # 2. Generate /robots.txt with Sitemap pointer
    robots_txt = f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n"
    (DIST_DIR / "robots.txt").write_text(robots_txt, encoding="utf-8")

    # 3. Generate /index.html
    index_items = []
    for p in posts_index:
        cc = f"{p['char_count']:,}"
        if len(p["part_urls"]) > 1:
            parts_str = " &bull; ".join(
                [f'<a href="{u}">Part {i+1}</a>' for i, u in enumerate(p["part_urls"])]
            )
            index_items.append(
                f'<li><strong><a href="{p["part_urls"][0]}">{p["title"]}</a></strong> '
                f'<br><small>{p["date"]} ({cc} chars) &bull; {parts_str}</small></li>'
            )
        else:
            index_items.append(
                f'<li><strong><a href="{p["part_urls"][0]}">{p["title"]}</a></strong> '
                f'<br><small>{p["date"]} ({cc} chars)</small></li>'
            )

    header_html = f"<h1>{BLOG_TITLE}</h1>"
    if has_instructions:
        header_html += f"\n<p>AI Instructions: <a href='{SITE_URL}/ai-instructions.txt'>ai-instructions.txt</a></p>"

    index_html = build_html_page(
        title=BLOG_TITLE,
        content_html=f"{header_html}\n<ul>\n" + "\n".join(index_items) + "\n</ul>",
        canonical_url=f"{SITE_URL}/",
    )
    (DIST_DIR / "index.html").write_text(index_html, encoding="utf-8")
    print(f"\nDone! Built {len(posts_index)} essays ({len(sitemap_urls) - (2 if has_instructions else 1)} HTML pages) into {DIST_DIR}/.")


if __name__ == "__main__":
    main()