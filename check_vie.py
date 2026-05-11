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


def french_date_to_number(date_text):
    months = {
        "janvier": "01",
        "fevrier": "02",
        "février": "02",
        "mars": "03",
        "avril": "04",
        "mai": "05",
        "juin": "06",
        "juillet": "07",
        "aout": "08",
        "août": "08",
        "septembre": "09",
        "octobre": "10",
        "novembre": "11",
        "decembre": "12",
        "décembre": "12",
    }

    match = re.search(
        r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})",
        date_text,
        re.IGNORECASE,
    )

    if not match:
        return clean_text(date_text)

    day = match.group(1).zfill(2)
    month_text = normalize(match.group(2))
    year = match.group(3)

    month = months.get(month_text)

    if not month:
        return clean_text(date_text)

    return f"{day}/{month}/{year}"


def get_page_text(page, url):
    print(f"Ouverture : {url}")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
    except PlaywrightTimeoutError:
        print("Timeout au chargement, on continue quand même.")

    page.wait_for_timeout(10000)

    try:
        cookie_button = page.get_by_text("Tout refuser", exact=True)
        if cookie_button.count() > 0:
            cookie_button.first.click(timeout=3000)
            page.wait_for_timeout(2000)
    except Exception:
        pass

    try:
        return page.locator("body").inner_text(timeout=15000)
    except Exception:
        return ""


def get_lines(text):
    return [clean_text(line) for line in text.splitlines() if clean_text(line)]


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

    print(f"{len(links)} lien(s) trouvé(s).")
    print(links)

    return links[:10]


def find_title(lines):
    for line in lines:
        if "(H/F)" in line or "(F/H)" in line or "(H/F/X)" in line or "(H/F/N)" in line:
            return line

    return "Nouvelle offre V.I.E"


def find_company(lines, title):
    for line in lines:
        if normalize(line).startswith("etablissement"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                return clean_text(parts[1])

    if title in lines:
        index = lines.index(title)

        if index > 0:
            possible_company = lines[index - 1]

            bad_values = [
                "retour",
                "favoris",
                "postuler",
                "tout accepter",
                "tout refuser",
                "se connecter",
            ]

            if normalize(possible_company) not in bad_values:
                return possible_company

    return "Non indiqué"


def parse_location_from_line(line):
    line = clean_text(line)

    if "(H/F)" in line or "(F/H)" in line:
        return None, None

    if " VIE " in line and "mois" in line.lower():
        return None, None

    match = re.search(r"^(.+?)\s*\((.+?)\)$", line)

    if match:
        country = clean_text(match.group(1))
        city = clean_text(match.group(2))
        return country, city

    if " - " in line:
        parts = [clean_text(part) for part in line.split(" - ") if clean_text(part)]

        if len(parts) >= 2:
            country = parts[0]
            city = " - ".join(parts[1:])
            return country, city

    return None, None


def find_mission_block(lines):
    for i, line in enumerate(lines):
        if normalize(line) == "la mission":
            return lines[i + 1:i + 15]

    return []


def find_country_city(lines):
    mission_block = find_mission_block(lines)

    for line in mission_block:
        country, city = parse_location_from_line(line)

        if country and city:
            return country, city

    for line in lines:
        country, city = parse_location_from_line(line)

        if country and city:
            return country, city

    return "Non indiqué", "Non indiqué"


def find_dates_and_duration(lines):
    mission_block = find_mission_block(lines)

    all_lines = mission_block + lines

    for line in all_lines:
        match = re.search(
            r"du\s+(.+?)\s+au\s+(.+?)\s*\((\d+)\s*mois\)",
            line,
            re.IGNORECASE,
        )

        if match:
            start_date = french_date_to_number(match.group(1))
            end_date = french_date_to_number(match.group(2))
            duration = match.group(3)
            return start_date, end_date, duration

    for line in all_lines:
        match = re.search(
            r"du\s+(.+?)\s+au\s+(.+)",
            line,
            re.IGNORECASE,
        )

        if match:
            start_date = french_date_to_number(match.group(1))
            end_date = french_date_to_number(match.group(2))
            return start_date, end_date, "Non indiqué"

    duration = "Non indiqué"

    for line in lines:
        match = re.search(r"\b(\d+)\s*mois\b", line, re.IGNORECASE)

        if match:
            duration = match.group(1)
            break

    return "Non indiqué", "Non indiqué", duration


def find_salary(lines):
    for line in lines:
        if "REMUNERATION MENSUELLE" in line.upper():
            match = re.search(r"([0-9][0-9\s]*(?:[.,]\d+)?)\s*€", line)

            if match:
                return clean_text(match.group(1)) + " €"

    for line in lines:
        match = re.search(r"([0-9][0-9\s]*(?:[.,]\d+)?)\s*€", line)

        if match:
            return clean_text(match.group(1)) + " €"

    return "Non indiqué"


def get_offer_details(page, url):
    text = get_page_text(page, url)
    lines = get_lines(text)

    print("----- TEXTE LU SUR LA PAGE -----")
    for line in lines[:120]:
        print(line)
    print("----- FIN TEXTE LU -----")

    title = find_title(lines)
    company = find_company(lines, title)
    country, city = find_country_city(lines)
    start_date, end_date, duration = find_dates_and_duration(lines)
    salary = find_salary(lines)

    return {
        "title": title,
        "company": company,
        "duration": duration,
        "city": city,
        "country": country,
        "salary": salary,
        "start_date": start_date,
        "end_date": end_date,
        "url": url,
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
                        "inline": False,
                    },
                ],
                "footer": {
                    "text": "Alerte VIE • Business France"
                },
            }
        ],
    }

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)

    if response.status_code not in [200, 204]:
        raise Exception(f"Erreur Discord : {response.status_code} - {response.text}")


def main():
    seen = load_seen()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        context = browser.new_context(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
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
