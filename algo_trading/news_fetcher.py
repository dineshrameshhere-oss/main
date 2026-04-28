import requests
import feedparser
from bs4 import BeautifulSoup
from .logger import log

def fetch_nifty_news():
    """
    Fetches latest news regarding Nifty 50 from RSS feeds.
    Returns a formatted string of headlines.
    """
    headlines = []
    
    # 1. Moneycontrol RSS
    try:
        mc_url = "https://www.moneycontrol.com/rss/marketreports.xml"
        feed = feedparser.parse(mc_url)
        for entry in feed.entries[:5]:
            headlines.append(f"[Moneycontrol] {entry.title}")
    except Exception as e:
        log.warning(f"⚠️ Could not fetch Moneycontrol news: {e}")

    # 2. Economic Times RSS
    try:
        et_url = "https://economictimes.indiatimes.com/markets/rss.cms"
        feed = feedparser.parse(et_url)
        for entry in feed.entries[:5]:
            headlines.append(f"[ET Markets] {entry.title}")
    except Exception as e:
        log.warning(f"⚠️ Could not fetch ET news: {e}")
        
    # Deduplicate and format
    unique_headlines = list(set(headlines))
    
    if not unique_headlines:
        return "No recent news fetched."
        
    formatted = ""
    for i, hl in enumerate(unique_headlines[:15]):
        formatted += f"{i+1}. {hl}\n"
        
    return formatted
