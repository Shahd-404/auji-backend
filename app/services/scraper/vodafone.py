# FILE: app/services/scraper/vodafone.py
# ==============================================================
# AUJI – Vodafone Scraper + Autofill + Details (robust + keep-open)
#  - fetch_vodafone_jobs(url, category?) -> List[dict]
#  - scrape_and_save_vodafone_for_profiles(active_profiles?, pages=1) -> int
#  - autofill_vodafone_form(url, headless=True, config=?, keep_open=?, keep_open_seconds=?) -> bool
#  - autofill_vodafone_from_db(limit=5, category=None, headless=True) -> int
#  - fetch_vodafone_job_details(url) -> Dict[str, Any]
#  - enrich_vodafone_descriptions(limit=50) -> int        (اختياري لملء وصف/متطلبات الوظائف القديمة)
#  - create_driver(headless=True, detach=False)
# ==============================================================

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import os
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from pydantic import BaseModel
from sqlmodel import select

from app.db import get_session
from app.models import Job, SearchProfile


# ------------ constants ------------
BASE_HOST = "jobs.vodafone.com"
BASE_ORIGIN = f"https://{BASE_HOST}"
BASE_LIST_URL = f"{BASE_ORIGIN}/careers"


# ------------ profile canonicalization ------------
CANONICAL_NAME = {
    "Digital Marketing": "Digital Marketing",
    "تسويق رقمي": "Digital Marketing",

    "Machine Learning": "Machine Learning",
    "تعلم آلي": "Machine Learning",
    "مهندس تعلم آلي": "Machine Learning",
    "مهندس برمجيات تعلم آلي": "Machine Learning",

    "Data Analysis": "Data Analysis",
    "محلل بيانات": "Data Analysis",
}

PROFILE_LINKS: Dict[str, List[str] | str] = {
    "Machine Learning": "https://jobs.vodafone.com/careers?domain=vodafone.com&query=Machine+Learning&start=0&pid=563018687504927&sort_by=solr",
    "Digital Marketing": "https://jobs.vodafone.com/careers?domain=vodafone.com&query=Digital+Marketing&start=0&pid=563018675569773&sort_by=solr",
    "Data Analysis": "https://jobs.vodafone.com/careers?domain=vodafone.com&query=Data+Analysis&start=0&pid=563018687504917&sort_by=solr",
}


# ------------ small helpers ------------

def _canonicalize(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    n = name.strip()
    return CANONICAL_NAME.get(n, n)


def _ensure_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def _txt(el) -> str:
    try:
        return (el.text or el.get_attribute("innerText") or "").strip()
    except Exception:
        return ""


def _first(container, selector):
    try:
        els = container.find_elements(By.CSS_SELECTOR, selector)
        return els[0] if els else None
    except Exception:
        return None


# ------------ posted_at parsing ------------

def parse_posted_at(text: Optional[str]) -> datetime:
    if not text:
        return datetime.utcnow()
    t = text.strip().lower()
    now = datetime.utcnow()
    try:
        if "day ago" in t:
            return now - timedelta(days=1)
        if "days ago" in t:
            days = int(re.search(r"(\d+)", t).group(1))
            return now - timedelta(days=days)
        if "hour ago" in t:
            return now - timedelta(hours=1)
        if "hours ago" in t:
            hours = int(re.search(r"(\d+)", t).group(1))
            return now - timedelta(hours=hours)
        if "minute ago" in t:
            return now - timedelta(minutes=1)
        if "minutes ago" in t:
            minutes = int(re.search(r"(\d+)", t).group(1))
            return now - timedelta(minutes=minutes)
        # fallback ISO
        return datetime.strptime(t, "%Y-%m-%d")
    except Exception:
        return now


# ------------ canonical apply URL ------------

def to_apply_url(url: Optional[str], default_domain: str = "vodafone.com") -> Optional[str]:
    if not url:
        return url
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or BASE_HOST
    query = parse_qs(parsed.query)
    domain = (query.get("domain") or [default_domain])[0]

    if "apply" in parsed.path:
        pid = (query.get("pid") or [None])[0]
        if pid:
            return f"{scheme}://{netloc}/careers/apply?pid={pid}&domain={domain}"
        m = re.search(r"pid=(\d+)", parsed.query or "")
        if m:
            return f"{scheme}://{netloc}/careers/apply?pid={m.group(1)}&domain={domain}"
        return f"{scheme}://{netloc}{parsed.path}?domain={domain}"

    m = re.search(r"/job/(\d+)", parsed.path or "")
    if m:
        pid = m.group(1)
        return f"{scheme}://{netloc}/careers/apply?pid={pid}&domain={domain}"

    return url


# ------------ build search URLs ------------

def _set_start_param(u: str, start: int) -> str:
    pr = urlparse(u)
    q = parse_qs(pr.query)
    q["start"] = [str(start)]
    new_q = urlencode({k: (v[0] if isinstance(v, list) else v) for k, v in q.items()})
    return urlunparse((pr.scheme, pr.netloc, pr.path, pr.params, new_q, pr.fragment))


def build_search_urls(active_profiles: Iterable[str], page: int = 1) -> List[Dict]:
    start = max(0, (page - 1) * 20)
    requests: List[Dict] = []
    for raw_name in active_profiles:
        key = _canonicalize(raw_name)
        if not key:
            continue
        urls = _ensure_list(PROFILE_LINKS.get(key, []))
        for u in urls:
            u2 = _set_start_param(u, start) if "start=" in u else u
            requests.append({"url": u2, "category": key, "page": page})
    print("[SCRAPER] Build URLs ->", requests)
    return requests


# ------------ driver factory (exported) ------------

def create_driver(headless: bool = True, detach: bool = False) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    else:
        options.add_argument("--disable-gpu")
        options.add_argument("--start-maximized")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if detach and not headless:
        options.add_experimental_option("detach", True)  # keep open
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


# ------------ requirements extraction helpers ------------

def _extract_requirements_text_from_doc(driver) -> str:
    """Reads a lot of visible text and returns cleaned requirements-like lines."""
    try:
        blocks = driver.find_elements(By.CSS_SELECTOR, "section, article, .phs-text, .content, .job")
        if not blocks:
            blocks = [driver.find_element(By.TAG_NAME, "body")]
        raw = "\n".join([(b.get_attribute("innerText") or b.text or "") for b in blocks])
        parts = [p.strip() for p in re.split(r"\n|•|\u2022|;|\t", raw) if p.strip()]
        bad = re.compile(r"(cookie|policy|reject|non[- ]?essential|partners)", re.I)
        clean = [p for p in parts if not bad.search(p)]
        return "\n".join(clean[:120])  # limit
    except Exception:
        return ""


def _open_new_tab_get_apply_and_desc(driver, detail_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Open detail page in new tab then get apply_url + requirements_text; switch back."""
    apply_url, req_text = None, None
    try:
        driver.execute_script("window.open(arguments[0], '_blank');", detail_url)
        driver.switch_to.window(driver.window_handles[-1])
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except Exception:
            pass
        time.sleep(0.4)

        # Apply button
        btn = _first(driver, 'a[href*="/careers/apply"]') or _first(driver, "a[role='button']")
        if btn:
            apply_url = btn.get_attribute("href")
            if apply_url and apply_url.startswith("/"):
                apply_url = BASE_ORIGIN + apply_url

        # Requirements text
        req_text = _extract_requirements_text_from_doc(driver) or None

    finally:
        try:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])
        except Exception:
            pass
    return detail_url, apply_url, req_text


# ------------ main fetch ------------

def fetch_vodafone_jobs(list_url: str, category: Optional[str] = None) -> List[Dict]:
    print(f"[SCRAPER] Fetching: {list_url} | category={category}")
    driver = create_driver(headless=True)
    jobs: List[Dict] = []

    try:
        driver.get(list_url)
        time.sleep(1.2)

        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".cardContainer-GcY1a, li.search-result-item, .job-card, [data-ph-at-id='job-title']")
                )
            )
        except Exception:
            for _ in range(5):
                driver.execute_script("window.scrollBy(0, 900);")
                time.sleep(0.5)

        cards = driver.find_elements(By.CSS_SELECTOR, ".cardContainer-GcY1a, li.search-result-item, .job-card")
        if not cards:
            cards = driver.find_elements(By.CSS_SELECTOR, "[data-ph-at-id='job-title'], a[href*='/careers/job/']")
        print(f"[SCRAPER] Found {len(cards)} cards")

        for card in cards:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", card)
            except Exception:
                pass
            time.sleep(0.05)

            # title
            title = None
            for sel in [
                '[data-ph-at-id="job-title"]',
                '[data-ph-id*="job-title"]',
                '[data-ph-id*="jobTitle"]',
                'a[aria-label]',
                'a[title]',
                '.job-title',
                'h3',
                'h2',
                'a.r-link',
            ]:
                el = _first(card, sel)
                if el:
                    title = (el.get_attribute("aria-label") or el.get_attribute("title") or _txt(el) or "").strip()
                    if len(title) >= 3:
                        break
            if not title:
                inner = card.get_attribute("innerText") or ""
                for line in [x.strip() for x in inner.splitlines()]:
                    if len(line) >= 3 and not re.search(r"\b(Apply|job id|posted|location)\b", line, re.I):
                        title = line
                        break
            title = title or "N/A"

            # location
            try:
                location_el = _first(card, "[data-ph-at-job-location]") or _first(card, ".fieldValue-3kEar") or _first(card, ".location")
                location = _txt(location_el)
            except Exception:
                location = None

            # posted text
            posted_text = None
            try:
                posted_el = (
                    _first(card, '[data-ph-at-id="job-posted"]')
                    or _first(card, "[data-ph-at-job-posted]")
                    or _first(card, ".subData-13Lm1")
                    or _first(card, ".posted, time")
                )
                posted_text = _txt(posted_el)
            except Exception:
                posted_text = None

            # links + description
            detail_url: Optional[str] = None
            apply_url: Optional[str] = None
            requirements_text: Optional[str] = None
            try:
                link_el = _first(card, "a.r-link") or _first(card, 'a[href*="/careers/job/"]') or _first(card, 'a[href*="/job/"]')
                detail_url = link_el.get_attribute("href") if link_el else None
                if detail_url and detail_url.startswith("/"):
                    detail_url = BASE_ORIGIN + detail_url
                if detail_url:
                    detail_url, apply_url, requirements_text = _open_new_tab_get_apply_and_desc(driver, detail_url)
                if not apply_url:
                    apply_url = detail_url
            except Exception:
                pass

            canonical_apply = to_apply_url(apply_url or detail_url)

            jobs.append(
                {
                    "title": title.strip(),
                    "company": "Vodafone",
                    "location": location,
                    # ✅ خزّن الوصف/المتطلبات هنا
                    "description": (requirements_text or None),
                    "detail_url": detail_url,
                    "apply_url": canonical_apply,
                    "url": canonical_apply,
                    "source": "vodafone",
                    "employment_type": "full_time",
                    "category": category,
                    "posted_at": parse_posted_at(posted_text),
                }
            )

        return jobs
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ------------ save to DB (tolerant to schema diffs) ------------

def _job_model_keys() -> set:
    keys = set()
    try:
        if hasattr(Job, "__fields__"):
            keys |= set(Job.__fields__.keys())
        if hasattr(Job, "model_fields"):
            keys |= set(Job.model_fields.keys())
    except Exception:
        pass
    return keys


def _filter_job_dict(d: Dict) -> Dict:
    allowed = _job_model_keys()
    if not allowed:
        return d
    return {k: v for k, v in d.items() if k in allowed}


def save_jobs_bulk(items: List[Dict]) -> int:
    saved = 0
    with next(get_session()) as db:
        for it in items:
            detail = it.get("detail_url")
            apply_ = it.get("apply_url") or it.get("url")

            exists = None
            if detail:
                exists = db.exec(select(Job).where(Job.detail_url == detail)).first()
            if not exists and apply_:
                exists = db.exec(select(Job).where(Job.apply_url == apply_)).first()
            if not exists and it.get("url"):
                exists = db.exec(select(Job).where(Job.url == it["url"])).first()

            if exists:
                new_title = it.get("title")
                if new_title and new_title != "N/A" and (not getattr(exists, "title", None) or getattr(exists, "title", None) == "N/A"):
                    exists.title = new_title
                for key in [
                    "company",
                    "location",
                    "apply_url",
                    "detail_url",
                    "category",
                    "source",
                    "employment_type",
                    "posted_at",
                    "description",   # ✅ مهم لعرض "اعرف التفاصيل"
                ]:
                    try:
                        val = it.get(key)
                        if val not in (None, ""):
                            setattr(exists, key, val)
                    except Exception:
                        pass
            else:
                payload = _filter_job_dict(it)
                try:
                    db.add(Job(**payload))
                except TypeError:
                    payload.pop("employment_type", None)
                    db.add(Job(**payload))
            saved += 1
        db.commit()
    return saved


# ------------ active profiles ------------

def get_active_profile_names() -> List[str]:
    names: List[str] = []
    with next(get_session()) as db:
        rows = db.exec(select(SearchProfile.name).where(SearchProfile.is_active == True)).all()
        for r in rows:
            names.append(r[0] if isinstance(r, (list, tuple)) else r)
    return names


# ------------ entrypoint for scraping ------------

def scrape_and_save_vodafone_for_profiles(
    active_profiles: Iterable[str] | None = None,
    pages: int = 1,
) -> int:
    profiles = list(active_profiles) if active_profiles else get_active_profile_names()
    if not profiles:
        profiles = ["Digital Marketing", "Data Analysis", "Machine Learning"]

    print(f"[SCRAPER] Profiles to run: {profiles}")

    all_jobs: List[Dict] = []
    for page in range(1, max(1, pages) + 1):
        reqs = build_search_urls(profiles, page=page)
        for r in reqs:
            print(f"[SCRAPER] GET URL: {r['url']}  [cat={r.get('category')}]")
            batch = fetch_vodafone_jobs(r["url"], category=r.get("category"))
            print(f"[SCRAPER] Got {len(batch)} jobs for cat={r.get('category')} (page={page})")
            all_jobs.extend(batch)

    saved = save_jobs_bulk(all_jobs)
    print(f"[SCRAPER] DONE. fetched={len(all_jobs)}, saved/updated={saved}")
    return saved


# alias
scrape_and_save_vodafone = scrape_and_save_vodafone_for_profiles


# ==============================================================
# ======================= AUTOFILL ==============================
# ==============================================================

# --- Compatibility shims (for older imports) ---
class Profile(BaseModel):
    title: Optional[str] = None
    preferred_name: Optional[str] = None
    phone_country: Optional[str] = None
    residence_country: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    gender: Optional[str] = None


class Files(BaseModel):
    cv_path: Optional[str] = None
    cover_letter_path: Optional[str] = None
    portfolio_path: Optional[str] = None
    graduation_path: Optional[str] = None


@dataclass
class AutofillConfig:
    cv: Optional[str] = None
    attachment1: Optional[str] = None
    attachment2: Optional[str] = None
    attachment3: Optional[str] = None

    title: str = "Mrs."
    preferred_name: str = "S. Elgayah"
    phone_country_query: str = "egy"
    country_of_residence: str = "Egypt"
    city: str = "Cairo"
    zip_code: str = "11311"
    gender: str = "Female"

    keep_open: bool = False
    keep_open_seconds: Optional[int] = None


def _scroll_into_view(driver, el):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    except Exception:
        pass


def _js_click(driver, el):
    try:
        driver.execute_script("arguments[0].click();", el)
        return True
    except Exception:
        try:
            el.click()
            return True
        except Exception:
            return False


def _fill_input_css(driver, wait, selector: str, value: str) -> bool:
    try:
        elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
        _scroll_into_view(driver, elem)
        try:
            elem.clear()
        except Exception:
            pass
        elem.send_keys(value)
        print(f"[AUTOFILL] Filled: {selector}")
        return True
    except Exception as e:
        print(f"[AUTOFILL] Failed to fill {selector}: {e}")
        return False


def _autofill_single(driver: webdriver.Chrome, url: str, cfg: AutofillConfig) -> bool:
    print(f"[AUTOFILL] Open: {url}")
    wait = WebDriverWait(driver, 20)

    driver.get(url)

    # cookies
    try:
        accept_btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept')]"))
        )
        _js_click(driver, accept_btn)
        print("[AUTOFILL] Cookies accepted")
    except Exception:
        print("[AUTOFILL] No cookie popup")

    # upload CV
    if cfg.cv:
        try:
            file_input = wait.until(
                EC.presence_of_element_located((By.XPATH, "//input[@type='file' and contains(@accept, '.pdf')]"))
            )
            file_input.send_keys(cfg.cv)
            print("[AUTOFILL] CV uploaded")
        except Exception as e:
            print("[AUTOFILL] Failed CV upload:", e)

    # agree privacy
    try:
        agree_btn = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'I Agree')]"))
        )
        _js_click(driver, agree_btn)
        print("[AUTOFILL] Clicked 'I Agree'")
    except Exception as e:
        print("[AUTOFILL] No/Failed 'I Agree':", e)

    # wait email filled
    try:
        wait.until(lambda d: d.find_element(By.CSS_SELECTOR, "#Profile_Information_email").get_attribute("value") != "")
        print("[AUTOFILL] Autofill complete (email present)")
    except Exception:
        print("[AUTOFILL] Email not auto-populated; continuing…")

    # title
    try:
        dropdown_input = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@placeholder='No selection']")))
        _scroll_into_view(driver, dropdown_input)
        _js_click(driver, dropdown_input)
        mrs_option = wait.until(EC.element_to_be_clickable((By.XPATH, f"//span[text()='{cfg.title}']")))
        _js_click(driver, mrs_option)
        print(f"[AUTOFILL] Title selected: {cfg.title}")
    except Exception as e:
        print("[AUTOFILL] Couldn't select title:", e)

    # preferred name
    _fill_input_css(driver, wait, "#Profile_Information_q_preferredName", cfg.preferred_name)

    # phone country
    try:
        country_code_input = wait.until(EC.element_to_be_clickable((By.ID, "Profile_Information_phone-country-code")))
        _scroll_into_view(driver, country_code_input)
        _js_click(driver, country_code_input)
        country_code_input.send_keys(cfg.phone_country_query)
        egypt_option = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(@title, 'Egypt')]")))
        _js_click(driver, egypt_option)
        print("[AUTOFILL] Egypt selected for phone country")
    except Exception as e:
        print("[AUTOFILL] Phone country select failed:", e)

    # residence country
    try:
        residence_input = wait.until(EC.element_to_be_clickable((By.ID, "input-11")))
        _scroll_into_view(driver, residence_input)
        _js_click(driver, residence_input)
        residence_input.send_keys(cfg.country_of_residence)
        residence_option = wait.until(EC.element_to_be_clickable((By.XPATH, f"//button[contains(., '{cfg.country_of_residence}')]")))
        _js_click(driver, residence_option)
        print("[AUTOFILL] Country of Residence set")
    except Exception as e:
        print("[AUTOFILL] Failed to set Country of Residence:", e)

    # city + zip
    _fill_input_css(driver, wait, "#Profile_Information_q_city", cfg.city)
    _fill_input_css(driver, wait, "#Profile_Information_q_zip", cfg.zip_code)

    # gender
    try:
        gender_input = wait.until(EC.element_to_be_clickable((By.ID, "input-14")))
        _scroll_into_view(driver, gender_input)
        _js_click(driver, gender_input)
        gender_input.send_keys(cfg.gender)
        female_option = wait.until(EC.element_to_be_clickable((By.XPATH, f"//button[contains(., '{cfg.gender}')]")))
        _js_click(driver, female_option)
        print(f"[AUTOFILL] Gender set: {cfg.gender}")
    except Exception as e:
        print("[AUTOFILL] Failed to set gender:", e)

    # recruiter radio
    try:
        radio = driver.find_element(By.XPATH, "//label[contains(., 'Any company recruiter worldwide')]/preceding-sibling::input")
        _js_click(driver, radio)
        print("[AUTOFILL] Recruiter radio selected")
    except Exception as e:
        print("[AUTOFILL] Recruiter radio failed:", e)

    # attachments
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        inputs = wait.until(
            EC.presence_of_all_elements_located(
                (By.XPATH, "//input[@type='file' and contains(@accept, '.doc,.docx,.pdf,.txt,.jpg,.jpeg,.png,.ppt,.pptx')]")
            )
        )
        paths = [cfg.attachment1, cfg.attachment2, cfg.attachment3]
        for idx, p in enumerate(paths):
            if not p:
                continue
            try:
                inputs[idx].send_keys(p)
                print(f"[AUTOFILL] Uploaded attachment[{idx}] -> {os.path.basename(p)}")
            except Exception as e:
                print(f"[AUTOFILL] Attach upload failed {idx}: {e}")
    except Exception as e:
        print("[AUTOFILL] File inputs not found:", e)

    # screenshot
    try:
        os.makedirs("screenshots", exist_ok=True)
        fname = f"screenshots/vodafone_{int(time.time())}.png"
        driver.save_screenshot(fname)
        print(f"[AUTOFILL] Screenshot saved -> {fname}")
    except Exception as e:
        print("[AUTOFILL] Screenshot failed:", e)

    return True


def autofill_vodafone_form(
    url: str,
    headless: bool = True,
    config: Optional[AutofillConfig] = None,
    *,
    keep_open: Optional[bool] = None,
    keep_open_seconds: Optional[int] = None,
) -> bool:
    cfg = config or AutofillConfig()
    if keep_open is not None:
        cfg.keep_open = keep_open
    if keep_open_seconds is not None:
        cfg.keep_open_seconds = keep_open_seconds

    driver = create_driver(headless=headless, detach=(cfg.keep_open and not headless))

    ok = False
    try:
        ok = _autofill_single(driver, url, cfg)
        if cfg.keep_open and not headless:
            if cfg.keep_open_seconds and cfg.keep_open_seconds > 0:
                time.sleep(int(cfg.keep_open_seconds))
            return ok
        return ok
    finally:
        if not (cfg.keep_open and not headless):
            try:
                driver.quit()
            except Exception:
                pass


def _iter_vodafone_apply_urls(limit: int = 5, category: Optional[str] = None) -> List[str]:
    urls: List[str] = []
    with next(get_session()) as db:
        q = select(Job.apply_url).where(Job.source == "vodafone").where(Job.apply_url.is_not(None))
        if category:
            q = q.where(Job.category == category)
        try:
            q = q.order_by(Job.posted_at.desc())
        except Exception:
            pass
        rows = db.exec(q).all()
        for r in rows:
            u = r[0] if isinstance(r, (list, tuple)) else r
            if u and u not in urls:
                urls.append(u)
            if len(urls) >= limit:
                break
    return urls


def autofill_vodafone_from_db(limit: int = 5, category: Optional[str] = None, headless: bool = True) -> int:
    urls = _iter_vodafone_apply_urls(limit=limit, category=category)
    print(f"[AUTOFILL] From DB -> {len(urls)} urls (limit={limit}, category={category})")
    ok_count = 0
    for u in urls:
        try:
            ok = autofill_vodafone_form(u, headless=headless, config=AutofillConfig(), keep_open=False)
            ok_count += 1 if ok else 0
        except Exception as e:
            print("[AUTOFILL] Error:", e)
    print(f"[AUTOFILL] DONE. ok={ok_count}/{len(urls)}")
    return ok_count


# ==============================================================
# ======================= DETAILS ===============================
# ==============================================================

def fetch_vodafone_job_details(url: str) -> Dict[str, Any]:
    """Scrape single Vodafone job detail page (title/location/posted/apply_link/requirements)."""
    driver = create_driver(headless=True)
    base_url = BASE_ORIGIN
    data: Dict[str, Any] = {
        "title": None,
        "location": None,
        "posted": None,
        "apply_link": None,
        "requirements": None,
        "detail_url": url,
        "source": "vodafone",
    }
    try:
        driver.get(url)
        wait = WebDriverWait(driver, 15)
        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except Exception:
            pass
        time.sleep(1.0)

        try:
            t = (
                _txt(_first(driver, '[data-ph-at-id="job-title"]'))
                or _txt(_first(driver, '[data-ph-id*="jobTitle"]'))
                or _txt(_first(driver, "h1"))
                or _txt(_first(driver, "h2"))
            )
            data["title"] = (t or "").strip() or None
        except Exception:
            pass

        try:
            loc_el = (
                _first(driver, "[data-ph-at-job-location]")
                or _first(driver, ".fieldValue-3kEar")
                or _first(driver, ".location")
            )
            data["location"] = _txt(loc_el) or None
        except Exception:
            pass

        try:
            posted_el = (
                _first(driver, '[data-ph-at-id="job-posted"]')
                or _first(driver, "[data-ph-at-job-posted]")
                or _first(driver, ".subData-13Lm1")
                or _first(driver, "time")
            )
            data["posted"] = _txt(posted_el) or None
        except Exception:
            pass

        try:
            btn = (
                _first(driver, 'a[href*="/careers/apply"]')
                or driver.find_element(By.XPATH, "//a[.//span[contains(.,'Apply')]]")
            )
            href = btn.get_attribute("href") if btn else None
            if href and href.startswith("/"):
                href = base_url + href
            data["apply_link"] = to_apply_url(href)
        except Exception:
            pass

        try:
            req_text = _extract_requirements_text_from_doc(driver)
            data["requirements"] = (req_text or "").strip() or None
        except Exception:
            data["requirements"] = None

        return data
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def enrich_vodafone_descriptions(limit: int = 50) -> int:
    """Fill description for existing Vodafone jobs that miss it."""
    filled = 0
    with next(get_session()) as db:
        q = select(Job).where(Job.source == "vodafone").where((Job.description.is_(None)) | (Job.description == ""))
        try:
            q = q.order_by(Job.posted_at.desc())
        except Exception:
            pass
        jobs = db.exec(q).all()[:limit]

        for j in jobs:
            detail = j.detail_url or j.apply_url or j.url
            if not detail:
                continue
            try:
                data = fetch_vodafone_job_details(detail)
                desc = (data.get("requirements") or "").strip()
                if desc:
                    j.description = desc
                    filled += 1
            except Exception as e:
                print("[DETAILS] error:", e)
        db.commit()
    print(f"[DETAILS] enriched {filled} jobs.")
    return filled


__all__ = [
    "create_driver",
    "fetch_vodafone_jobs",
    "scrape_and_save_vodafone_for_profiles",
    "autofill_vodafone_form",
    "autofill_vodafone_from_db",
    "AutofillConfig",
    "Profile",
    "Files",
    "fetch_vodafone_job_details",
    "enrich_vodafone_descriptions",
]
