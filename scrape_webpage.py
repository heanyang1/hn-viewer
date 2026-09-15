import glob
import os
import re
import cloudscraper
import html2text


def fetch_html(url):
    scraper = cloudscraper.create_scraper()
    try:
        resp = scraper.get(url, timeout=60)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"Request failed: {e}")
        return None


def html_to_text(html):
    h = html2text.HTML2Text()
    h.ignore_images = True
    h.ignore_emphasis = True
    h.ignore_links = True
    h.ignore_mailto_links = True
    h.ignore_tables = True
    h.body_width = 0
    text = h.handle(html)
    title = re.search(r"^#[^#]\s*(.*)", text, flags=re.MULTILINE)
    if title is not None:
        title = title.group(1)
        title = re.sub(r"[^\w_.-]", "_", title)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    return text, title


def scrape_webpage(url):
    html = fetch_html(url)
    if html is None:
        return None, None
    return html_to_text(html)


def process_url(url, index):
    if glob.glob(f"text/*_{index}.txt"):
        print(f"skipping already scraped [{index}]: {url}")
        return

    print("start scraping", url)
    text, title = scrape_webpage(url)
    print("finish scraping", url, title)

    if not os.path.exists("text"):
        os.makedirs("text")

    if not text:
        print(f"no text scraped for [{index}]: {url}")
        return

    name = title if title else f"unnamed_{index}"
    out_path = f"text/{name}_{index}.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"saved {out_path}")


if __name__ == "__main__":
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import sys

    dry_run = "--dry-run" in sys.argv
    if dry_run:
        sys.argv.remove("--dry-run")

    if len(sys.argv) < 2:
        print("Usage: python scrape_webpage.py [--dry-run] <file_with_urls>")
        sys.exit(1)

    with open(sys.argv[1], "r") as f:
        webpages = [line.strip() for line in f.readlines()]

    if dry_run:
        print(f"Dry-run: would scrape {len(webpages)} URLs")
        for i, url in enumerate(webpages):
            if glob.glob(f"text/*_{i}.txt"):
                print(f"  [{i}] already scraped, would skip: {url}")
            else:
                print(f"  [{i}] would scrape: {url}")
        sys.exit(0)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(process_url, url, i): (i, url)
            for i, url in enumerate(webpages)
        }
        for future in as_completed(futures):
            i, url = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"Error processing {url}: {e}")
