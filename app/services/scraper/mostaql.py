# ==============================================================
# 📁 app/services/scraper/mostaql.py
# Scrape Mostaql listing pages and map results to AUJI profiles.
# ==============================================================

from __future__ import annotations
from typing import List, Dict, Iterable, Optional
from urllib.parse import urlencode
from datetime import datetime, timedelta
import re
import time
import os
import sys
import asyncio
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SOURCE_NAME = "mostaql"
HEADLESS = os.getenv("MOSTAQL_HEADLESS", "1") not in ("0", "false", "False")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
DEBUG_DIR = Path("logs/mostaql"); DEBUG_DIR.mkdir(parents=True, exist_ok=True)

# -------------------- Windows fix (Playwright + FastAPI) --------------------
def _ensure_windows_proactor():
    """Fix NotImplementedError on Windows by enforcing Proactor event loop."""
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass

# -------------------- choose categories string --------------------
def _profiles_to_categories(active_profiles: Iterable[str]) -> str:
    """
    - Marketing فقط → marketing
    - Marketing + (Data/Machine) → development,marketing
    - (Data/Machine) فقط → development
    """
    s = {p.strip().lower() for p in (active_profiles or [])}
    has_marketing = any("marketing" in x for x in s)
    has_data_or_ml = any(("data" in x) or ("machine" in x) for x in s)
    if has_marketing and has_data_or_ml:
        return "development,marketing"
    if has_marketing:
        return "marketing"
    if has_data_or_ml:
        return "development"
    return "marketing"

def _build_url(categories: str, page: int = 1) -> str:
    qs = {"category": categories, "sort": "latest"}
    if page > 1:
        qs["page"] = page
    return f"https://mostaql.com/projects?{urlencode(qs)}"

# -------------------- parse "منذ ..." -> datetime --------------------
_AR_NUM = { "٠":"0","١":"1","٢":"2","٣":"3","٤":"4","٥":"5","٦":"6","٧":"7","٨":"8","٩":"9" }
def _to_ascii_digits(s: str) -> str:
    for a, b in _AR_NUM.items():
        s = s.replace(a, b)
    return s

def parse_ar_posted(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    t = _to_ascii_digits(text.strip())
    now = datetime.utcnow()
    m = re.search(r"(\d+)\s*(?:دقيقة|دقائق)", t)
    if m: return now - timedelta(minutes=int(m.group(1)))
    m = re.search(r"(\d+)\s*(?:ساعة|ساعات)", t)
    if m: return now - timedelta(hours=int(m.group(1)))
    m = re.search(r"(\d+)\s*(?:يوم|أيام)", t)
    if m: return now - timedelta(days=int(m.group(1)))
    if "أسبوع" in t: return now - timedelta(days=7)
    return now

# -------------------- classify into AUJI profiles --------------------
ML_KW = ("machine learning","deep learning","pytorch","tensorflow","ml","ai","تعلم آلي","ذكاء اصطناعي","nlp","computer vision","رؤية حاسوبية")
DA_KW = ("data analysis","analyst","power bi","tableau","excel","sql","etl","dashboard","تحليل بيانات","محلل بيانات","باور بي آي","تابلو","لوحات تحكم")
MK_KW = ("marketing","تسويق","social","smm","seo","sem","ads","إعلانات","brand","branding","content","محتوى","copy","copywriting","إدارة صفحات","facebook","instagram","tiktok","حملات","campaign","manager social","digital")

def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)

def _classify_profile(title: str, active_profiles: Iterable[str], categories: str) -> str:
    """
    لو categories = marketing فقط → Digital Marketing
    لو categories = development فقط → ML/DA بالكلمات
    لو categories = development,marketing → فرق حسب الكلمات
    """
    t = (title or "").lower()
    act = set(active_profiles or [])

    if categories == "marketing":
        return "Digital Marketing"

    if categories == "development":
        if _contains_any(t, ML_KW) and "Machine Learning" in act:
            return "Machine Learning"
        if _contains_any(t, DA_KW) and "Data Analysis" in act:
            return "Data Analysis"
        if "Machine Learning" in act and "Data Analysis" not in act:
            return "Machine Learning"
        if "Data Analysis" in act and "Machine Learning" not in act:
            return "Data Analysis"
        return "Data Analysis"

    # development,marketing
    if _contains_any(t, MK_KW):
        return "Digital Marketing"
    if _contains_any(t, ML_KW) and "Machine Learning" in act:
        return "Machine Learning"
    if _contains_any(t, DA_KW) and "Data Analysis" in act:
        return "Data Analysis"
    if "Digital Marketing" in act:
        return "Digital Marketing"
    if "Machine Learning" in act and "Data Analysis" not in act:
        return "Machine Learning"
    if "Data Analysis" in act:
        return "Data Analysis"
    return "Digital Marketing"

# -------------------- extract from HTML --------------------
LINK_SELECTOR = 'div.card-title h2 a.anchor, h2 a[href*="/project/"], h3 a[href*="/project/"]'
META_UL_SELECTORS = (".project_meta", ".list-meta-items")

def _find_meta_ul(a_tag) -> Optional[BeautifulSoup]:
    node = a_tag
    for _ in range(4):
        if not node: break
        ul = None
        for cls in META_UL_SELECTORS:
            ul = node.find("ul", class_=cls.replace(".",""))
            if ul: return ul
        node = node.parent
    card = a_tag.find_parent(class_="card-title") or a_tag.find_parent(class_="card-title_wrapper")
    if card:
        for cls in META_UL_SELECTORS:
            ul = card.find("ul", class_=cls.replace(".",""))
            if ul: return ul
    return None

def _extract_jobs_from_html(html: str, categories: str, active_profiles: Iterable[str], debug_name: str) -> List[Dict]:
    soup = BeautifulSoup(html, "lxml")
    links = soup.select(LINK_SELECTOR)

    if not links:
        try:
            dump = DEBUG_DIR / f"{debug_name}.html"
            dump.write_text(html, encoding="utf-8")
            print(f"[MOSTAQL] DEBUG saved -> {dump}")
        except Exception:
            pass
        return []

    out: List[Dict] = []
    seen = set()

    for a in links:
        title = " ".join((a.get_text(" ", strip=True) or "").split())
        href = (a.get("href") or "").strip()
        if not title or not href:
            continue
        if href in seen:
            continue
        seen.add(href)
        if href.startswith("/"):
            href = "https://mostaql.com" + href

        ul = _find_meta_ul(a)
        owner, posted_text = None, None
        if ul:
            for li in ul.select("li"):
                txt = " ".join((li.get_text(" ", strip=True) or "").split())
                if not owner:
                    i_tag = li.find("i")
                    if i_tag and "fa-user" in " ".join(i_tag.get("class", [])):
                        bdi = li.find("bdi")
                        if bdi:
                            owner = bdi.get_text(strip=True)
                        else:
                            a_user = li.find("a", href=True)
                            owner = a_user.get_text(strip=True) if a_user else None
                    else:
                        a_user = li.find("a", href=True)
                        if a_user and "/u/" in a_user.get("href", ""):
                            owner = a_user.get_text(strip=True)
                if not posted_text and any(x in txt for x in ("منذ", "قبل", "دقيقة", "دقائق", "ساعة", "ساعات", "يوم", "أيام", "أسبوع")):
                    posted_text = txt

        posted_at = parse_ar_posted(posted_text)
        mapped_category = _classify_profile(title, active_profiles, categories)

        out.append({
            "title": title,
            "company": owner or "مستقل",
            "location": None,
            "description": None,
            "detail_url": href,
            "apply_url": href,
            "url": href,
            "source": SOURCE_NAME,
            "category": mapped_category,      # AUJI profile name
            "employment_type": "freelance",   # ✅ مهم للفلاتر
            "posted_at": posted_at,           # datetime (UTC)
        })
    return out

# -------------------- main scrape APIs --------------------
def scrape_mostaql(categories: str, pages: int = 2,
                   active_profiles: Iterable[str] | None = None) -> List[Dict]:
    _ensure_windows_proactor()
    results: List[Dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(user_agent=USER_AGENT, locale="ar-EG")
        page = context.new_page()

        for i in range(1, max(1, int(pages)) + 1):
            url = _build_url(categories, i)
            print(f"[MOSTAQL] Page {i}: {url}")

            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PWTimeout:
                pass
            for _ in range(6):
                try:
                    page.mouse.wheel(0, 1200)
                except Exception:
                    pass
                time.sleep(0.35)

            try:
                page.wait_for_selector(LINK_SELECTOR, timeout=8000)
            except PWTimeout:
                pass

            html = page.content()
            items = _extract_jobs_from_html(
                html, categories, active_profiles or [], debug_name=f"page{i}_{categories.replace(',','_')}"
            )
            print(f"[MOSTAQL] items on page {i}: {len(items)}")
            results.extend(items)
            time.sleep(0.6)

        browser.close()
    return results

def scrape_mostaql_for_profiles(active_profiles: Iterable[str], pages: int = 2) -> List[Dict]:
    cats = _profiles_to_categories(active_profiles or [])
    return scrape_mostaql(cats, pages=pages, active_profiles=active_profiles or [])
