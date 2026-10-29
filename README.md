# Zadarske vijesti – agregator lokalnih portala

Skuplja najnovije članke s lokalnih portala preko RSS-a i prikazuje ih na jednoj
stranici: naslov, jedna slika, prve 2–3 rečenice i link na izvorni članak.

## Pokretanje

    python3 aggregator.py

Potreban je samo Python 3.9+ (bez dodatnih paketa). Rezultat je `site/index.html`,
obična statična stranica koju možeš otvoriti u pregledniku ili staviti na bilo koji hosting.

## Portali

Popis je u `feeds.json`. Za svaki portal:

- `name` – ime koje se prikazuje
- `site` – naslovnica; ako nijedan feed ne radi, skripta na njoj sama traži RSS link
- `feeds` – jedan ili više RSS/Atom URL-ova (prvi koji radi se koristi)
- `link_contains` (neobavezno) – popis; zadržava samo članke čiji link sadrži neki od tih dijelova
  (npr. Zadarski list: `["/zadar-zupanija/"]`)
- `categories` (neobavezno) – popis rubrika iz feeda koje se zadržavaju (npr. Znet: `["ZADAR / ŽUPANIJA"]`)

- `enabled` (neobavezno) – `false` privremeno isključuje portal (razlog piše u `note`)

Ako portal ima oba filtra, članak prolazi ako zadovoljava bilo koji. Bez filtra uzima se sve.

Ako feed nema sliku, skripta je uzima iz `og:image` oznake na stranici članka.

## Automatsko osvježavanje (GitHub Pages)

`.github/workflows/update.yml` svaki sat pokreće skriptu i objavljuje stranicu
na GitHub Pages. U repozitoriju uključi Settings → Pages → Source: GitHub Actions.
