import json
import os
import re
import unicodedata
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

LATEST_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche?latest=true"
BASE_URL = "https://mon-vie-via.businessfrance.fr"
SEEN_FILE = Path("seen_offers.json")


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def normalize(text):
    text = text or ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return clean_text(text).lower()


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


def get_page_text(page, url):
    print(f"Ouverture : {url}")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
    except PlaywrightTimeoutError:
        print("Timeout au chargement, on continue quand même.")

    page.wait_for_timeout(10000)

    try:
        return page.locator("body").inner_text(timeout=15000)
    except Exception:
        return ""


def extract_offer_links(page):
    get_page_text(page, LATEST_URL)

    links = []

    try:
        raw_links = page.locator("a").evaluate_all("(els) => els.map(a => a.href)")
    except Exception:
        raw_links = []

    for href in raw_links:
        if not href:
            continue

        match = re.search(r"/offres/(\d+)", href)
        if match:
            offer_url = f"{BASE_URL}/offres/{match.group(1)}"
            if offer_url not in links:
                links.append(offer_url)

    print(f"Liens trouvés : {links}")
    return links[:10]


def get_lines(text):
    return [clean_text(line) for line in text.splitlines() if clean_text(line)]


def find_after(lines, possible_labels):
    labels = [normalize(label) for label in possible_labels]

    for i, line in enumerate(lines):
        line_n = normalize(line)

        for label in labels:
            if line_n == label or line_n.startswith(label):
                for value in lines[i + 1:i + 6]:
                    value_n = normalize(value)

                    if value_n not in labels and len(value) <= 120:
                        return value

    return "Non indiqué"


def find_title(lines):
    for line in lines:
        if "(H/F)" in line or "(F/H)" in line or "(M/F)" in line:
            return line

    for line in lines:
        if len(line) > 10 and line.isupper():
            return line

    return "Nouvelle offre V.I.E"


def find_dates(text):
    dates = re.findall(r"\b\d{2}/\d{2}/\d{4}\b", text)

    # On retire les doublons en gardant l’ordre.
    unique_dates = []
    for date in dates:
        if date not in unique_dates:
            unique_dates.append(date)

    start_date = unique_dates[0] if len(unique_dates) >= 1 else "Non indiqué"
    end_date = unique_dates[1] if len(unique_dates) >= 2 else "Non indiqué"

    return start_date, end_date


def find_salary(text):
    matches = re.findall(r"\b\d{3,5}\s*€", text)

    if matches:
        return matches[-1]

    return "Non indiqué"


def find_duration(text):
    patterns = [
        r"Durée\s*\(mois\)\s*(\d+)",
        r"Durée\s*[:\-]?\s*(\d+)\s*mois",
        r"Duration\s*\(months\)\s*(\d+)",
        r"Duration\s*[:\-]?\s*(\d+)\s*months"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)

    return "Non indiqué"


def find_country_city(lines):
    countries = [
        "ETATS-UNIS", "ÉTATS-UNIS", "ALLEMAGNE", "ESPAGNE", "ITALIE", "ROYAUME-UNI",
        "CANADA", "BELGIQUE", "SUISSE", "SINGAPOUR", "THAILANDE", "THAÏLANDE",
        "JAPON", "CHINE", "HONG KONG", "AUSTRALIE", "SUEDE", "SUÈDE",
        "NORVEGE", "NORVÈGE", "DANEMARK", "PAYS-BAS", "PORTUGAL"
    ]

    for line in lines:
        line_upper = line.upper()

        for country in countries:
            if country in line_upper:
                country_clean = country.replace("É", "E").replace("È", "E")

                # Cas fréquent : ETATS-UNIS - NEW-YORK -NY-
                if " - " in line:
                    parts = [clean_text(part) for part in line.split(" - ") if clean_text(part)]
                    if len(parts) >= 2:
                        return country_clean, " - ".join(parts[1:])

                return country_clean, "Non indiqué"

    return "Non indiqué", "Non indiqué"


def get_offer_details(page, url):
    text = get_page_text(page, url)
    lines = get_lines(text)

    print("----- TEXTE LU SUR LA PAGE -----")
    for line in lines[:80]:
        print(line)
    print("----- FIN TEXTE LU -----")

    title = find_title(lines)

    company = find_after(lines, ["Entreprise", "Company"])
    duration = find_duration(text)

    country = find_after(lines, ["Pays", "Country"])
    city = find_after(lines, ["Ville", "City"])

    fallback_country, fallback_city = find_country_city(lines)

    if country == "Non indiqué":
        country = fallback_country

    if city == "Non indiqué":
        city = fallback_city

    salary = find_after(lines, ["Salaire", "Indemnité", "Allowance", "Salary"])

    if salary == "Non indiqué":
        salary = find_salary(text)

    start_date = find_after(lines, ["Début", "Date de début", "Start", "Start date"])
    end_date = find_after(lines, ["Fin", "Date de fin", "End", "End date"])

    fallback_start, fallback_end = find_dates(text)

    if start_date == "Non indiqué":
        start_date = fallback_start

    if end_date == "Non indiqué":
        end_date = fallback_end

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


def send_to_discord(offer):
    payload = {
        "username": "Alerte VIE",
        "embeds": [
            {
                "title": offer["title"],
                "url": offer["url"],
                "color": 3447003,
                "fields": [
                    {"name": "Entreprise", "value": offer["company"], "inline": False},
                    {"name": "Durée (mois)", "value": offer["duration"], "inline": True},
                    {"name": "Ville", "value": offer["city"], "inline": True},
                    {"name": "Pays", "value": offer["country"], "inline": True},
                    {"name": "$ Salaire", "value": offer["salary"], "inline": True},
                    {"name": "Début", "value": offer["start_date"], "inline": True},
                    {"name": "Fin", "value": offer["end_date"], "inline": True},
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
            args=["--no-sandbox", "--disable-dev-shm-usage"]
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

        for link in reversed(new_links[:5]):
            offer_page = context.new_page()

            try:
                offer = get_offer_details(offer_page, link)
                send_to_discord(offer)
                seen.add(link)

                print(f"Envoyé : {offer['title']}")

            except Exception as e:
                print(f"Erreur avec {link} : {e}")

            finally:
                offer_page.close()

        browser.close()

    save_seen(seen)


if __name__ == "__main__":
    main()
