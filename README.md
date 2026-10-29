# zadar.click – agregator lokalnih vijesti iz Zadra i Zadarske županije

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

- `kind` (neobavezno) – `"info"` znači da se ne prikazuje među vijestima nego u okviru (npr. Jadrolinija → Trajekti)
- `topic` (neobavezno) – tema koja se dodjeljuje svim člancima portala (npr. Sport023 → `"Sport"`)
- `scope` (neobavezno) – `"national"` za nacionalne portale: prikazuju se samo članci kojima je Zadar
  ili jasno zadarsko mjesto u naslovu ili uvodu (dvosmislena mjesta iz `places.txt`, označena s `?`, ne računaju se)
- `local_only` (neobavezno) – `false` isključuje strogi filtar mjesta za taj portal

Ako portal ima oba filtra, članak prolazi ako zadovoljava bilo koji. Bez filtra uzima se sve.

Izbačeni jer im ne možemo preuzimati vijesti: 057info (odbija automatske zahtjeve), eZadar
(ne odgovara GitHubu), HRT Radio Zadar (nema RSS), Zadar.events (odbija automatske zahtjeve).

## Strogi filtar mjesta

Svaki članak mora spominjati Zadar ili mjesto iz Zadarske županije (naslov, tekst iz feeda,
link ili rubrike). Popis mjesta je u `places.txt` i lako se dopunjuje; odjeljci `[zadar]`, `[zupanija]` i `[otoci]`
određuju i temu članka.

Ako feed nema sliku, skripta je uzima iz `og:image` oznake na stranici članka.

## Stranica

- pretraga, teme (Zadar, Županija, Otoci, Sport) i izbor portala
- isti događaj s više portala prikazuje se kao jedna kartica s linkovima "Piše i: ..."
- okviri Vrijeme u Zadru (Open-Meteo, upozorenje na jak vjetar), Trajekti (Jadrolinija) i
  Ceste i promet (vijesti o prometu iz zadnjih 36 sati + link na HAK)

## Brojanje posjeta

Napravi besplatan račun na [goatcounter.com](https://www.goatcounter.com) i upiši njegov kod
u `site.json`, npr. `{"goatcounter": "zadarskevijesti"}`.

## Automatsko osvježavanje (GitHub Pages)

`.github/workflows/update.yml` svaki sat pokreće skriptu i objavljuje stranicu
na GitHub Pages. U repozitoriju uključi Settings → Pages → Source: GitHub Actions.

## Izgled i stranice

- `template.html` je predložak; `aggregator.py` iz njega gradi naslovnicu i po jednu stranicu za svaku rubriku (`/zadar/`, `/zupanija/`, `/otoci/`, `/sport/`).
- Datoteke iz `static/` (ikone, `manifest.webmanifest`, `sw.js`) kopiraju se u `site/`, pa se stranica može instalirati na mobitel kao aplikacija (PWA) i radi i bez veze sa zadnjim učitanim vijestima.
- Adresa stranice za linkove i dijeljenje je `url` u `site.json`; promijeni je kad se spoji domena zadar.click.
