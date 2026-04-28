"""Source policy — deterministic evidence-type classification and ranking.

The brain's prompt asks it to prefer authoritative sources, but words alone
aren't enough. This module applies hard rules first based on domain, so the
extraction LLM gets a head start and ranking is reproducible.

Evidence types (each is distinct, not strictly ranked against each other):
  official_regulatory  — government, regulators, central banks, intergovernmental
  company_primary      — investor relations, earnings, official press
  market_news          — Reuters, Bloomberg, FT, WSJ, AP, BBC Business, etc.
  analyst_forecast     — McKinsey, Gartner, Forrester, etc. (opinion / projection)
  academic             — arxiv, pubmed, peer-reviewed journals
  general_web          — everything else
"""

from __future__ import annotations

from typing import List
from urllib.parse import urlparse

from deep_research.notebook import Source


# =============================================================================
# Domain → evidence type mapping
# =============================================================================

OFFICIAL_REGULATORY_DOMAINS = {
    # US government & regulators
    "sec.gov", "ftc.gov", "federalreserve.gov", "bls.gov", "census.gov",
    "irs.gov", "doj.gov", "whitehouse.gov", "congress.gov", "cbo.gov",
    # EU / UK / international
    "europa.eu", "ecb.europa.eu", "gov.uk", "hmrc.gov.uk", "fca.org.uk",
    "bankofengland.co.uk", "bis.org", "imf.org", "worldbank.org", "oecd.org",
    "wto.org", "un.org", "who.int",
}

COMPANY_PRIMARY_DOMAINS = {
    "investor.apple.com", "investors.google.com", "ir.microsoft.com",
    "investor.amazon.com", "ir.tesla.com", "investors.meta.com",
}

# IR/newsroom subdomains are matched by prefix pattern (see classify_domain)
COMPANY_PRIMARY_PATTERNS = ("investor.", "investors.", "ir.", "newsroom.", "press.")

MARKET_NEWS_DOMAINS = {
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com",
    "apnews.com", "bbc.co.uk", "bbc.com", "cnbc.com",
    "economist.com", "businessinsider.com", "marketwatch.com",
    "financialtimes.com", "nytimes.com", "theguardian.com",
    "axios.com", "politico.com",
    "techcrunch.com", "venturebeat.com", "wired.com", "theverge.com",
    "arstechnica.com", "zdnet.com", "cnet.com", "engadget.com",
    "technologyreview.mit.edu", "techradar.com", "theregister.com",
    "9to5mac.com", "macrumors.com", "androidcentral.com",
    "forbes.com", "fortune.com", "inc.com", "fastcompany.com",
    "hbr.org", "barrons.com", "investopedia.com",
    "seekingalpha.com", "thestreet.com",
    "handelsblatt.com", "lemonde.fr", "eleconomista.es",
    "nikkei.com", "scmp.com", "hindustantimes.com",
    "healthcareitnews.com", "modernhealthcare.com", "fierce-biotech.com",
    "energymonitor.ai", "pv-magazine.com", "windpowermonthly.com",
    "constructiondive.com", "supplychaindive.com",
}

ANALYST_FORECAST_DOMAINS = {
    "mckinsey.com", "gartner.com", "forrester.com", "idc.com",
    "deloitte.com", "pwc.com", "ey.com", "kpmg.com", "bcg.com",
    "bain.com", "accenture.com", "capgemini.com", "ihs.com",
    "morningstar.com", "spglobal.com", "moodys.com", "fitchratings.com",
    "statista.com", "grandviewresearch.com", "marketsandmarkets.com",
    "businessresearchcompany.com", "globenewswire.com", "prnewswire.com",
    "businesswire.com", "coresignal.com", "pitchbook.com", "crunchbase.com",
    "cbinsights.com", "wood-mackenzie.com",
    "goldmansachs.com", "jpmorgan.com", "morganstanley.com",
    "ubs.com", "barclays.com", "citigroup.com",
}

ACADEMIC_DOMAINS = {
    "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "scholar.google.com",
    "ssrn.com", "jstor.org", "springer.com", "nature.com",
    "science.org", "cell.com", "wiley.com", "elsevier.com",
    "researchgate.net", "semanticscholar.org", "ncbi.nlm.nih.gov",
    "ieeexplore.ieee.org", "dl.acm.org", "tandfonline.com",
    "cambridge.org", "oxford.ac.uk", "mit.edu", "stanford.edu",
    "papers.ssrn.com", "plos.org", "frontiersin.org",
    "biorxiv.org", "medrxiv.org", "hal.science",
}

# Domains that produce mostly noise — still indexed but scored low.
WEAK_SIGNALS = {
    "medium.com", "substack.com", "wordpress.com", "blogspot.com",
    "linkedin.com", "reddit.com", "quora.com", "yahoo.com",
    "tumblr.com", "hackernoon.com", "dev.to", "hashnode.com",
    "towardsdatascience.com", "levelup.gitconnected.com",
    "pinterest.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "tiktok.com", "youtube.com",
}


# =============================================================================
# Classification
# =============================================================================


def classify_domain(url: str) -> str:
    """Return evidence_type for a URL based on domain rules."""
    if not url:
        return "unknown"

    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        return "unknown"

    domain = hostname.removeprefix("www.")

    if domain in OFFICIAL_REGULATORY_DOMAINS or any(
        domain.endswith(f".{d}") for d in OFFICIAL_REGULATORY_DOMAINS
    ):
        return "official_regulatory"

    if domain in COMPANY_PRIMARY_DOMAINS:
        return "company_primary"

    if any(domain.startswith(p) for p in COMPANY_PRIMARY_PATTERNS):
        return "company_primary"

    if domain in MARKET_NEWS_DOMAINS or any(
        domain.endswith(f".{d}") for d in MARKET_NEWS_DOMAINS
    ):
        return "market_news"

    if domain in ANALYST_FORECAST_DOMAINS or any(
        domain.endswith(f".{d}") for d in ANALYST_FORECAST_DOMAINS
    ):
        return "analyst_forecast"

    if domain in ACADEMIC_DOMAINS or any(
        domain.endswith(f".{d}") for d in ACADEMIC_DOMAINS
    ):
        return "academic"

    return "general_web"


def is_weak_signal(url: str) -> bool:
    """Return True if the domain is a known low-quality signal."""
    try:
        domain = (urlparse(url).hostname or "").removeprefix("www.")
        return domain in WEAK_SIGNALS
    except Exception:
        return False


# =============================================================================
# Ranking — used to order search results before showing them to the brain
# =============================================================================

# Lower number = read first.
READ_PRIORITY: dict = {
    "official_regulatory": 1,
    "company_primary": 2,
    "market_news": 3,
    "academic": 4,
    "analyst_forecast": 5,
    "general_web": 6,
    "unknown": 7,
}


def rank_results(results: List[dict], seen_urls: set) -> List[dict]:
    """Re-rank search results: dedupe, then by evidence type, then by snippet length."""
    new_results: List[dict] = []
    seen_this_batch: set = set()
    for r in results:
        url = r.get("url", "")
        if url and (url in seen_urls or url in seen_this_batch):
            continue
        if url:
            seen_this_batch.add(url)
        evidence_type = classify_domain(url)
        snippet_len = len(r.get("snippet", r.get("content", "")))
        new_results.append({
            **r,
            "evidence_type": evidence_type,
            "_read_priority": READ_PRIORITY.get(evidence_type, 7),
            "_snippet_len": snippet_len,
            "_is_weak": is_weak_signal(url),
        })

    new_results.sort(
        key=lambda r: (r["_is_weak"], r["_read_priority"], -r["_snippet_len"])
    )

    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in new_results]


def enrich_source(result: dict) -> Source:
    """Build a Source from a raw search result dict."""
    url = result.get("url", "")
    try:
        domain = (urlparse(url).hostname or "").removeprefix("www.")
    except Exception:
        domain = ""

    date_str = result.get("date", result.get("last_updated", ""))
    if date_str:
        year_str = date_str[:4] if len(date_str) >= 4 else ""
        try:
            year = int(year_str)
            if year >= 2025:
                freshness = "current"
            elif year >= 2023:
                freshness = "recent"
            else:
                freshness = "outdated"
        except ValueError:
            freshness = "unknown"
    else:
        freshness = "unknown"

    return Source(
        url=url,
        title=result.get("title", ""),
        domain=domain,
        evidence_type=classify_domain(url),
        freshness=freshness,
        fetch_success=False,
    )
