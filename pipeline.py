import feedparser
import trafilatura
import time
import os
from openai import OpenAI
from supabase import create_client

import json
import sys

# Credentials come from environment variables (see .env.example).
# In GitHub Actions they are supplied as repository secrets.
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY]):
    sys.exit("Missing SUPABASE_URL, SUPABASE_KEY or OPENAI_API_KEY. See .env.example.")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

RSS_FEEDS = [
    ("BBC News", "http://feeds.bbci.co.uk/news/rss.xml"),
    ("The Guardian", "https://www.theguardian.com/uk/rss"),
    ("The Telegraph", "https://www.telegraph.co.uk/rss.xml"),
    ("The Independent", "https://www.independent.co.uk/news/rss"),
    ("Sky News", "https://feeds.skynews.com/feeds/rss/home.xml"),
    ("The Times", "https://www.thetimes.co.uk/rss/news"),
    ("Evening Standard", "https://www.standard.co.uk/rss"),
    ("The Mirror", "https://www.mirror.co.uk/news/rss.xml"),
    ("Daily Mail", "https://www.dailymail.co.uk/news/index.rss"),
    ("The Sun", "https://www.thesun.co.uk/news/feed/"),
    ("Reuters", "https://feeds.reuters.com/reuters/topNews"),
    ("Associated Press", "https://feeds.apnews.com/apnews/topnews"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("France 24", "https://www.france24.com/en/rss"),
    ("DW News", "https://rss.dw.com/rdf/rss-en-all"),
    ("Euronews", "https://www.euronews.com/rss"),
    ("New York Times", "https://rss.nytimes.com/services/xml/rss/nf/HomePage.xml"),
    ("Washington Post", "https://feeds.washingtonpost.com/rss/national"),
    ("CNN", "http://rss.cnn.com/rss/edition.rss"),
    ("Fox News", "https://moxie.foxnews.com/google-publisher/latest.xml"),
    ("NPR", "https://feeds.npr.org/1001/rss.xml"),
    ("The Hill", "https://thehill.com/feed/"),
    ("Politico", "https://www.politico.com/rss/politicopicks.xml"),
    ("Breitbart", "http://feeds.feedburner.com/breitbart"),
    ("Manchester Evening News", "https://www.manchestereveningnews.co.uk/news/rss.xml"),
    ("Birmingham Live", "https://www.birminghammail.co.uk/news/rss.xml"),
    ("Leeds Live", "https://www.leeds-live.co.uk/news/rss.xml"),
    ("Liverpool Echo", "https://www.liverpoolecho.co.uk/news/rss.xml"),
    ("Glasgow Live", "https://www.glasgowlive.co.uk/news/rss.xml"),
    ("Politics Home", "https://www.politicshome.com/rss/news"),
    ("Conservative Home", "https://www.conservativehome.com/feed"),
    ("Left Foot Forward", "https://leftfootforward.org/feed/"),
    ("Full Fact", "https://fullfact.org/feeds/"),
    ("Snopes", "https://www.snopes.com/feed/"),
    ("PolitiFact", "https://www.politifact.com/rss/all/"),
    ("Reuters Fact Check", "https://www.reuters.com/fact-check/feed"),
    ("Daily Express", "https://www.express.co.uk/news/rss"),
    ("GB News", "https://www.gbnews.com/feeds/news.rss"),
    ("Spectator", "https://www.spectator.co.uk/feed/"),
    ("New Statesman", "https://www.newstatesman.com/feed/"),
    ("Spiked Online", "https://www.spiked-online.com/feed/"),
    ("RT News", "https://www.rt.com/rss/news/"),
    ("The Canary", "https://thecanary.co/feed/"),
    ("Novara Media", "https://novaramedia.com/feed/"),
    ("InfoWars", "https://www.infowars.com/feed/custom_feed_rss"),
]

SKIP_KEYWORDS = [
    "review -", "review:", "reviewed:",
    "TV review", "film review", "book review",
    "opinion:", "opinion -", "comment:",
    "horoscope", "crossword", "puzzle",
    "laundry", "washing machine", "recipe",
    "deals", "discount", "voucher",
]

SKIP_SOURCES = [
    "Unherd",
    "The Canary",
    "Guido Fawkes",
    "The Federalist",
    "Zero Hedge",
    "Novara Media",
]

def should_skip_article(title, source_name=None):
    title_lower = title.lower()
    for keyword in SKIP_KEYWORDS:
        if keyword.lower() in title_lower:
            return True
    if source_name and source_name in SKIP_SOURCES:
        return True
    return False

def fetch_articles_from_feeds(feeds):
    articles = []
    for source_name, url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                published = entry.get("published", "")
                if title and link:
                    articles.append({
                        "title": title,
                        "url": link,
                        "source_name": source_name,
                        "published_at": published,
                        "body_text": None
                    })
        except Exception:
            pass
    return articles

def save_articles_to_supabase(articles):
    for article in articles:
        try:
            supabase.table("articles").upsert(
                article, on_conflict="url"
            ).execute()
        except Exception:
            pass

def extract_article_text(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            return trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False
            )
        return None
    except Exception:
        return None

def extract_and_update_recent_articles():
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")
    response = supabase.table("articles")\
        .select("id, url")\
        .is_("body_text", "null")\
        .gte("published_at", cutoff)\
        .limit(30)\
        .execute()
    for article in response.data:
        text = extract_article_text(article["url"])
        if text:
            supabase.table("articles").update(
                {"body_text": text}
            ).eq("id", article["id"]).execute()
        time.sleep(0.3)

def extract_claims(article_text, source_name, title):
    prompt = f"""You are a claim extraction specialist working for a fact-checking organisation.

Your job is to read a news article and extract every specific, verifiable factual claim it makes.

RULES:
- Extract only specific, assertable statements of fact that can be verified true or false
- Every claim must contain at least one specific detail: a name, number, date, place, or named organisation
- Reject any claim that is vague, interpretive, or unmeasurable
- Do not extract statements where the only source is the person making the claim about themselves or others
- A verifiable claim must be checkable against external evidence such as data, records, documents, or independent reporting
- Prioritise claims about events that happened, policies that were passed, numbers that were reported, or actions that were taken
- Do not extract background context or widely accepted facts
- Each claim must be self-contained and understandable without reading the article
- Each claim should be one clear sentence
- Extract between 3 and 8 claims per article
- Format your response as a numbered list and nothing else

ARTICLE TITLE: {title}
SOURCE: {source_name}

ARTICLE TEXT:
{article_text[:3000]}

Extract the key factual claims from this article now:"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=500
        )
        raw_output = response.choices[0].message.content
        claims = []
        for line in raw_output.strip().split("\n"):
            line = line.strip()
            if line and line[0].isdigit():
                claim_text = line.split(".", 1)[-1].strip()
                if claim_text:
                    claims.append(claim_text)
        return claims
    except Exception:
        return []

def save_claims_to_supabase(article_id, source_name, article_title, claims, published_at=None):
    existing = supabase.table("claims")\
        .select("claim_text")\
        .eq("article_id", article_id)\
        .execute()
    existing_texts = [r["claim_text"] for r in existing.data]
    for claim_text in claims:
        if claim_text not in existing_texts:
            try:
                supabase.table("claims").insert({
                    "article_id": article_id,
                    "claim_text": claim_text,
                    "source_name": source_name,
                    "article_title": article_title,
                    "published_at": published_at
                }).execute()
            except Exception:
                pass

def score_rhetoric_and_save(article_id, body_text):
    already = supabase.table("rhetorical_scores")\
        .select("id")\
        .eq("article_id", article_id)\
        .execute()
    if already.data:
        return

    prompt = f"""You are a structural analysis specialist. Score this article excerpt across four rhetorical dimensions.

Return ONLY a JSON object with exactly these four keys and integer scores from 1 to 5:
- emotional_intensity: 1=analytical tone, 5=sustained high-arousal language
- certainty_language: 1=heavily hedged, 5=highly declarative with no uncertainty
- attribution_clarity: 1=vague sourcing, 5=clear named sources throughout
- framing_cues: 1=balanced multiple perspectives, 5=single dominant narrative

ARTICLE TEXT:
{body_text[:2000]}

Return only the JSON object, nothing else:"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=100
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        scores = json.loads(raw)

        risk_score = round(
            (scores["emotional_intensity"] * 0.3) +
            (scores["certainty_language"] * 0.2) +
            ((5 - scores["attribution_clarity"]) * 0.3) +
            (scores["framing_cues"] * 0.2), 1
        )

        supabase.table("rhetorical_scores").insert({
            "article_id": article_id,
            "emotional_intensity": scores["emotional_intensity"],
            "certainty_language": scores["certainty_language"],
            "attribution_clarity": scores["attribution_clarity"],
            "framing_cues": scores["framing_cues"],
            "risk_score": risk_score
        }).execute()

    except Exception:
        pass

def process_recent_unextracted_articles(limit=60):
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")

    already_processed = supabase.table("claims")\
        .select("article_id")\
        .execute()
    processed_ids = list(set([r["article_id"] for r in already_processed.data]))

    response = supabase.table("articles")\
        .select("id, title, source_name, body_text, published_at")\
        .not_.is_("body_text", "null")\
        .gte("published_at", cutoff)\
        .order("published_at", desc=True)\
        .execute()

    all_articles = response.data
    unprocessed = [a for a in all_articles if a["id"] not in processed_ids]
    unprocessed = [a for a in unprocessed if not should_skip_article(a["title"], a["source_name"])]
    to_process = unprocessed[:limit]

    for article in to_process:
        claims = extract_claims(
            article["body_text"],
            article["source_name"],
            article["title"]
        )
        if claims:
            save_claims_to_supabase(
                article["id"],
                article["source_name"],
                article["title"],
                claims,
                published_at=article.get("published_at")
            )
        time.sleep(0.5)
        if article.get("body_text"):
            score_rhetoric_and_save(article["id"], article["body_text"])

def run_pipeline():
    print("Pipeline started...")
    articles = fetch_articles_from_feeds(RSS_FEEDS)
    print(f"Fetched {len(articles)} articles")
    save_articles_to_supabase(articles)
    print("Saved to database")
    extract_and_update_recent_articles()
    print("Text extraction complete")
    process_recent_unextracted_articles(limit=25)
    print("Claim extraction complete")
    print("Pipeline complete.")

if __name__ == "__main__":
    run_pipeline()
