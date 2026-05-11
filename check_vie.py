import json
import os
import re
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

LATEST_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche?latest=true"
BASE_URL = "https://mon-vie-via.businessfrance.fr"
SEEN_FILE = Path("seen_offers.json")


def load_seen():
    if not SEEN_FILE.exists():
        return set()

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def normalize(text):
    text = text or ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return clean_text(text).lower()


def get_rendered_page(page, url):
    print(f"Ouverture de la page : {url}")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
    except PlaywrightTimeoutError:
        print("La page a mis trop longtemps à charger, mais on essaie quand même de lire le contenu.")

    page.wait_for_timeout(8000)

    text = ""
    html = ""

    for _ in range(12):
        try:
            text = page.locator("body").inner_text(timeout=10000)
            html = page.content()

            if len(text.strip()) > 300 and not text.strip().lower().startswith("loading"):
                return text, html, page.url
        except Exception:
            pass

        page.wait_for_timeout(2000)

    return text, html, page.url


def extract_offer_links(page):
    text, html, final_url = get_rendered_page(page, LATEST_URL)

    links = []

    try:
        raw_links = page.locator("a").evaluate_all("(els) => els.map(a => a.href)")
    except Exception:
        raw_links = []

    for href in raw_links:
        match = re.search(r"https://mon-vie-via\.businessfrance\.fr/(?:en/)?offres/(\d+)", href)
        if match:
            offer_url = f"{BASE_URL}/offres/{match.group(1)}"
            if offer_url not in links:
                links.append(offer_url)

    for offer_id in re.findall(r"/(?:en/)?offres/(\d+)", html):
        offer_url = f"{BASE_URL}/offres/{offer_id}"
        if offer_url not in links:
            links.append(offer_url)

    print(f"{len(links)} lien(s) d'offre trouvé(s).")

    return links[:20]


def lines_from_text(text):
    return [clean_text(line) for line in text.splitlines() if clean_text(line)]


def get_value_after_label(lines, labels):
    normalized_labels = [normalize(label) for label in labels]

    known_labels = [
        "Entreprise", "Company",
        "Durée", "Durée (mois)", "Duration", "Duration (months)",
        "Ville", "City",
        "Pays", "Country",
        "Salaire", "Salary", "Indemnité", "Allowance",
        "Début", "Start", "Date de début", "Start date",
        "Fin", "End", "Date de fin", "End date",
        "Lien", "Link"
    ]

    normalized_known_labels = [normalize(label) for label in known_labels]

    for i, line in enumerate(lines):
        normalized_line = normalize(line)

        for label in normalized_labels:
            if normalized_line == label or normalized_line.startswith(label + " "):
                for candidate in lines[i + 1:i + 5]:
                    normalized_candidate = normalize(candidate)

                    if normalized_candidate not in normalized_known_labels:
                        if len(candidate) <= 120:
                            return candidate

    return "Non indiqué"


def find_title(lines, html):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    if h1 and clean_text(h1.get_text()):
        return clean_text(h1.get_text())

    title_patterns = [
        r".+\(H/F\)",
        r".+\(F/H\)",
        r".+\(M/F\)",
        r".+\(H/F/X\)"
    ]

    for line in lines:
        for pattern in title_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                return line

    return "Nouvelle offre V.I.E"


def find_company_fallback(lines, title):
    if title not in lines:
        return "Non indiqué"

    title_index = lines.index(title)

    for candidate in reversed(lines[max(0, title_index - 5):title_index]):
        candidate_normalized = normalize(candidate)

        if candidate_normalized not in ["logo entreprise", "image", "dernieres offres", "last offers"]:
            if len(candidate) <= 80:
                return candidate

    return "Non indiqué"


def find_location_fallback(lines, title):
    if title not in lines:
        return "Non indiqué", "Non indiqué"

    title_index = lines.index(title)

    for candidate in lines[title_index + 1:title_index + 8]:
        if " - " in candidate and len(candidate) <= 100:
            parts = candidate.split(" - ", 1)
            if len(parts) == 2:
                country = clean_text(parts[0])
                city = clean_text(parts[1])
                return country, city

    return "Non indiqué", "Non indiqué"


def get_offer_details(page, url):
    text, html, final_url = get_rendered_page(page, url)
    lines = lines_from_text(text)

    title = find_title(lines, html)

    company = get_value_after_label(lines, ["Entreprise", "Company"])
    duration = get_value_after_label(lines, ["Durée (mois)", "Durée", "Duration (months)", "Duration"])
    city = get_value_after_label(lines, ["Ville", "City"])
    country = get_value_after_label(lines, ["Pays", "Country"])
    salary = get_value_after_label(lines, ["Salaire", "Salary", "Indemnité", "Allowance"])
    start_date = get_value_after_label(lines, ["Début", "Date de début", "Start", "Start date"])
    end_date = get_value_after_label(lines, ["Fin", "Date de fin", "End", "End date"])

    if company == "Non indiqué":
        company = find_company_fallback(lines, title)

    fallback_country, fallback_city = find_location_fallback(lines, title)

    if country == "Non indiqué":
        country = fallback_country

    if city == "Non indiqué":
        city = fallback_city

    dates = re.findall(r"\b\d{2}/\d{2}/\d{4}\b", text)

    if start_date == "Non indiqué" and len(dates) >= 1:
        start_date = dates[0]

    if end_date == "Non indiqué" and len(dates) >= 2:
        end_date = dates[1]

    salary_match = re.search(r"\b\d{3,5}\s*€", text)

    if salary == "Non indiqué" and salary_match:
        salary = salary_match.group(0)

    return {
        "title": title,
        "company": company,
        "duration": duration,
        "city": city,
        "country": country,
        "salary": salary,
        "start_date": start_date,
        "end_date": end_date,
        "url": url
    }


def offer_is_valid(offer):
    if offer["title"] == "Nouvelle offre V.I.E":
        return False

    fields = [
        offer["company"],
        offer["duration"],
        offer["city"],
        offer["country"],
        offer["salary"],
        offer["start_date"],
        offer["end_date"]
    ]

    non_indique_count = fields.count("Non indiqué")

    return non_indique_count < len(fields)


def send_to_discord(offer):
    payload = {
        "username": "Alerte VIE",
        "embeds": [
            {
                "title": offer["title"],
                "url": offer["url"],
                "color": 3447003,
                "fields": [
                    {
                        "name": "Entreprise",
                        "value": offer["company"],
                        "inline": False
                    },
                    {
                        "name": "Durée (mois)",
                        "value": offer["duration"],
                        "inline": True
                    },
                    {
                        "name": "Ville",
                        "value": offer["city"],
                        "inline": True
                    },
                    {
                        "name": "Pays",
                        "value": offer["country"],
                        "inline": True
                    },
                    {
                        "name": "$ Salaire",
                        "value": offer["salary"],
                        "inline": True
                    },
                    {
                        "name": "Début",
                        "value": offer["start_date"],
                        "inline": True
                    },
                    {
                        "name": "Fin",
                        "value": offer["end_date"],
                        "inline": True
                    },
                    {
                        "name": "Lien",
                        "value": f"[Voir l'offre sur Business France]({offer['url']})",
                        "inline": False
                    }
                ],
                "footer": {
                    "text": "Alerte VIE • Business France"
                }
            }
        ]
    }

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)

    if response.status_code not in [200, 204]:
        raise Exception(f"Erreur Discord : {response.status_code} - {response.text}")


def main():
    seen = load_seen()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        context = browser.new_context(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        page = context.new_page()
        links = extract_offer_links(page)

        new_links = [link for link in links if link not in seen]

        if not new_links:
            print("Aucune nouvelle offre.")
            browser.close()
            return

        print(f"{len(new_links)} nouvelle(s) offre(s) détectée(s).")

        for link in reversed(new_links[:10]):
            offer_page = context.new_page()

            try:
                offer = get_offer_details(offer_page, link)

                if not offer_is_valid(offer):
                    print(f"Offre ignorée car extraction incomplète : {link}")
                    continue

                send_to_discord(offer)
                seen.add(link)

                print(f"Envoyé sur Discord : {offer['title']}")

            except Exception as e:
                print(f"Erreur avec {link} : {e}")

            finally:
                offer_page.close()

        browser.close()

    save_seen(seen)


if __name__ == "__main__":
    main()
