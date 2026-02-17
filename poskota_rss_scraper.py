#!/usr/bin/env python3
"""
Poskota.co.id RSS Feed Scraper with Full Article Content
Dijalankan otomatis via GitHub Actions + publish ke GitHub Pages
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import time
import re
import os
import html
import hashlib

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
REQUEST_DELAY = 2
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
})


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
    """Parse halaman tag/kategori poskota untuk mendapatkan daftar artikel."""
    print(f"\n[*] Scraping halaman list: {url}")
    html_content = fetch_page(url)
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, 'lxml')
    articles = []

    # Poskota: artikel ada di h2 > a dengan link ke artikel
    # Pattern URL: /2026/02/11/judul-artikel
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        title = link.get_text(strip=True)

        if not href or not title:
            continue

        # Filter hanya link artikel poskota (pattern: /YYYY/MM/DD/slug)
        if not re.search(r'/\d{4}/\d{2}/\d{2}/', href):
            continue

        # Pastikan URL lengkap
        if href.startswith('/'):
            href = 'https://www.poskota.co.id' + href

        # Skip jika bukan dari poskota
        if 'poskota.co.id' not in href:
            continue

        # Skip judul pendek (kemungkinan navigasi)
        if len(title) < 20:
            continue

        # Hindari duplikat
        if any(a['link'] == href for a in articles):
            continue

        articles.append({'title': title, 'link': href})
        if len(articles) >= MAX_ARTICLES:
            break

    print(f"  [+] Ditemukan {len(articles)} artikel")
    return articles


def parse_article_page(url):
    """Parse halaman artikel poskota untuk mendapatkan konten lengkap."""
    print(f"  [>] Mengambil artikel: {url}")
    html_content = fetch_page(url)
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, 'lxml')
    article_data = {}

    # === JUDUL ===
    h1 = soup.find('h1')
    article_data['title'] = h1.get_text(strip=True) if h1 else ''

    # === TANGGAL ===
    # Format poskota: "Rabu 11 Feb 2026, 13:03 WIB" atau "11 Feb 2026, 13:03 WIB"
    date_text = ''
    hari_list = ['Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu', 'Minggu']
    bulan_map = {
        'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
        'Mei': '05', 'Jun': '06', 'Jul': '07', 'Agu': '08', 'Agus': '08',
        'Sep': '09', 'Okt': '10', 'Nov': '11', 'Des': '12',
        'May': '05', 'Aug': '08', 'Oct': '10', 'Dec': '12',
    }

    for text_node in soup.find_all(string=re.compile(r'\d{2}\s+(Jan|Feb|Mar|Apr|Mei|May|Jun|Jul|Agu|Agus|Aug|Sep|Okt|Oct|Nov|Des|Dec)\s+\d{4}')):
        date_text = text_node.strip()
        break

    article_data['date_text'] = date_text
    article_data['pub_date'] = parse_date(date_text, bulan_map)

    # === REPORTER & EDITOR ===
    reporter = ''
    editor = ''

    # Poskota: Reporter dan Editor ada di bawah artikel
    # <a href="/author/...">Nama Reporter</a>
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

    # === GAMBAR UTAMA ===
    main_image = ''
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if 'assets.poskota.co.id' in src and ('crop' in src or 'medias' in src):
            # Prefer gambar besar (crop/original atau crop/538x390)
            if '/crop/original/' in src or '/crop/538' in src:
                main_image = src
                break
            elif not main_image:
                main_image = src

    article_data['image'] = main_image

    # === CAPTION GAMBAR ===
    caption = ''
    if main_image:
        img_tag = soup.find('img', src=main_image)
        if img_tag:
            alt_text = img_tag.get('alt', '')
            if alt_text and len(alt_text) > 10:
                caption = alt_text
    article_data['caption'] = caption

    # === KONTEN ARTIKEL ===
    content_parts = []
    found_content = False

    for element in soup.find_all(['p', 'h2', 'h3', 'h4', 'li']):
        text = element.get_text(strip=True)
        if not text:
            continue

        # Skip elemen navigasi, sidebar
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

        # Skip metadata
        if any(skip in text for skip in ['Reporter', 'Editor', 'Follow Poskota',
                                          'Google News', 'WhatsApp Channel',
                                          'Cek berita', 'Berita Terkait',
                                          'News Update', 'Trending']):
            continue

        # Skip teks pendek yang kemungkinan UI/navigasi
        if len(text) < 15 and element.name == 'p':
            continue

        # Skip caption
        if text == caption:
            continue

        # Deteksi awal konten artikel
        if re.match(r'^(POSKOTA\.CO\.ID|[A-Z]{3,})', text):
            found_content = True

        if not found_content and len(text) > 40:
            found_content = True

        if found_content:
            clean_text = text.replace('\xa0', ' ').strip()
            if clean_text:
                if element.name in ['h2', 'h3', 'h4']:
                    # Skip heading navigasi
                    if text in ['Berita Terkait', 'News Update', 'Trending']:
                        continue
                    content_parts.append(f"\n### {clean_text}\n")
                elif element.name == 'li':
                    content_parts.append(f"• {clean_text}")
                else:
                    content_parts.append(clean_text)

    article_data['content'] = '\n\n'.join(content_parts)

    # === MULTI-PAGE: Poskota pakai ?halaman=2 ===
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

    # === TAG ===
    tags = []
    for tag_link in soup.find_all('a', href=re.compile(r'/tag/')):
        tag_text = tag_link.get_text(strip=True)
        if tag_text and len(tag_text) > 1 and tag_text not in ['Tags', 'Tag']:
            # Bersihkan tag
            clean_tag = tag_text.replace('#', '').strip()
            if clean_tag and clean_tag not in tags:
                tags.append(clean_tag)
    article_data['tags'] = tags

    # === KATEGORI ===
    category = ''
    # Poskota: kategori di breadcrumb atau link kategori
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text = a.get_text(strip=True)
        # Pattern: /ekonomi, /tekno, /news dll (langsung di root)
        if re.match(r'^https?://www\.poskota\.co\.id/[a-z-]+$', href) and text:
            if text.upper() == text or text[0].isupper():
                if text not in ['Home', '', 'E-Paper'] and len(text) < 30:
                    category = text
                    break
    article_data['category'] = category

    return article_data


def fetch_additional_page(url):
    """Fetch halaman lanjutan dari artikel multi-page."""
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


def parse_date(date_text, bulan_map):
    """Parse tanggal Indonesia ke format RFC 822."""
    if not date_text:
        return datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0700')

    # Format: "Rabu 11 Feb 2026, 13:03 WIB" atau "11 Feb 2026, 13:03 WIB"
    match = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4}),?\s*(\d{2}):(\d{2})', date_text)
    if match:
        day, bulan, year, hour, minute = match.groups()
        month_num = bulan_map.get(bulan, bulan_map.get(bulan[:3], None))
        if month_num:
            try:
                dt = datetime(int(year), int(month_num), int(day), int(hour), int(minute))
                days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
                months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                return f"{days[dt.weekday()]}, {dt.day:02d} {months[dt.month-1]} {dt.year} {dt.hour:02d}:{dt.minute:02d}:00 +0700"
            except ValueError:
                pass

    return datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0700')


def generate_rss(articles_data):
    """Generate file RSS XML dari data artikel."""
    print(f"\n[*] Generating RSS XML...")
    now = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')

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


def main():
    print("=" * 60)
    print("  Poskota.co.id RSS Scraper - Full Content")
    print("=" * 60)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    all_articles = []
    for url in SCRAPE_URLS:
        articles = parse_list_page(url)
        all_articles.extend(articles)
        time.sleep(REQUEST_DELAY)

    if not all_articles:
        print("\n[!] Tidak ada artikel ditemukan.")
        return

    # Hapus duplikat
    seen = set()
    unique_articles = []
    for article in all_articles:
        if article['link'] not in seen:
            seen.add(article['link'])
            unique_articles.append(article)

    print(f"\n[*] Total {len(unique_articles)} artikel unik")

    # Fetch konten lengkap
    articles_data = []
    for i, article in enumerate(unique_articles):
        print(f"\n--- Artikel {i+1}/{len(unique_articles)} ---")
        article_data = parse_article_page(article['link'])
        if article_data:
            if not article_data.get('title'):
                article_data['title'] = article['title']
            article_data['link'] = article['link']
            articles_data.append(article_data)
        else:
            articles_data.append({
                'title': article['title'],
                'link': article['link'],
                'content': '(Konten tidak dapat diambil)',
                'pub_date': datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0700'),
                'image': '', 'reporter': '', 'editor': '',
                'tags': [], 'category': '', 'caption': '',
            })
        time.sleep(REQUEST_DELAY)

    # Generate & simpan RSS
    rss_xml = generate_rss(articles_data)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(rss_xml)

    print(f"\n{'=' * 60}")
    print(f"  SELESAI! File: {OUTPUT_FILE}")
    print(f"  Total artikel: {len(articles_data)}")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
