#!/usr/bin/env python3
"""
Poskota.co.id RSS Feed Scraper with Full Article Content
=========================================================
- Hanya artikel BARU yang masuk feed (belum pernah di-scrape sebelumnya)
- Tanggal artikel = waktu saat scraping (date NOW), bukan tanggal asli
- Artikel lama di-track via seen_articles.json
- Dijalankan otomatis via GitHub Actions + publish ke GitHub Pages
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import time
import re
import os
import html
import hashlib
import json

# ============================================================
# KONFIGURASI
# ============================================================

SCRAPE_URLS = [
    "https://www.poskota.co.id/tag/paylater",
]

MAX_ARTICLES = 20
FEED_TITLE = "Poskota.co.id - PayLater"
FEED_DESCRIPTION = "RSS Feed dari poskota.co.id tag PayLater dengan konten artikel lengkap"
FEED_LINK = "https://www.poskota.co.id"
OUTPUT_FILE = "docs/feed.xml"
SEEN_FILE = "seen_articles.json"
FEED_MAX_AGE_HOURS = 3
SEEN_MAX_AGE_DAYS = 30
REQUEST_DELAY = 2
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
})


# ============================================================
# TRACKING ARTIKEL YANG SUDAH PERNAH MASUK
# ============================================================

def load_seen_articles():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_seen_articles(seen):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_MAX_AGE_DAYS)).isoformat()
    cleaned = {url: data for url, data in seen.items() if data.get('first_seen', '') > cutoff}

    with open(SEEN_FILE, 'w', encoding='utf-8') as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)

    print(f"  [i] Tracked articles: {len(cleaned)} (cleaned {len(seen) - len(cleaned)} old entries)")


# ============================================================
# FETCH & PARSE FUNCTIONS
# ============================================================

def fetch_page(url, retries=3):
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            response.encoding = 'utf-8'
            return response.text
        except requests.RequestException as e:
            print(f"  [!] Gagal fetch {url} (percobaan {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(REQUEST_DELAY * 2)
    return None


def parse_list_page(url):
    print(f"\n[*] Scraping halaman list: {url}")
    html_content = fetch_page(url)
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, 'lxml')
    articles = []

    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        title = link.get_text(strip=True)

        if not href or not title:
            continue

        if not re.search(r'/\d{4}/\d{2}/\d{2}/', href):
            continue

        if href.startswith('/'):
            href = 'https://www.poskota.co.id' + href

        if 'poskota.co.id' not in href:
            continue

        if len(title) < 20:
            continue

        if any(a['link'] == href for a in articles):
            continue

        articles.append({'title': title, 'link': href})
        if len(articles) >= MAX_ARTICLES:
            break

    print(f"  [+] Ditemukan {len(articles)} artikel di halaman")
    return articles


def parse_article_page(url):
    print(f"  [>] Mengambil artikel: {url}")
    html_content = fetch_page(url)
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, 'lxml')
    article_data = {}

    # JUDUL
    h1 = soup.find('h1')
    article_data['title'] = h1.get_text(strip=True) if h1 else ''

    # TANGGAL ASLI (simpan sebagai referensi, TIDAK dipakai sebagai pubDate)
    date_text = ''
    for text_node in soup.find_all(string=re.compile(r'\d{2}\s+(Jan|Feb|Mar|Apr|Mei|May|Jun|Jul|Agu|Agus|Aug|Sep|Okt|Oct|Nov|Des|Dec)\s+\d{4}')):
        date_text = text_node.strip()
        break
    article_data['original_date'] = date_text

    # REPORTER & EDITOR
    reporter = ''
    editor = ''

    reporter_section = soup.find(string=re.compile(r'Reporter'))
    if reporter_section:
        parent = reporter_section.find_parent()
        if parent:
            reporter_link = parent.find_next('a', href=re.compile(r'/author/'))
            if reporter_link:
                reporter = reporter_link.get_text(strip=True)

    editor_section = soup.find(string=re.compile(r'Editor'))
    if editor_section:
        parent = editor_section.find_parent()
        if parent:
            editor_link = parent.find_next('a', href=re.compile(r'/author/'))
            if editor_link:
                editor = editor_link.get_text(strip=True)

    article_data['reporter'] = reporter
    article_data['editor'] = editor

    # GAMBAR UTAMA
    main_image = ''
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if 'assets.poskota.co.id' in src and ('crop' in src or 'medias' in src):
            if '/crop/original/' in src or '/crop/538' in src:
                main_image = src
                break
            elif not main_image:
                main_image = src
    article_data['image'] = main_image

    # CAPTION
    caption = ''
    if main_image:
        img_tag = soup.find('img', src=main_image)
        if img_tag:
            alt_text = img_tag.get('alt', '')
            if alt_text and len(alt_text) > 10:
                caption = alt_text
    article_data['caption'] = caption

    # KONTEN ARTIKEL
    content_parts = []
    found_content = False

    for element in soup.find_all(['p', 'h2', 'h3', 'h4', 'li']):
        text = element.get_text(strip=True)
        if not text:
            continue

        parent_classes = ' '.join(element.parent.get('class', []) if element.parent else [])
        grandparent_classes = ''
        if element.parent and element.parent.parent:
            grandparent_classes = ' '.join(element.parent.parent.get('class', []))

        skip_classes = ['sidebar', 'footer', 'nav', 'menu', 'comment', 'trending',
                        'news-update', 'berita-terkait', 'terkait']
        if any(skip in parent_classes.lower() for skip in skip_classes):
            continue
        if any(skip in grandparent_classes.lower() for skip in skip_classes):
            continue

        if any(skip in text for skip in ['Reporter', 'Editor', 'Follow Poskota',
                                          'Google News', 'WhatsApp Channel',
                                          'Cek berita', 'Berita Terkait',
                                          'News Update', 'Trending']):
            continue

        if len(text) < 15 and element.name == 'p':
            continue

        if text == caption:
            continue

        if re.match(r'^(POSKOTA\.CO\.ID|[A-Z]{3,})', text):
            found_content = True

        if not found_content and len(text) > 40:
            found_content = True

        if found_content:
            clean_text = text.replace('\xa0', ' ').strip()
            if clean_text:
                if element.name in ['h2', 'h3', 'h4']:
                    if text in ['Berita Terkait', 'News Update', 'Trending']:
                        continue
                    content_parts.append(f"\n### {clean_text}\n")
                elif element.name == 'li':
                    content_parts.append(f"• {clean_text}")
                else:
                    content_parts.append(clean_text)

    article_data['content'] = '\n\n'.join(content_parts)

    # MULTI-PAGE
    next_pages = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        if '?halaman=' in href and href not in next_pages:
            page_url = href if href.startswith('http') else 'https://www.poskota.co.id' + href
            if page_url != url:
                next_pages.append(page_url)

    for page_url in sorted(set(next_pages))[:5]:
        print(f"    [>] Halaman lanjutan: {page_url}")
        time.sleep(REQUEST_DELAY)
        page_content = fetch_additional_page(page_url)
        if page_content:
            article_data['content'] += '\n\n' + page_content

    # TAG
    tags = []
    for tag_link in soup.find_all('a', href=re.compile(r'/tag/')):
        tag_text = tag_link.get_text(strip=True)
        if tag_text and len(tag_text) > 1 and tag_text not in ['Tags', 'Tag']:
            clean_tag = tag_text.replace('#', '').strip()
            if clean_tag and clean_tag not in tags:
                tags.append(clean_tag)
    article_data['tags'] = tags

    # KATEGORI
    category = ''
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text = a.get_text(strip=True)
        if re.match(r'^https?://www\.poskota\.co\.id/[a-z-]+$', href) and text:
            if text.upper() == text or text[0].isupper():
                if text not in ['Home', '', 'E-Paper'] and len(text) < 30:
                    category = text
                    break
    article_data['category'] = category

    return article_data


def fetch_additional_page(url):
    html_content = fetch_page(url)
    if not html_content:
        return ''
    soup = BeautifulSoup(html_content, 'lxml')
    content_parts = []
    found_content = False

    for p in soup.find_all(['p', 'h2', 'h3', 'h4', 'li']):
        text = p.get_text(strip=True)
        if not text or len(text) < 15:
            continue
        parent_classes = ' '.join(p.parent.get('class', []))
        if any(skip in parent_classes.lower() for skip in ['sidebar', 'footer', 'nav', 'trending']):
            continue
        if any(skip in text for skip in ['Reporter', 'Editor', 'Follow Poskota',
                                          'Google News', 'WhatsApp Channel']):
            continue

        if not found_content and len(text) > 40:
            found_content = True

        if found_content:
            clean_text = text.replace('\xa0', ' ').strip()
            if clean_text:
                if p.name in ['h2', 'h3', 'h4']:
                    content_parts.append(f"\n### {clean_text}\n")
                elif p.name == 'li':
                    content_parts.append(f"• {clean_text}")
                else:
                    content_parts.append(clean_text)

    return '\n\n'.join(content_parts)


# ============================================================
# RSS GENERATION
# ============================================================

def make_pub_date(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    return f"{days[dt.weekday()]}, {dt.day:02d} {months[dt.month-1]} {dt.year} {dt.hour:02d}:{dt.minute:02d}:00 +0700"


def generate_rss(articles_data):
    print(f"\n[*] Generating RSS XML with {len(articles_data)} articles...")
    now = datetime.now(timezone(timedelta(hours=7))).strftime('%a, %d %b %Y %H:%M:%S +0700')

    rss_items = []
    for article in articles_data:
        if not article:
            continue

        content_html = ''
        if article.get('image'):
            content_html += f'<p><img src="{html.escape(article["image"])}" alt="{html.escape(article.get("title", ""))}" style="max-width:100%;" /></p>\n'
        if article.get('caption'):
            content_html += f'<p><em>{html.escape(article["caption"])}</em></p>\n'
        if article.get('reporter'):
            content_html += f'<p><strong>Reporter:</strong> {html.escape(article["reporter"])}'
            if article.get('editor'):
                content_html += f' | <strong>Editor:</strong> {html.escape(article["editor"])}'
            content_html += '</p>\n'

        if article.get('original_date'):
            content_html += f'<p><em>Tanggal asli: {html.escape(article["original_date"])}</em></p>\n'

        if article.get('content'):
            paragraphs = article['content'].split('\n\n')
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                if para.startswith('### '):
                    content_html += f'<h3>{html.escape(para[4:])}</h3>\n'
                elif para.startswith('• '):
                    content_html += f'<li>{html.escape(para[2:])}</li>\n'
                else:
                    content_html += f'<p>{html.escape(para)}</p>\n'
        if article.get('tags'):
            tags_str = ', '.join(article['tags'])
            content_html += f'<p><strong>Tags:</strong> {html.escape(tags_str)}</p>\n'

        guid = article.get('link', hashlib.md5(article.get('title', '').encode()).hexdigest())

        rss_items.append({
            'title': article.get('title', 'Tanpa Judul'),
            'link': article.get('link', ''),
            'description': content_html,
            'pubDate': article.get('pub_date', now),
            'category': article.get('category', ''),
            'tags': article.get('tags', []),
            'guid': guid,
            'image': article.get('image', ''),
        })

    rss_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>{html.escape(FEED_TITLE)}</title>
    <description>{html.escape(FEED_DESCRIPTION)}</description>
    <link>{html.escape(FEED_LINK)}</link>
    <language>id</language>
    <lastBuildDate>{now}</lastBuildDate>
    <generator>Poskota RSS Scraper (GitHub Actions)</generator>
'''

    for item in rss_items:
        rss_xml += f'''    <item>
      <title><![CDATA[{item['title']}]]></title>
      <link>{html.escape(item['link'])}</link>
      <guid isPermaLink="true">{html.escape(item['guid'])}</guid>
      <pubDate>{item['pubDate']}</pubDate>
'''
        if item['category']:
            rss_xml += f'      <category><![CDATA[{item["category"]}]]></category>\n'
        for tag in item.get('tags', []):
            rss_xml += f'      <category><![CDATA[{tag}]]></category>\n'
        if item['image']:
            rss_xml += f'      <media:content url="{html.escape(item["image"])}" medium="image" />\n'
        rss_xml += f'      <description><![CDATA[{item["description"]}]]></description>\n'
        rss_xml += f'      <content:encoded><![CDATA[{item["description"]}]]></content:encoded>\n'
        rss_xml += '    </item>\n'

    rss_xml += '''  </channel>
</rss>'''
    return rss_xml


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("  Poskota.co.id RSS Scraper - NEW Articles Only")
    print("=" * 60)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    seen = load_seen_articles()
    print(f"  [i] Artikel yang sudah ditrack: {len(seen)}")

    all_articles = []
    for url in SCRAPE_URLS:
        articles = parse_list_page(url)
        all_articles.extend(articles)
        time.sleep(REQUEST_DELAY)

    if not all_articles:
        print("\n[!] Tidak ada artikel ditemukan.")
        rss_xml = generate_rss([])
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(rss_xml)
        return

    # Filter hanya artikel BARU
    new_articles = []
    for article in all_articles:
        if article['link'] not in seen:
            new_articles.append(article)

    print(f"\n[*] Artikel baru: {len(new_articles)} dari {len(all_articles)} total")

    if not new_articles:
        print("[i] Tidak ada artikel baru.")
        recent_in_feed = get_recent_feed_articles(seen)
        rss_xml = generate_rss(recent_in_feed)
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(rss_xml)
        save_seen_articles(seen)
        return

    # Fetch konten lengkap HANYA untuk artikel baru
    now = datetime.now(timezone.utc)
    articles_data = []

    for i, article in enumerate(new_articles):
        print(f"\n--- Artikel Baru {i+1}/{len(new_articles)} ---")
        article_data = parse_article_page(article['link'])

        pub_date_now = make_pub_date(now + timedelta(minutes=i))

        if article_data:
            if not article_data.get('title'):
                article_data['title'] = article['title']
            article_data['link'] = article['link']
            article_data['pub_date'] = pub_date_now
            articles_data.append(article_data)
        else:
            articles_data.append({
                'title': article['title'],
                'link': article['link'],
                'content': '(Konten tidak dapat diambil)',
                'pub_date': pub_date_now,
                'original_date': '',
                'image': '', 'reporter': '', 'editor': '',
                'tags': [], 'category': '', 'caption': '',
            })

        seen[article['link']] = {
            'title': article['title'],
            'first_seen': now.isoformat(),
            'pub_date': pub_date_now,
        }

        time.sleep(REQUEST_DELAY)

    # Tambahkan artikel recent yang masih dalam window
    recent_in_feed = get_recent_feed_articles(seen)
    all_feed_articles = articles_data + recent_in_feed

    rss_xml = generate_rss(all_feed_articles)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(rss_xml)

    save_seen_articles(seen)

    print(f"\n{'=' * 60}")
    print(f"  SELESAI!")
    print(f"  Artikel baru di feed  : {len(articles_data)}")
    print(f"  Artikel recent di feed: {len(recent_in_feed)}")
    print(f"  Total di feed         : {len(all_feed_articles)}")
    print(f"  File: {OUTPUT_FILE}")
    print(f"{'=' * 60}")


def get_recent_feed_articles(seen):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=FEED_MAX_AGE_HOURS)).isoformat()
    recent = []
    for url, data in seen.items():
        if data.get('first_seen', '') > cutoff and data.get('pub_date'):
            recent.append({
                'title': data.get('title', 'Tanpa Judul'),
                'link': url,
                'content': '',
                'pub_date': data.get('pub_date', ''),
                'original_date': '',
                'image': '', 'reporter': '', 'editor': '',
                'tags': [], 'category': '', 'caption': '',
            })
    return recent


if __name__ == '__main__':
    main()
