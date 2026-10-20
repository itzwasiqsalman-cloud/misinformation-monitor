import streamlit as st
import numpy as np
import time
import requests
from datetime import datetime, timedelta
from supabase import create_client
from sentence_transformers import SentenceTransformer

st.set_page_config(
    page_title="Claim Monitor",
    page_icon="🔍",
    layout="wide"
)

@st.cache_resource
def init_connections():
    supabase = create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"]
    )
    model = SentenceTransformer('all-MiniLM-L6-v2')
    return supabase, model

supabase, model = init_connections()

def cosine_similarity(vec1, vec2):
    return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

def parse_date(date_str):
    if not date_str:
        return None
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except:
            continue
    return None

def get_all_claims(hours=48):
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    all_claims = []
    page = 0
    page_size = 1000
    while True:
        response = supabase.table("claims")\
            .select("id, claim_text, source_name, article_title, article_id, published_at")\
            .gte("published_at", cutoff)\
            .range(page * page_size, (page + 1) * page_size - 1)\
            .execute()
        if not response.data:
            break
        all_claims.extend(response.data)
        if len(response.data) < page_size:
            break
        page += 1
    return all_claims

def get_rhetoric_for_articles(article_ids):
    scores_list = []
    for article_id in article_ids[:5]:
        try:
            response = supabase.table("rhetorical_scores")\
                .select("emotional_intensity, certainty_language, attribution_clarity, framing_cues, risk_score")\
                .eq("article_id", int(article_id))\
                .execute()
            if response.data:
                scores_list.append(response.data[0])
        except Exception:
            pass
    return scores_list

def get_fact_check(claim_text):
    try:
        cached = supabase.table("fact_check_cache")\
            .select("results")\
            .eq("claim_text", claim_text[:500])\
            .execute()
        if cached.data:
            results = cached.data[0]["results"]
            return results if results else None

        url = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
        params = {
            "query": claim_text[:200],
            "key": st.secrets["FACT_CHECK_API_KEY"],
            "pageSize": 3
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if "claims" not in data or not data["claims"]:
            results = None
        else:
            results = []
            for claim in data["claims"]:
                review = claim.get("claimReview", [{}])[0]
                results.append({
                    "claim": claim.get("text", ""),
                    "fact_checker": review.get("publisher", {}).get("name", "Unknown"),
                    "rating": review.get("textualRating", "Unknown"),
                    "url": review.get("url", ""),
                })

        try:
            supabase.table("fact_check_cache").insert({
                "claim_text": claim_text[:500],
                "results": results if results else []
            }).execute()
        except Exception:
            pass

        return results if results else None

    except Exception:
        return None

def get_cluster_fact_checks(cluster_claims_list):
    all_results = []
    for claim in cluster_claims_list[:5]:
        results = get_fact_check(claim["claim_text"])
        if results:
            all_results.extend(results)
        time.sleep(0.1)
    seen_urls = set()
    unique_results = []
    for r in all_results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            unique_results.append(r)
    return unique_results if unique_results else None

def cluster_claims(claims, embeddings, threshold=0.82):
    clusters = []
    assigned = set()
    for i in range(len(claims)):
        if i in assigned:
            continue
        cluster = [i]
        assigned.add(i)
        for j in range(i + 1, len(claims)):
            if j in assigned:
                continue
            sim = cosine_similarity(embeddings[i], embeddings[j])
            if sim >= threshold:
                cluster.append(j)
                assigned.add(j)
        if len(cluster) >= 2:
            clusters.append(cluster)
    clusters.sort(key=len, reverse=True)
    return clusters

def rank_clusters(clusters, all_claims, min_sources=2):
    ranked = []
    for cluster in clusters:
        cluster_claims_list = [all_claims[idx] for idx in cluster]
        sources = list(set([c["source_name"] for c in cluster_claims_list]))
        if len(sources) < min_sources:
            continue
        ranked.append({
            "size": len(cluster),
            "source_count": len(sources),
            "sources": sources,
            "claims": cluster_claims_list,
            "sample_claim": cluster_claims_list[0]["claim_text"]
        })
    ranked.sort(key=lambda x: (x["source_count"], x["size"]), reverse=True)
    return ranked

def get_article_dates(cluster_claims_list):
    article_ids = list(set([c["article_id"] for c in cluster_claims_list]))
    dates = []
    for article_id in article_ids:
        response = supabase.table("articles")\
            .select("published_at")\
            .eq("id", article_id)\
            .execute()
        if response.data and response.data[0]["published_at"]:
            parsed = parse_date(str(response.data[0]["published_at"]))
            if parsed:
                dates.append(parsed)
    return dates

def score_velocity(dates):
    if len(dates) >= 2:
        dates.sort()
        spread_hours = (dates[-1] - dates[0]).total_seconds() / 3600
        if spread_hours < 1:
            return 10, spread_hours, dates[0]
        elif spread_hours < 6:
            return 8, spread_hours, dates[0]
        elif spread_hours < 24:
            return 6, spread_hours, dates[0]
        elif spread_hours < 48:
            return 4, spread_hours, dates[0]
        else:
            return 2, spread_hours, dates[0]
    return 1, 0, None

def get_priority_label(score):
    if score >= 35:
        return "URGENT", "🔴"
    elif score >= 25:
        return "HIGH PRIORITY", "🟠"
    elif score >= 15:
        return "MONITOR", "🟡"
    else:
        return "LOW PRIORITY", "🟢"

def get_rhetoric_label(risk_score):
    if risk_score >= 4.0:
        return "HIGH RISK", "🔴"
    elif risk_score >= 3.0:
        return "MODERATE RISK", "🟠"
    else:
        return "LOW RISK", "🟢"

@st.cache_data(ttl=300)
def run_full_analysis(hours=48):
    all_claims = get_all_claims(hours=hours)

    if not all_claims:
        return [], 0, 0

    claim_texts = [c["claim_text"] for c in all_claims]
    embeddings = model.encode(claim_texts, batch_size=64)
    clusters = cluster_claims(all_claims, embeddings, threshold=0.82)
    ranked = rank_clusters(clusters, all_claims, min_sources=2)

    scored = []
    for cluster in ranked[:20]:
        dates = get_article_dates(cluster["claims"])
        velocity_score, spread_hours, earliest = score_velocity(dates)

        article_ids = list(set([c["article_id"] for c in cluster["claims"]]))
        rhetoric_scores = get_rhetoric_for_articles(article_ids)

        if rhetoric_scores:
            rhetoric = {
                "emotional_intensity": round(sum(s["emotional_intensity"] for s in rhetoric_scores) / len(rhetoric_scores), 1),
                "certainty_language": round(sum(s["certainty_language"] for s in rhetoric_scores) / len(rhetoric_scores), 1),
                "attribution_clarity": round(sum(s["attribution_clarity"] for s in rhetoric_scores) / len(rhetoric_scores), 1),
                "framing_cues": round(sum(s["framing_cues"] for s in rhetoric_scores) / len(rhetoric_scores), 1),
                "risk_score": round(sum(s["risk_score"] for s in rhetoric_scores) / len(rhetoric_scores), 1),
            }
        else:
            rhetoric = None

        fact_checks = get_cluster_fact_checks(cluster["claims"])

        final_score = (cluster["source_count"] * 3) + \
                      (cluster["size"] * 1) + \
                      velocity_score + \
                      (rhetoric["risk_score"] * 2 if rhetoric else 0)

        scored.append({
            **cluster,
            "velocity_score": velocity_score,
            "spread_hours": round(spread_hours, 1),
            "earliest_seen": earliest.strftime("%Y-%m-%d %H:%M") if earliest else "unknown",
            "rhetoric": rhetoric,
            "final_score": round(final_score, 1),
            "fact_checks": fact_checks
        })

    scored.sort(key=lambda x: x["final_score"], reverse=True)
    return scored, len(all_claims), len(ranked)

st.title("🔍 Claim Monitor")
st.caption("Real-time claim clustering and triage for fact-checkers — showing claims from selected time window")

col_filter1, col_filter2 = st.columns([1, 3])
with col_filter1:
    time_window = st.selectbox(
        "Time window",
        options=[24, 48, 72, 168],
        format_func=lambda x: f"Last {x} hours" if x < 168 else "Last 7 days",
        index=1
    )

with st.spinner("Running analysis — this takes 1 to 2 minutes on first load..."):
    clusters, total_claims, total_cross_source = run_full_analysis(hours=time_window)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Claims", f"{total_claims:,}")
col2.metric("Cross-source Clusters", total_cross_source)
col3.metric("Clusters Scored", len(clusters))
col4.metric("Last Updated", datetime.now().strftime("%H:%M"))

st.divider()

if not clusters:
    st.warning("No clusters found for this time window. Try a wider time range or check back after the next pipeline run.")
else:
    st.subheader("Priority Claim Clusters")
    st.caption("Ranked by source spread, velocity, and rhetorical risk")

    for i, cluster in enumerate(clusters):
        priority_label, priority_icon = get_priority_label(cluster["final_score"])

        with st.expander(
            f"{priority_icon} [{priority_label}] {cluster['sample_claim'][:100]}...",
            expanded=(i < 3)
        ):
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Sources", cluster["source_count"])
            col2.metric("Matching Claims", cluster["size"])
            col3.metric("Spread", f"{cluster['spread_hours']}h")
            col4.metric("Priority Score", cluster["final_score"])

            st.markdown(f"**First seen:** {cluster['earliest_seen']}")
            st.markdown(f"**Sources:** {', '.join(cluster['sources'])}")

            if cluster["rhetoric"]:
                r = cluster["rhetoric"]
                risk_label, risk_icon = get_rhetoric_label(r["risk_score"])
                st.markdown(f"**Rhetorical risk:** {risk_icon} {r['risk_score']}/5 ({risk_label})")
                rcol1, rcol2, rcol3, rcol4 = st.columns(4)
                rcol1.metric("Emotional", f"{r['emotional_intensity']}/5")
                rcol2.metric("Certainty", f"{r['certainty_language']}/5")
                rcol3.metric("Attribution", f"{r['attribution_clarity']}/5")
                rcol4.metric("Framing", f"{r['framing_cues']}/5")
            else:
                st.caption("Rhetorical scoring unavailable for this cluster")

            if cluster["fact_checks"]:
                st.markdown("**Previously fact-checked:**")
                for fc in cluster["fact_checks"]:
                    rating = fc["rating"].lower()
                    if rating in ["false", "incorrect", "misleading", "mostly false"]:
                        rating_icon = "🔴"
                    elif rating in ["true", "correct", "mostly true", "correct attribution"]:
                        rating_icon = "🟢"
                    else:
                        rating_icon = "🟡"
                    st.markdown(f"{rating_icon} **{fc['fact_checker']}** rated this **{fc['rating']}**")
                    if fc["url"]:
                        st.markdown(f"[View fact-check]({fc['url']})")
            else:
                st.caption("No previous fact-checks found for this cluster")

            st.markdown("**All claims in this cluster:**")
            for c in cluster["claims"]:
                st.markdown(f"- **[{c['source_name']}]** {c['claim_text']}")

st.divider()
st.caption(f"Claim Monitor — Built for fact-checkers | {datetime.now().strftime('%Y-%m-%d')}")
