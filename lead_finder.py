"""Finds design-hiring posts on Reddit, saves them to Supabase, pings Telegram."""
import json
import os
import re
import time
import urllib.parse
import urllib.request

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PORTFOLIO_URL = os.environ.get("PORTFOLIO_URL", "[portfolio link]")

# ---------- Things you can edit ----------
SUBREDDITS = ["forhire", "DesignJobs", "graphic_design", "logodesign"]
MAX_ALERTS_PER_RUN = 10
LOOKBACK_HOURS = 24
USER_AGENT = "web:peters-lead-finder:v1.0 (personal freelance tool)"

DESIGN_TERMS = [
    "logo", "brand identity", "branding", "brand design", "visual identity",
    "graphic design", "graphic designer", "flyer", "poster", "business card",
    "social media design", "social media graphics", "packaging",
    "brand guidelines", "brand kit",
]
HIRING_TERMS = [
    "hiring", "looking for", "need a", "need an", "seeking", "commission",
    "paid", "budget", "quote", "freelance", "contract", "gig",
]
RUSH_TERMS = ["urgent", "asap", "rush", "today", "tomorrow", "24 hours", "48 hours"]

# Your rate sheet in USD (converted from your Naira prices)
PRICES = {
    "brand": ("Brand identity package", 109),
    "logo": ("Logo design", 36),
    "card": ("Business card design", 11),
    "flyer": ("Flyer / poster design", 11),
}
# -----------------------------------------


def has_any(text, terms):
    return any(re.search(r"\b" + re.escape(t) + r"s?\b", text) for t in terms)


def http(url, method="GET", headers=None, body=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def fetch_subreddit(sub):
    url = f"https://www.reddit.com/r/{sub}/new.json?limit=50&raw_json=1"
    data = http(url, headers={"User-Agent": USER_AGENT})
    return [child["data"] for child in data["data"]["children"]]


def is_lead(sub, post):
    title = (post.get("title") or "").lower()
    text = title + "\n" + (post.get("selftext") or "").lower()
    if sub.lower() == "forhire":
        if "[hiring]" not in title:
            return False
    else:
        if "for hire" in title:
            return False
        if not has_any(text, HIRING_TERMS):
            return False
    return has_any(text, DESIGN_TERMS)


def suggest(text):
    t = text.lower()
    if has_any(t, ["brand identity", "branding", "brand guidelines", "brand kit", "visual identity"]):
        key = "brand"
    elif has_any(t, ["logo"]):
        key = "logo"
    elif has_any(t, ["business card"]):
        key = "card"
    elif has_any(t, ["flyer", "poster", "social media design", "social media graphics"]):
        key = "flyer"
    else:
        return None, None, False
    item, price = PRICES[key]
    rush = has_any(t, RUSH_TERMS)
    if rush:
        price = round(price * 1.3)
    return item, price, rush


def build_pitch(item, price):
    if item:
        return (
            f"Hi! I saw your post and I'd love to help with your {item.lower()}. "
            "I'm Peter, a freelance designer specializing in logo design, branding "
            f"and graphic design. You can see my work here: {PORTFOLIO_URL}. "
            f"My rate for this is ${price}, delivered in 2 to 3 days. "
            "Happy to chat about your vision!"
        )
    return (
        "Hi! I saw your post and I'd love to help. I'm Peter, a freelance designer "
        "specializing in logo design, branding and graphic design. You can see my "
        f"work here: {PORTFOLIO_URL}. Rates depend on scope, and I'm happy to share "
        "a quote once I know more about your project."
    )


def supabase_headers():
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    if SUPABASE_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    return headers


def already_saved(link):
    q = urllib.parse.quote(link, safe="")
    url = f"{SUPABASE_URL}/rest/v1/leads?link=eq.{q}&select=id&limit=1"
    return bool(http(url, headers=supabase_headers()))


def save_lead(link, name, wants):
    headers = supabase_headers()
    headers["Prefer"] = "return=minimal"
    body = json.dumps({
        "source": "Reddit",
        "link": link,
        "name": name,
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
    failed = 0
    sent = 0

    for sub in SUBREDDITS:
        if sent >= MAX_ALERTS_PER_RUN:
            break
        try:
            posts = fetch_subreddit(sub)
        except Exception as err:
            print(f"r/{sub}: could not fetch ({err})")
            failed += 1
            continue
        time.sleep(2)

        for post in posts:
            if sent >= MAX_ALERTS_PER_RUN:
                break
            if post.get("stickied") or post.get("created_utc", 0) < cutoff:
                continue
            if not is_lead(sub, post):
                continue

            link = "https://www.reddit.com" + post["permalink"]
            if already_saved(link):
                continue

            title = post.get("title", "")
            body = (post.get("selftext") or "")[:400]
            author = "u/" + post.get("author", "unknown")
            save_lead(link, author, (title + "\n\n" + body).strip())

            item, price, rush = suggest(title + "\n" + body)
            quote = "Rates depend on scope"
            if item:
                quote = f"{item}: ${price}" + (" (includes 30% rush fee)" if rush else "")

            message = (
                f"New lead from r/{sub}\n\n{title}\n\n"
                f"Suggested quote: {quote}\n{link}\n\n"
                f"Pitch:\n{build_pitch(item, price)}"
            )
            try:
                telegram(message)
            except Exception as err:
                print(f"Telegram failed: {err}")
            sent += 1
            print(f"Saved and sent: {link}")

    print(f"Done. {sent} new lead(s).")
    if failed == len(SUBREDDITS):
        raise SystemExit("Every Reddit request failed, Reddit may be blocking this server.")


if __name__ == "__main__":
    main()
