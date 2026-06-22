#!/usr/bin/env python3
"""
Save an AI Builders Digest into the follow_builders project.

Usage:
  python3 save_digest.py --data '{"items":[...]}'  # from feed JSON
Or pipe:
  node prepare-digest.js | python3 save_digest.py

The script reads stdin or --data flag, generates the structured JSON,
updates the _index.json manifest, and prints the saved file path.
"""

import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / 'data'

def load_digest_data(raw: dict) -> dict:
    """Transform raw feed data into the structured digest format."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    filename = f"follow_builders_{today}.json"

    items = []

    # --- X / Twitter ---
    for builder in raw.get('x', []):
        tweets = builder.get('tweets', [])
        if not tweets:
            continue

        # Build summarized content (bilingual) - placeholder since AI remixing happens separately
        en_lines = [t.get('text', '') for t in tweets]
        en_summary = '\n'.join(en_lines)[:3000]

        item = {
            "type": "twitter",
            "authorName": builder.get('name', ''),
            "handle": builder.get('handle', ''),
            "bio": builder.get('bio', ''),
            "publishedAt": tweets[0].get('createdAt', today),
            "content_en": en_summary,
            "content_zh": "",  # filled in by /ai remixing
            "urls": [t.get('url', '') for t in tweets if t.get('url')],
            "tweets": [{"text": t.get('text', ''), "url": t.get('url', '')} for t in tweets]
        }
        items.append(item)

    # --- Podcasts ---
    for pod in raw.get('podcasts', []):
        item = {
            "type": "podcast",
            "authorName": pod.get('name', ''),
            "handle": "",
            "bio": "",
            "title": pod.get('title', ''),
            "publishedAt": pod.get('publishedAt', today),
            "content_en": pod.get('transcript', '')[:5000],
            "content_zh": "",
            "urls": [pod.get('url', '')]
        }
        items.append(item)

    # --- Blogs ---
    for blog in raw.get('blogs', []):
        for post in blog.get('posts', []):
            item = {
                "type": "blog",
                "authorName": blog.get('name', ''),
                "handle": "",
                "bio": "",
                "title": post.get('title', ''),
                "publishedAt": post.get('publishedAt', today),
                "content_en": post.get('summary', ''),
                "content_zh": "",
                "urls": [post.get('url', '')]
            }
            items.append(item)

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "digestDate": today,
        "items": items
    }


def save_digest(data: dict) -> str:
    """Write digest JSON file and update manifest."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    date = data.get('digestDate', datetime.now(timezone.utc).strftime('%Y-%m-%d'))
    filename = f"follow_builders_{date}.json"
    filepath = DATA_DIR / filename

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Update _index.json
    index_path = DATA_DIR / '_index.json'
    if index_path.exists():
        with open(index_path, 'r', encoding='utf-8') as f:
            idx = json.load(f)
    else:
        idx = {"files": []}

    if filename not in idx['files']:
        idx['files'].append(filename)
        idx['files'].sort(reverse=True)  # newest first

    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)

    return str(filepath)


def main():
    if not sys.stdin.isatty():
        raw = json.load(sys.stdin)
    elif len(sys.argv) >= 3 and sys.argv[1] == '--data':
        raw = json.loads(sys.argv[2])
    else:
        print("Usage: cat feed.json | python3 save_digest.py")
        print("   or: python3 save_digest.py --data '{\"x\":[],\"podcasts\":[]}'")
        sys.exit(1)

    if isinstance(raw, dict) and raw.get('status') == 'ok':
        raw = raw  # feed JSON from prepare-digest.js

    digest = load_digest_data(raw)
    saved = save_digest(digest)

    print(json.dumps({
        "status": "ok",
        "saved": saved,
        "items": len(digest['items']),
        "date": digest['digestDate']
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
