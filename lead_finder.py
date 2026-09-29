"""Finds remote jobs from open job feeds, saves them to Supabase, pings Telegram."""
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PORTFOLIO_URL = os.environ.get("PORTFOLIO_URL", "[portfolio link]")

# ---------- Things you can edit ----------
LOOKBACK_HOURS = 72
MAX_ALERTS_PER_RUN = 10
USER_AGENT = "Mozilla/5.0 (compatible; peters-lead-finder/2.0; personal use)"

JOBICY_TAGS = [
    "graphic design", "logo", "brand",
    "ui design", "ux design", "product design",
    "video editor", "video editing",
    "automation", "ai integration", "chatbot",
    "digital marketing", "paid ads", "social media marketing",
]
HIMALAYAS_QUERIES = ["graphic design", "logo design", "flyer design", "social media design"]
REMOTEOK_TAGS = ["design", "video", "marketing"]
WWR_FEEDS = []  # removed: now requires a paid subscription to apply

DESIGN_TERMS = [
    "graphic designer", "graphic design", "brand designer", "brand identity",
    "branding", "logo", "visual designer", "visual identity", "packaging",
    "creative designer", "social media design", "social media graphics",
    "ui designer", "ux designer", "ui/ux", "product designer",
    "video editor", "video editing", "video production", "ad video",
    "automation specialist", "workflow automation", "ai automation",
    "chatbot", "digital marketer", "paid ads", "ad campaigns",
    "social media marketing",
]
# Jobs matching these always get sent before anything else
PRIORITY_TERMS = [
    "graphic designer", "graphic design", "logo", "flyer",
    "social media design", "social media graphics", "brand identity", "branding",
]
FREELANCE_TERMS = ["freelance", "contract", "contractor", "part-time", "part time"]
# -----------------------------------------


def has_any(text, terms):
    return any(re.search(r"\b" + re.escape(t) + r"s?\b", text) for t in terms)


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def http(url, method="GET", headers=None, body=None, raw=False):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    if raw:
        return text
    return json.loads(text) if text else None


def parse_iso(value):
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def fetch_jobicy(tag):
    query = urllib.parse.urlencode({"count": 50, "tag": tag})
    data = http(f"https://jobicy.com/api/v2/remote-jobs?{query}", headers={"User-Agent": USER_AGENT})
    jobs = []
    for j in (data or {}).get("jobs", []):
        jobs.append({
            "source": "Jobicy",
            "title": str(j.get("jobTitle", "")),
            "company": str(j.get("companyName", "")),
            "location": str(j.get("jobGeo", "")),
            "link": str(j.get("url", "")),
            "text": strip_html(j.get("jobExcerpt") or j.get("jobDescription") or ""),
            "ts": parse_iso(j.get("pubDate", "")),
        })
    return jobs


def fetch_remoteok(tag):
    url = f"https://remoteok.com/api?tag={urllib.parse.quote(tag)}"
    data = http(url, headers={"User-Agent": USER_AGENT})
    jobs = []
    for j in (data or []):
        if not isinstance(j, dict) or "id" not in j:
            continue
        jobs.append({
            "source": "RemoteOK",
            "title": str(j.get("position", "")),
            "company": str(j.get("company", "")),
            "location": str(j.get("location", "")),
            "link": str(j.get("url", "")),
            "text": strip_html(j.get("description", "")),
            "ts": j.get("epoch"),
        })
    return jobs


def fetch_himalayas(query):
    q = urllib.parse.urlencode({"q": query, "sort": "recent", "page": 1})
    data = http(f"https://himalayas.app/jobs/api/search?{q}", headers={"User-Agent": USER_AGENT})
    listing = data.get("jobs") if isinstance(data, dict) else data
    jobs = []
    for j in (listing or []):
        jobs.append({
            "source": "Himalayas",
            "title": str(j.get("title", "")),
            "company": str(j.get("companyName", "")),
            "location": ", ".join(j.get("locationRestrictions") or []) or "Worldwide",
            "link": str(j.get("applicationLink", "")),
            "text": strip_html(j.get("description", "") or ""),
            "ts": parse_iso(j.get("publishedAt", "")),
        })
    return jobs


def fetch_arbeitnow():
    data = http("https://www.arbeitnow.com/api/job-board-api", headers={"User-Agent": USER_AGENT})
    jobs = []
    for j in (data or {}).get("data", []):
        jobs.append({
            "source": "Arbeitnow",
            "title": str(j.get("title", "")),
            "company": str(j.get("company_name", "")),
            "location": str(j.get("location", "")) or ("Remote" if j.get("remote") else ""),
            "link": str(j.get("url", "")),
            "text": strip_html(j.get("description", "") or ""),
            "ts": j.get("created_at"),
        })
    return jobs


def fetch_wwr(feed_url):
    xml_text = http(feed_url, headers={"User-Agent": USER_AGENT}, raw=True)
    root = ET.fromstring(xml_text)
    jobs = []
    for item in root.iter("item"):
        full_title = (item.findtext("title") or "").strip()
        company, _, role = full_title.partition(": ")
        if not role:
            company, role = "", full_title
        ts = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                ts = parsedate_to_datetime(pub).timestamp()
            except Exception:
                ts = None
        jobs.append({
            "source": "We Work Remotely",
            "title": role,
            "company": company,
            "location": (item.findtext("region") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "text": strip_html(item.findtext("description") or ""),
            "ts": ts,
        })
    return jobs


def is_match(job):
    return has_any((job["title"] + " " + job["text"]).lower(), DESIGN_TERMS)


def supabase_headers():
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    if SUPABASE_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    return headers


def already_saved(link):
    q = urllib.parse.quote(link, safe="")
    url = f"{SUPABASE_URL}/rest/v1/leads?link=eq.{q}&select=id&limit=1"
    return bool(http(url, headers=supabase_headers()))


def save_lead(job):
    headers = supabase_headers()
    headers["Prefer"] = "return=minimal"
    wants = f"{job['title']}\nLocation: {job['location'] or 'not listed'}\n\n{job['text'][:400]}".strip()
    body = json.dumps({
        "source": job["source"],
        "link": job["link"],
        "name": job["company"] or job["source"],
        "what_client_wants": wants,
        "status": "new",
    }).encode("utf-8")
    http(f"{SUPABASE_URL}/rest/v1/leads", method="POST", headers=headers, body=body)


def telegram(text):
    body = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    http(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=body,
    )


def main():
    cutoff = time.time() - LOOKBACK_HOURS * 3600
    jobs = []
    searches_tried = 0
    searches_worked = 0

    for tag in JOBICY_TAGS:
        searches_tried += 1
        try:
            found = fetch_jobicy(tag)
            jobs += found
            searches_worked += 1
            print(f"Jobicy '{tag}': fetched {len(found)} job(s).")
        except Exception as err:
            print(f"Jobicy '{tag}': could not fetch ({err})")
        time.sleep(2)

    for feed in WWR_FEEDS:
        searches_tried += 1
        try:
            found = fetch_wwr(feed)
            jobs += found
            searches_worked += 1
            print(f"We Work Remotely: fetched {len(found)} job(s).")
        except Exception as err:
            print(f"We Work Remotely: could not fetch ({err})")
        time.sleep(2)

    for tag in REMOTEOK_TAGS:
        searches_tried += 1
        try:
            found = fetch_remoteok(tag)
            jobs += found
            searches_worked += 1
            print(f"RemoteOK '{tag}': fetched {len(found)} job(s).")
        except Exception as err:
            print(f"RemoteOK '{tag}': could not fetch ({err})")
        time.sleep(2)

    for query in HIMALAYAS_QUERIES:
        searches_tried += 1
        try:
            found = fetch_himalayas(query)
            jobs += found
            searches_worked += 1
            print(f"Himalayas '{query}': fetched {len(found)} job(s).")
        except Exception as err:
            print(f"Himalayas '{query}': could not fetch ({err})")
        time.sleep(2)

    searches_tried += 1
    try:
        found = fetch_arbeitnow()
        jobs += found
        searches_worked += 1
        print(f"Arbeitnow: fetched {len(found)} job(s).")
    except Exception as err:
        print(f"Arbeitnow: could not fetch ({err})")

    # Graphic design, logo, flyer, and social media design jobs go first
    jobs.sort(key=lambda j: 0 if has_any((j["title"] + " " + j["text"]).lower(), PRIORITY_TERMS) else 1)

    matched = 0
    seen = set()
    sent = 0
    for job in jobs:
        if sent >= MAX_ALERTS_PER_RUN:
            break
        link = job["link"]
        if not link or link in seen:
            continue
        seen.add(link)
        if job["ts"] is not None and job["ts"] < cutoff:
            continue
        if not is_match(job):
            continue
        matched += 1
        if already_saved(link):
            continue

        save_lead(job)

        flag = ""
        if has_any((job["title"] + " " + job["text"]).lower(), FREELANCE_TERMS):
            flag = " (freelance/contract)"
        message = (
            f"New job lead from {job['source']}\n\n"
            f"{job['title']}\n"
            f"{job['company'] or 'Company not listed'} | {job['location'] or 'location not listed'}{flag}\n"
            f"{link}\n\n"
            f"Portfolio to send: {PORTFOLIO_URL}"
        )
        try:
            telegram(message)
        except Exception as err:
            print(f"Telegram failed: {err}")
        sent += 1
        print(f"Saved and sent: {link}")

    print("")
    print("===== SEARCH SUMMARY =====")
    print(f"Searches run: {searches_worked} of {searches_tried} succeeded.")
    print(f"Jobs looked at: {len(jobs)}")
    print(f"Jobs matching your keywords: {matched}")
    print(f"New leads sent to Telegram this run: {sent}")
    print("===========================")
    if searches_worked == 0:
        raise SystemExit("Every job feed failed. They may be blocking this server.")


if __name__ == "__main__":
    main()
