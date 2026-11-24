import html
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://m.livetv.sx"
LIST_URL = f"{BASE_URL}/enx/"
ROOT_DIR = Path(__file__).resolve().parent
MY_IMG_DIR = ROOT_DIR / "myimg"
OUTPUT_M3U = ROOT_DIR / "live_events.m3u"
GROUP_NAME = "✨✦ʟɪᴠᴇ ᴇᴠᴇɴᴛꜱ✦✨"
FALLBACK_IMG_URL = (
    "https://img.magnific.com/free-vector/"
    "webinar-live-stream-broadcast-label-design_1017-59935.jpg"
)

# GitHub Actions mein GITHUB_REPOSITORY automatically set hota hai,
# local par chalate waqt yahan "username/repo" likh sakte hain.
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY", "YOUR_USERNAME/YOUR_REPO")
GITHUB_BRANCH = os.getenv("GITHUB_REF_NAME", "main")

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/",
}


def normalize_url(raw_url: str | None, base_url: str = BASE_URL) -> str | None:
    if not raw_url:
        return None
    cleaned = raw_url.strip()
    if not cleaned:
        return None
    if cleaned.startswith("//"):
        return "https:" + cleaned
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    return urljoin(base_url.rstrip("/") + "/", cleaned.lstrip("/"))


def is_youtube_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(
        domain in host
        for domain in ("youtube.com", "youtu.be", "youtube-nocookie.com")
    )


def fetch_html(url: str, referer: str | None = None, timeout: int = 20) -> str | None:
    headers = dict(DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code >= 400:
            return None
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except requests.RequestException:
        return None


def find_custom_icon(safe_name: str, base_stem: str) -> str | None:
    """myimg/ folder mein usi naam ki file check karta hai; mile toh raw URL return karta hai."""
    if not MY_IMG_DIR.exists() or not MY_IMG_DIR.is_dir():
        return None

    # Pehle exact filename check karein (jaise es.gif)
    exact_path = MY_IMG_DIR / safe_name
    if exact_path.is_file():
        return f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/myimg/{safe_name}"

    # Phir common extensions ke saath <stem> check karein (jaise es.png, es.jpg, es.webp)
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"):
        candidate = MY_IMG_DIR / f"{base_stem}{ext}"
        if candidate.is_file():
            return f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/myimg/{candidate.name}"

    return None


def resolve_icon_url(img_url: str) -> str:
    """Pehle myimg/ check karta hai, nahi mile toh fallback URL return karta hai."""
    if not img_url:
        return FALLBACK_IMG_URL

    parsed_name = os.path.basename(urlparse(img_url).path) or "icon.png"
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", parsed_name)
    base_stem = Path(safe_name).stem or "icon"

    custom_url = find_custom_icon(safe_name, base_stem)
    if custom_url:
        return custom_url

    return FALLBACK_IMG_URL


def get_live_events(list_html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(list_html, "html.parser")
    events: list[dict[str, str]] = []
    seen_hrefs: set[str] = set()

    for li in soup.select('ul#broadcasts li[data-search-item]'):
        live_span = li.select_one("span.live")
        if not live_span:
            continue

        title_div = li.select_one("div.title")
        link_tag = title_div.select_one("a[href]") if title_div else None
        img_tag = li.select_one("div.logo img[src]")

        raw_title = title_div.get_text(" ", strip=True) if title_div else ""
        title = re.sub(r"\s+", " ", html.unescape(raw_title)).strip()
        href = normalize_url(link_tag.get("href") if link_tag else None)
        img = normalize_url(img_tag.get("src") if img_tag else None) or ""

        if not title or not href:
            continue
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        events.append({
            "title": title,
            "href": href,
            "img": img,
        })

    return events


def get_event_embeds(event: dict[str, str]) -> list[str]:
    event_html = fetch_html(event["href"], referer=LIST_URL, timeout=15)
    if not event_html:
        return []

    soup = BeautifulSoup(event_html, "html.parser")
    anchors = soup.select("ul.sp-sources li.sp-source a.sp-source__link[href]")

    valid_embeds: list[str] = []
    for a in anchors:
        embed_url = normalize_url(a.get("href"))
        if not embed_url:
            continue
        if is_youtube_url(embed_url):
            continue
        if embed_url not in valid_embeds:
            valid_embeds.append(embed_url)

    return valid_embeds


def build_m3u() -> str:
    list_html = fetch_html(LIST_URL, referer=f"{BASE_URL}/", timeout=25)
    if not list_html:
        raise RuntimeError("Failed to fetch main broadcast list page.")

    live_events = get_live_events(list_html)
    results_by_index: dict[int, list[str]] = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_idx = {
            executor.submit(get_event_embeds, event): idx
            for idx, event in enumerate(live_events)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results_by_index[idx] = future.result()
            except Exception:
                results_by_index[idx] = []

    unique_imgs = {
        ev["img"] for idx, ev in enumerate(live_events)
        if results_by_index.get(idx)
    }
    logo_map: dict[str, str] = {}
    for src_img in unique_imgs:
        logo_map[src_img] = resolve_icon_url(src_img)

    m3u_lines = ["#EXTM3U"]

    for idx, event in enumerate(live_events):
        embeds = results_by_index.get(idx, [])
        if not embeds:
            continue

        final_logo = logo_map.get(event["img"], FALLBACK_IMG_URL).replace('"', "")
        logo_attr = f' tvg-logo="{final_logo}"' if final_logo else ""
        group_attr = f' group-title="{GROUP_NAME}"'

        for stream_num, embed_url in enumerate(embeds, start=1):
            channel_title = f"{event['title']} s-{stream_num}"
            m3u_lines.append(f"#EXTINF:-1{logo_attr}{group_attr},{channel_title}")
            m3u_lines.append(embed_url)

    return "\n".join(m3u_lines) + "\n"


def main() -> None:
    content = build_m3u()
    OUTPUT_M3U.write_text(content, encoding="utf-8")
    print(f"Saved M3U playlist to {OUTPUT_M3U}")


if __name__ == "__main__":
    main()
