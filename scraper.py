import os
import re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://ww7-fmovies.com/home/"
GROUP_NAME = "FM Movies"
OUTPUT_FILE = "fm_movies.m3u"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Mobile) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": BASE_URL,
}

M3U8_PATTERN = re.compile(r'https?://[^\s\'"<>\\]+\.m3u8[^\s\'"<>\\]*', re.IGNORECASE)
IFRAME_PATTERN = re.compile(r'<iframe[^>]+src=[\'"]([^\'"]+)[\'"]', re.IGNORECASE)


def fetch_html(url: str, referer: str = BASE_URL) -> str | None:
    try:
        resp = requests.get(
            url,
            headers={**HEADERS, "Referer": referer},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[WARN] Failed to fetch {url}: {e}")
        return None


def extract_m3u8_from_page(movie_url: str) -> str | None:
    html = fetch_html(movie_url, referer=BASE_URL)
    if not html:
        return None

    # 1. Direct m3u8 match in movie page HTML/JS
    match = M3U8_PATTERN.search(html)
    if match:
        return match.group(0).replace("\\/", "/")

    # 2. Check iframes / embed links inside page
    soup = BeautifulSoup(html, "html.parser")
    embed_urls = []

    for iframe in soup.find_all("iframe"):
        src = iframe.get("src") or iframe.get("data-src")
        if src:
            embed_urls.append(urljoin(movie_url, src))

    for raw_src in IFRAME_PATTERN.findall(html):
        embed_urls.append(urljoin(movie_url, raw_src))

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if any(k in href.lower() for k in ["embed", "player", "watch", "server", "stream"]):
            embed_urls.append(urljoin(movie_url, href))

    # Deduplicate while preserving order
    seen = set()
    unique_embeds = []
    for u in embed_urls:
        if u not in seen:
            seen.add(u)
            unique_embeds.append(u)

    for embed_url in unique_embeds[:5]:
        embed_html = fetch_html(embed_url, referer=movie_url)
        if not embed_html:
            continue
        m_embed = M3U8_PATTERN.search(embed_html)
        if m_embed:
            return m_embed.group(0).replace("\\/", "/")

    return None


def scrape_home_movies() -> list[dict]:
    html = fetch_html(BASE_URL, referer="https://ww7-fmovies.com/")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen_urls = set()

    # Movie cards usually wrap <a> and <img>
    for card in soup.select("a[href]"):
        img = card.find("img")
        if not img:
            continue

        href = urljoin(BASE_URL, card["href"].strip())
        if href in seen_urls or href.rstrip("/") == BASE_URL.rstrip("/"):
            continue

        title = (
            card.get("title")
            or img.get("alt")
            or card.get_text(" ", strip=True)
            or "Untitled Movie"
        ).strip()
        title = re.sub(r"\s+", " ", title)

        logo = (
            img.get("data-src")
            or img.get("data-lazy-src")
            or img.get("src")
            or ""
        ).strip()
        if logo:
            logo = urljoin(BASE_URL, logo)

        # Skip obvious nav/logo/icon links if needed
        if any(bad in href.lower() for bad in ["/genre/", "/year/", "/country/", "/dmca", "/contact"]):
            continue

        seen_urls.add(href)
        items.append({
            "title": title,
            "logo": logo,
            "href": href,
        })

    return items


def build_playlist():
    movies = scrape_home_movies()
    print(f"[INFO] Found {len(movies)} candidate items on home page.")

    lines = ["#EXTM3U"]
    added = 0

    for item in movies:
        stream_url = extract_m3u8_from_page(item["href"])
        if not stream_url:
            print(f"[SKIP] No m3u8 found for: {item['title']} ({item['href']})")
            continue

        safe_title = item["title"].replace('"', "'")
        extinf = (
            f'#EXTINF:-1 tvg-name="{safe_title}" '
            f'tvg-logo="{item["logo"]}" '
            f'group-title="{GROUP_NAME}",{safe_title}'
        )
        lines.append(extinf)
        lines.append(f'#EXTVLCOPT:http-referrer={item["href"]}')
        lines.append(stream_url)
        added += 1
        print(f"[OK] Added: {safe_title} -> {stream_url}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[DONE] Wrote {added} streams to {OUTPUT_FILE}")


if __name__ == "__main__":
    build_playlist()
