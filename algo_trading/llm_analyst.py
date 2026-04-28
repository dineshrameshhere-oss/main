import os
import json
from google import genai
from google.genai import types
from .logger import log
from .config import GEMINI_MODEL

def get_genai_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.error("❌ GEMINI_API_KEY is missing!")
        return None
    return genai.Client(api_key=api_key)

def call_gemini(prompt: str, max_tokens=1024) -> str:
    client = get_genai_client()
    if not client:
        return "{}"
        
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=0.2, # Low temp for consistent JSON
                response_mime_type="application/json"
            )
        )
        return response.text
    except Exception as e:
        log.error(f"❌ Gemini API Error: {e}")
        return "{}"

def analyze_premarket(historical_data_str: str, news_str: str, key_levels: str = "{}") -> dict:
    prompt = f"""
You are a professional Nifty 50 options trader.
Analyze the following data and provide a structured trading outlook.
FOCUS: The user has low capital (Rs 2000), so we ONLY care about high-volatility scalps (5-10 mins).

=== HISTORICAL MARKET DATA ===
{historical_data_str}

=== KEY TECHNICAL LEVELS ===
{key_levels}

=== TODAY'S NEWS & MARKET SENTIMENT ===
{news_str}

Respond ONLY in this exact JSON structure:
{{
  "overall_bias": "LONG|SHORT|NEUTRAL",
  "news_sentiment": "POSITIVE|NEGATIVE|NEUTRAL",
  "expected_range_low": 22000,
  "expected_range_high": 22500,
  "strategy_suggestion": "SCALP_LONG|SCALP_SHORT|WAIT",
  "confidence": "HIGH|MEDIUM|LOW",
  "reasoning": "2-3 sentence summary focusing on scalp potential."
}}
"""
    response_text = call_gemini(prompt)
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        log.error("❌ Failed to parse Gemini pre-market JSON.")
        return {}

def analyze_market_open(premarket_analysis: dict, open_data: dict) -> dict:
    prompt = f"""
Pre-market analysis result: {json.dumps(premarket_analysis)}

First 30 minutes of market data:
Open: {open_data.get('open')} | Current: {open_data.get('current')} 
High: {open_data.get('high')} | Low: {open_data.get('low')}
Move: {open_data.get('pct_change', 0):.2f}% | Volume: {open_data.get('volume')}

Based on all the above, confirm or revise the trading decision for SCALPING.
Respond ONLY in JSON:
{{
  "final_direction": "SCALP_LONG|SCALP_SHORT|NO_TRADE",
  "trade_type": "SCALP",
  "confidence": "HIGH|MEDIUM|LOW",
  "reasoning": "1-2 sentences"
}}
"""
    response_text = call_gemini(prompt)
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        log.error("❌ Failed to parse Gemini open JSON.")
        return {}
