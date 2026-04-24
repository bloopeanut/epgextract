#!/usr/bin/env python3
"""
build_matched_channels.py
--------------------------
Drop this script into the root of your epgextract repo and run:

    python build_matched_channels.py

It scans all sites/*/channels/*.xml files, collects every channel with
lang="en" or lang="es", deduplicates by xmltv_id (keeping the entry with
the most specific site_id), and writes a fresh matched_channels.xml.

Options:
  --sites     Root of your epgextract repo (default: current directory)
  --output    Output file path (default: generated/all_en_es_channels.xml)
  --overwrite Overwrite output if it already exists (default: asks)
"""

import argparse
import glob
import os
import sys
import xml.etree.ElementTree as ET


def parse_epg_channels(sites_root: str) -> list[dict]:
    """
    Walk sites/*/channels/*.xml and collect every en/es <channel> entry.
    Returns list of {display_name, xmltv_id, site, site_id, lang, source_file}
    """
    pattern = os.path.join(sites_root, 'sites', '**', 'channels', '*.xml')
    xml_files = glob.glob(pattern, recursive=True)

    if not xml_files:
        pattern2 = os.path.join(sites_root, 'sites', '**', '*.channels.xml')
        xml_files = glob.glob(pattern2, recursive=True)

    if not xml_files:
        return []

    print(f"  Found {len(xml_files)} XML files to scan...")

    all_channels = []
    files_done = 0

    for xml_file in xml_files:
        files_done += 1
        if files_done % 50 == 0 or files_done == len(xml_files):
            print(f"  [{files_done}/{len(xml_files)}] scanning files... {len(all_channels)} channels collected", end='\r')

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

            all_channels.append({
                'display_name': name,
                'xmltv_id':     xmltv_id,
                'site':         site,
                'site_id':      site_id,
                'lang':         lang,
            })

    print()  # newline after progress line
    return all_channels


def base_id(xmltv_id: str) -> str:
    """Return the part of an xmltv_id before the '@', e.g. '10.au@Adelaide' -> '10.au'."""
    return xmltv_id.split('@')[0]


def deduplicate(channels: list[dict]) -> list[dict]:
    """
    Deduplicate by the base xmltv_id (part before '@').
    e.g. 10.au@Adelaide and 10.au@Brisbane are treated as the same channel.
    When multiple entries share the same base id, prefer the one with a
    non-empty site_id (more specific), then the first encountered as a tiebreaker.
    """
    seen = {}
    for ch in channels:
        key = base_id(ch['xmltv_id'])
        if key not in seen:
            seen[key] = ch
        else:
            # Prefer entry that has a site_id over one that doesn't
            if ch['site_id'] and not seen[key]['site_id']:
                seen[key] = ch

    return list(seen.values())


def get_allowed_sites(matched_channels_path: str) -> set:
    """Return the set of site values present in matched_channels.xml."""
    if not os.path.isfile(matched_channels_path):
        print(f"WARNING: '{matched_channels_path}' not found — site filter skipped.")
        return None
    try:
        tree = ET.parse(matched_channels_path)
        sites = {ch.get('site', '') for ch in tree.getroot().findall('channel')}
        sites.discard('')
        return sites
    except ET.ParseError as e:
        print(f"WARNING: Could not parse '{matched_channels_path}': {e} — site filter skipped.")
        return None


def filter_by_site(channels: list[dict], allowed_sites: set) -> list[dict]:
    """Keep only channels whose site appears in allowed_sites."""
    return [ch for ch in channels if ch['site'] in allowed_sites]


def write_matched_channels(channels: list[dict], output_path: str) -> int:
    """Write channels to a matched_channels.xml file, sorted by xmltv_id."""
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

    channels_sorted = sorted(channels, key=lambda c: c['xmltv_id'].lower())

    def xe(s: str) -> str:
        """XML-escape a string so special characters don't break the parser."""
        return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<channels>']
    for ch in channels_sorted:
        site_id_attr = f' site_id="{xe(ch["site_id"])}"' if ch['site_id'] else ''
        lines.append(
            f'  <channel site="{xe(ch["site"])}" lang="{xe(ch["lang"])}"'
            f' xmltv_id="{xe(ch["xmltv_id"])}"{site_id_attr}>'
            f'{xe(ch["display_name"])}</channel>'
        )
    lines.append('</channels>')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    return len(channels_sorted)


def main():
    parser = argparse.ArgumentParser(
        description='Build matched_channels.xml from all en/es EPG site channels.'
    )
    parser.add_argument(
        '--sites',
        default='.',
        help='Root of your epgextract repo (default: current directory)',
    )
    parser.add_argument(
        '--output',
        default=os.path.join('generated', 'all_en_es_channels.xml'),
        help='Output file path (default: generated/all_en_es_channels.xml)',
    )
    parser.add_argument(
        '--matched',
        default=os.path.join('generated', 'matched_channels.xml'),
        help='Path to existing matched_channels.xml used to filter allowed sites '
             '(default: generated/matched_channels.xml)',
    )
    parser.add_argument(
        '--overwrite',
        action='store_true',
        help='Overwrite output file without asking',
    )
    args = parser.parse_args()

    # ── Validate ─────────────────────────────────────────────────────────────
    sites_path = os.path.join(args.sites, 'sites')
    if not os.path.isdir(sites_path):
        print(f"ERROR: 'sites' directory not found under: {args.sites}")
        print("Make sure --sites points to your epgextract repo root.")
        sys.exit(1)

    if os.path.isfile(args.output) and not args.overwrite:
        answer = input(f"'{args.output}' already exists. Overwrite? [y/N] ").strip().lower()
        if answer != 'y':
            print("Aborted.")
            sys.exit(0)

    # ── Scan ─────────────────────────────────────────────────────────────────
    print(f"\nScanning EPG channel XMLs under: {sites_path}")
    all_channels = parse_epg_channels(args.sites)
    print(f"  -> {len(all_channels)} total en/es channel entries found")

    if not all_channels:
        print("ERROR: No channels found. Check your --sites path.")
        sys.exit(1)

    # ── Deduplicate ───────────────────────────────────────────────────────────
    print(f"\nDeduplicating by xmltv_id...")
    unique_channels = deduplicate(all_channels)
    dupes_removed = len(all_channels) - len(unique_channels)
    print(f"  -> {len(unique_channels)} unique channels ({dupes_removed} duplicates removed)")

    # ── Filter by site ───────────────────────────────────────────────────────
    print(f"\nLoading allowed sites from: {args.matched}")
    allowed_sites = get_allowed_sites(args.matched)
    if allowed_sites is not None:
        print(f"  -> {len(allowed_sites)} distinct sites found")
        filtered_channels = filter_by_site(unique_channels, allowed_sites)
        removed = len(unique_channels) - len(filtered_channels)
        print(f"  -> {len(filtered_channels)} channels kept ({removed} removed — site not in matched_channels.xml)")
        unique_channels = filtered_channels
    else:
        print("  -> No site filter applied")

    # ── Write ─────────────────────────────────────────────────────────────────
    print(f"\nWriting output to: {args.output}")
    count = write_matched_channels(unique_channels, args.output)
    print(f"  -> {count} channels written")

    print(f"""
Done! You can now run your grab command:

  npm run grab --- --channels={args.output} ^
      --maxConnections=7 --days=5 --lang=en,es --timeout=8000
""")


if __name__ == '__main__':
    main()