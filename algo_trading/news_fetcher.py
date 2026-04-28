import feedparser
from .logger import log

_RSS_FEEDS = [
    ('Moneycontrol', 'https://www.moneycontrol.com/rss/marketreports.xml'),
    ('ET Markets',   'https://economictimes.indiatimes.com/markets/rss.cms'),
]

def fetch_nifty_news(max_per_feed: int = 5) -> str:
    """
    Fetches latest market headlines from RSS feeds.
    Returns a formatted string of up to 10 unique headlines.
    No BeautifulSoup dependency — feedparser handles parsing.
    """
    headlines = []
    for source, url in _RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                title = entry.get('title', '').strip()
                if title:
                    headlines.append(f'[{source}] {title}')
        except Exception as e:
            log.warning(f'⚠️ Could not fetch {source} news: {e}')

    # Deduplicate preserving order
    seen = set()
    unique = []
    for h in headlines:
        if h not in seen:
            seen.add(h)
            unique.append(h)

    if not unique:
        return 'No recent news fetched.'

    return '\n'.join(f'{i+1}. {h}' for i, h in enumerate(unique[:10]))
