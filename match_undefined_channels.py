#!/usr/bin/env python3
"""
match_undefined_channels.py
----------------------------
Drop this script into the root of your epgextract repo and run:

    python match_undefined_channels.py --m3u /path/to/undefined.m3u

It will scan all sites/*/channels/*.xml files, find the single best EPG
match for each M3U channel, and automatically append new entries directly
to generated/matched_channels.xml (skipping any xmltv_id already in it).

Requirements:
    pip install rapidfuzz
"""

import argparse
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

try:
    from rapidfuzz import fuzz, process
except ImportError:
    print("ERROR: rapidfuzz is required. Install it with: pip install rapidfuzz")
    sys.exit(1)

# ── Tunables ────────────────────────────────────────────────────────────────
HIGH_CONFIDENCE = 90   # score >= this → appended to matched_channels.xml
# ────────────────────────────────────────────────────────────────────────────


def normalize(name: str) -> str:
    """Strip resolution tags, punctuation, and extra whitespace for matching."""
    name = re.sub(r'\s*\(?\d{3,4}p\)?', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\[.*?\]', '', name)      # [Geo-blocked], [Not 24/7], etc.
    name = re.sub(r'[^\w\s]', ' ', name)     # punctuation → space
    name = re.sub(r'\s+', ' ', name).strip()
    return name.lower()


def parse_m3u(path: str) -> list[dict]:
    """Return list of {raw_name, norm_name} from an M3U file."""
    channels = []
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if line.startswith('#EXTINF'):
            match = re.search(r',(.+)$', line)
            raw_name = match.group(1).strip() if match else ''
            if raw_name:
                channels.append({
                    'raw_name':  raw_name,
                    'norm_name': normalize(raw_name),
                })
    return channels


def parse_epg_channels(sites_root: str) -> list[dict]:
    """
    Walk sites/*/channels/*.xml and collect every en/es <channel> entry.
    Returns list of {display_name, norm_name, xmltv_id, site, site_id, lang}
    """
    pattern = os.path.join(sites_root, 'sites', '**', 'channels', '*.xml')
    xml_files = glob.glob(pattern, recursive=True)

    if not xml_files:
        pattern2 = os.path.join(sites_root, 'sites', '**', '*.channels.xml')
        xml_files = glob.glob(pattern2, recursive=True)

    epg_channels = []
    seen = set()

    for xml_file in xml_files:
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
        except ET.ParseError:
            continue

        for ch in root.findall('channel'):
            site     = ch.get('site', '')
            lang     = ch.get('lang', '')
            xmltv_id = ch.get('xmltv_id', '')
            site_id  = ch.get('site_id', '')
            name     = (ch.text or '').strip()

            if not name or not xmltv_id:
                continue
            if lang not in ('en', 'es'):
                continue

            key = (xmltv_id, site, lang)
            if key in seen:
                continue
            seen.add(key)

            epg_channels.append({
                'display_name': name,
                'norm_name':    normalize(name),
                'xmltv_id':     xmltv_id,
                'site':         site,
                'site_id':      site_id,
                'lang':         lang,
            })

    return epg_channels


def get_existing_xmltv_ids(matched_channels_path: str) -> set:
    """Return the set of xmltv_ids already in matched_channels.xml."""
    if not os.path.isfile(matched_channels_path):
        return set()
    try:
        tree = ET.parse(matched_channels_path)
        return {ch.get('xmltv_id', '') for ch in tree.getroot().findall('channel')}
    except ET.ParseError:
        return set()


def find_best_matches(m3u_channels, epg_channels, min_score):
    """
    For each M3U channel return only its single highest-scoring EPG match,
    provided the score meets min_score.
    """
    epg_norm_names = [ch['norm_name'] for ch in epg_channels]

    results = []
    for m3u_ch in m3u_channels:
        query = m3u_ch['norm_name']
        if not query:
            continue

        hit = process.extractOne(
            query,
            epg_norm_names,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=min_score,
        )

        if not hit:
            continue

        _matched_norm, score, idx = hit
        epg_ch = epg_channels[idx]
        results.append({
            'm3u_name': m3u_ch['raw_name'],
            'epg_name': epg_ch['display_name'],
            'score':    round(score, 1),
            'xmltv_id': epg_ch['xmltv_id'],
            'site':     epg_ch['site'],
            'site_id':  epg_ch['site_id'],
            'lang':     epg_ch['lang'],
        })

    return results


def append_to_matched_channels(matches, matched_channels_path, existing_ids):
    """
    Append new channel entries to matched_channels.xml, inserting them
    before the closing </channels> tag. Skips xmltv_ids already present.
    """
    new_entries = []
    added_ids = set()

    for m in matches:
        if m['xmltv_id'] in existing_ids or m['xmltv_id'] in added_ids:
            continue
        added_ids.add(m['xmltv_id'])
        site_id_attr = f' site_id="{m["site_id"]}"' if m['site_id'] else ''
        new_entries.append(
            f'  <channel site="{m["site"]}" lang="{m["lang"]}"'
            f' xmltv_id="{m["xmltv_id"]}"{site_id_attr}>'
            f'{m["epg_name"]}</channel>'
        )

    if not new_entries:
        return 0

    if not os.path.isfile(matched_channels_path):
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<channels>']
        lines += new_entries
        lines.append('</channels>')
        with open(matched_channels_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    else:
        with open(matched_channels_path, 'r', encoding='utf-8') as f:
            content = f.read()

        insertion = '\n'.join(new_entries)
        content = content.rstrip()
        if content.endswith('</channels>'):
            content = content[:-len('</channels>')] + insertion + '\n</channels>\n'
        else:
            content += '\n' + insertion + '\n'

        with open(matched_channels_path, 'w', encoding='utf-8') as f:
            f.write(content)

    return len(new_entries)


def main():
    parser = argparse.ArgumentParser(
        description='Match undefined M3U channels to EPG xmltv_ids.'
    )
    parser.add_argument(
        '--m3u',
        default='undefined.m3u',
        help='Path to the undefined.m3u file (default: ./undefined.m3u)',
    )
    parser.add_argument(
        '--sites',
        default='.',
        help='Root of your epgextract repo (default: current directory)',
    )
    parser.add_argument(
        '--matched',
        default=os.path.join('generated', 'matched_channels.xml'),
        help='Path to matched_channels.xml (default: generated/matched_channels.xml)',
    )
    parser.add_argument(
        '--threshold',
        type=float,
        default=HIGH_CONFIDENCE,
        help=f'Minimum match score to accept (default: {HIGH_CONFIDENCE})',
    )
    args = parser.parse_args()

    # ── Validate inputs ──────────────────────────────────────────────────────
    if not os.path.isfile(args.m3u):
        print(f"ERROR: M3U file not found: {args.m3u}")
        sys.exit(1)

    sites_path = os.path.join(args.sites, 'sites')
    if not os.path.isdir(sites_path):
        print(f"ERROR: 'sites' directory not found under: {args.sites}")
        print("Make sure --sites points to your epgextract repo root.")
        sys.exit(1)

    # ── Parse ────────────────────────────────────────────────────────────────
    print(f"Parsing M3U: {args.m3u}")
    m3u_channels = parse_m3u(args.m3u)
    print(f"  -> {len(m3u_channels)} channels found")

    print(f"Scanning EPG channel XMLs under: {sites_path}")
    epg_channels = parse_epg_channels(args.sites)
    print(f"  -> {len(epg_channels)} EPG channels found (lang=en or es)")

    if not epg_channels:
        print("ERROR: No EPG channels found. Check your --sites path.")
        sys.exit(1)

    print(f"Loading existing xmltv_ids from: {args.matched}")
    existing_ids = get_existing_xmltv_ids(args.matched)
    print(f"  -> {len(existing_ids)} already present, will skip these")

    # ── Match ────────────────────────────────────────────────────────────────
    print(f"Fuzzy matching (threshold>={args.threshold})...")
    matches = find_best_matches(m3u_channels, epg_channels, args.threshold)
    print(f"  -> {len(matches)} channels matched")

    # ── Append ───────────────────────────────────────────────────────────────
    added = append_to_matched_channels(matches, args.matched, existing_ids)
    print(f"  -> {added} new entries appended to {args.matched}")
    print(f"  -> {len(matches) - added} skipped (xmltv_id already present)")

    print(f"""
Done! You can now run your grab command:

  npm run grab --- --channels=generated/matched_channels.xml ^
      --maxConnections=7 --days=5 --lang=en,es --timeout=8000
""")


if __name__ == '__main__':
    main()