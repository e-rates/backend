import os
import requests
from typing import Any, Dict

SLM_BASE_URL = os.getenv("SLM_SERVICE_URL", "http://127.0.0.1:5001")


def extract_intent(prompt: str) -> Dict[str, Any]:
    url = f"{SLM_BASE_URL}/extract-intent"
    try:
        response = requests.post(url, json={"prompt": prompt}, timeout=(1.0, 15.0))
        response.raise_for_status()
        raw = response.json()

        args = raw.get("arguments", {})
        while isinstance(args, dict) and ("function" in args or "arguments" in args):
            if "function" in args and isinstance(args["function"], dict):
                args = args["function"].get("arguments", {})
            elif "arguments" in args and isinstance(args["arguments"], dict):
                args = args["arguments"]
            else:
                break

        return {
            "tool": raw.get("tool", "get_county_reconciliation"),
            "arguments": args if isinstance(args, dict) else {}
        }
    except Exception:
        return {"tool": "get_county_reconciliation", "arguments": {}}


def format_markdown(county: str, data: Dict[str, Any]) -> str:
    url = f"{SLM_BASE_URL}/format-markdown"
    try:
        response = requests.post(url, json={"county": county, "data": data}, timeout=(1.0, 20.0))
        response.raise_for_status()
        res = response.json()
        return res.get("report") or res.get("markdown") or res.get("answer") or ""
    except Exception:
        return ""
