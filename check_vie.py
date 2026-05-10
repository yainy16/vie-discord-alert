import json
import os
import re
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

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
    return re.sub(r"\s+", " ", text).strip()


def extract_offer_links():
    """
    Charge la page avec Playwright, car le site peut afficher les offres via JavaScript.
    """
    links = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(LATEST_URL, wait_until="networkidle", timeout=60000)

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if "/offres/" in href and "recherche" not in href:
            if href.startswith("/"):
                href = BASE_URL + href

            if href not in links:
                links.append(href)

    return links[:20]


def get_offer_details(url):
    """
    Récupère les détails visibles d'une offre.
    Les labels peuvent changer selon le site, donc cette version reste volontairement robuste.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)

        html = page.content()
        text = page.inner_text("body")

        browser.close()

    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    title = clean_text(h1.get_text()) if h1 else "Nouvelle offre V.I.E"

    full_text = clean_text(text)

    def find_field(label_patterns):
        for pattern in label_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                return clean_text(match.group(1))
        return "Non indiqué"

    company = find_field([
        r"Entreprise\s+(.+?)\s+(Durée|Ville|Pays|Salaire|Début|Date)",
        r"Company\s+(.+?)\s+(Duration|City|Country|Salary|Start)"
    ])

    duration = find_field([
        r"Durée\s*\(mois\)\s+(\d+)",
        r"Duration\s*\(months\)\s+(\d+)"
    ])

    city = find_field([
        r"Ville\s+(.+?)\s+(Pays|Salaire|Début|Date)",
        r"City\s+(.+?)\s+(Country|Salary|Start)"
    ])

    country = find_field([
        r"Pays\s+(.+?)\s+(Salaire|Début|Date)",
        r"Country\s+(.+?)\s+(Salary|Start)"
    ])

    salary = find_field([
        r"Salaire\s+([0-9\s]+€)",
        r"Salary\s+([0-9\s]+€)"
    ])

    start_date = find_field([
        r"Début\s+(\d{2}/\d{2}/\d{4})",
        r"Start\s+(\d{2}/\d{2}/\d{4})"
    ])

    dates = re.findall(r"\d{2}/\d{2}/\d{4}", full_text)
    end_date = dates[1] if len(dates) >= 2 else "Non indiqué"

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
    links = extract_offer_links()

    new_links = [link for link in links if link not in seen]

    if not new_links:
        print("Aucune nouvelle offre.")
        return

    print(f"{len(new_links)} nouvelle(s) offre(s) détectée(s).")

    for link in reversed(new_links):
        try:
            offer = get_offer_details(link)
            send_to_discord(offer)
            seen.add(link)
            print(f"Envoyé : {offer['title']}")
        except Exception as e:
            print(f"Erreur avec {link} : {e}")

    save_seen(seen)


if __name__ == "__main__":
    main()
