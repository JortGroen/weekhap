# Kistje Vol Smaak — autonome datapijplijn

Haalt de actuele inhoud van het **Hoeve Biesland / Kistje Vol Smaak** uit de
publieke WordPress REST API, valideert en normaliseert die, en publiceert het
resultaat als JSON via GitHub Pages.

Na deployment is er geen handmatige invoer, plugin, login, API-key of draaiende
eigen computer meer nodig.

```
Hoeve Biesland WordPress REST API
        -> GitHub Actions (fetch met retries)
        -> parser + validator
        -> genormaliseerde week-JSON
        -> commit naar deze repository
        -> GitHub Pages
        -> maaltijdplanner
```

## Publieke endpoints

Vervang `<user>` en `<repo>`:

```
https://<user>.github.io/<repo>/api/status.json
https://<user>.github.io/<repo>/api/latest.json
https://<user>.github.io/<repo>/api/by-week/2026-W35.json
```

Weeksleutels gebruiken **ISO-jaar + ISO-week** (`2026-W35`). Rond de
jaarwisseling lopen ISO-jaar en kalenderjaar uiteen; de ISO-variant voorkomt dat
`2026-W01` en `2025-W01` door elkaar lopen.

## Contract voor de maaltijdplanner

Een kistje wordt op **donderdag** opgehaald en mag vanaf dat moment worden
gebruikt. De relevante week is dus de ISO-week van de ophaaldonderdag, niet die
van de planstart:

```python
from datetime import date
from src.planner_week import week_key_for_plan_start

week_key_for_plan_start(date(2026, 8, 31))   # maandag -> "2026-W35"
```

| Planstart | Ophaaldonderdag | Weeksleutel |
|---|---|---|
| do 2026-08-27 | 2026-08-27 | `2026-W35` |
| ma 2026-08-31 | 2026-08-27 | `2026-W35` |
| do 2026-09-03 | 2026-09-03 | `2026-W36` |

Stappen:

1. bepaal de planstart;
2. bepaal de meest recente donderdag op of vóór de planstart;
3. bereken de ISO-week van die donderdag;
4. haal `api/by-week/<weeksleutel>.json` op;
5. controleer `schema_version`;
6. controleer dat `week` exact overeenkomt met de gevraagde sleutel;
7. controleer `source.modified` en `source.fetched_at`;
8. gebruik `vegetables` + `fruit`;
9. voeg `planner_additions` toe (de vaste 6 eieren);
10. ga daarna pas verder met recepten en boodschappen.

Bestaat het weekbestand niet, probeer dan `latest.json` — maar **accepteer
alleen data waarvan de week-sleutel exact overeenkomt**. Een oudere week mag
nooit als vervanging voor een ontbrekende actuele week worden gebruikt, en
kistinhoud mag nooit worden verzonnen.

### Kistgrootte

Het huishouden gebruikt een **2-persoonskistje**. Producten met een `*` in de
bron zitten níét in het 1-persoonskistje, maar wél in het 2-persoons. In de JSON:

- `excluded_from_one_person_box` — de `*`-markering uit de bron;
- `included_in_two_person_box` — altijd `true`; dit is het veld dat de planner
  moet gebruiken.

## Lokaal draaien

```bash
pip install -r requirements.txt
python -m pytest -q                              # unit- en fixture-tests
python -m src.build                              # live ophalen en publiceren
python -m src.build --from-file tests/fixtures/wordpress_response.json
```

## Modules

| Bestand | Verantwoordelijkheid |
|---|---|
| `src/fetch_kistje.py` | HTTP-GET met timeout en 3 retries (10/30/90s) |
| `src/parse_kistje.py` | HTML + Visual Composer-shortcodes → weeksecties |
| `src/normalize_kistje.py` | jaarinferentie, validatie, JSON-documenten |
| `src/planner_week.py` | ophaaldonderdag en ISO-weeksleutel |
| `src/build.py` | orchestratie en schrijven naar `docs/` |
| `src/verify_published.py` | live E2E-check tegen de publieke Pages-URL |

## Jaarinferentie

De bron noemt alleen `Week 35`, nooit het jaar. Het jaar wordt afgeleid uit de
datumrange naast het weeknummer (voor het juiste ISO-jaar valt de maandag van
die week exact op de genoemde startdatum), met `modified` als terugvaloptie.

`datetime.now().year` wordt nooit gebruikt: dat levert rond de jaarwisseling
stilletjes de verkeerde week op.

## Schedule

De workflow draait 5× per dag (`17 6,9,12,15,18 * * *` UTC) plus handmatig via
`workflow_dispatch`.

De waargenomen wijziging van de bron was **donderdag 27-08-2026 om 12:20** —
niet vrijdag 12:00, zoals soms wordt aangenomen. Eén waarneming is echter te
weinig om een ritme op vast te leggen, dus wordt er bewust breed gecontroleerd.
`status.json` houdt in `observed_modified_history` de laatste 20 waargenomen
`modified`-waarden bij; daarmee is het publicatieritme na een paar weken
meetbaar en kan de frequentie onderbouwd omlaag.

## Commit-gedrag en `last_success`

Bestanden worden alleen herschreven als de **inhoudelijke** data verandert;
`fetched_at` en `last_success` worden bij die vergelijking genegeerd. Zonder dat
zou elke run een commit opleveren — 5 per dag, terwijl de bron ongeveer eens per
week wijzigt.

Gevolg: `last_success` in `status.json` is het tijdstip van de laatste run die
een *wijziging* opleverde, niet van de laatste geslaagde controle. Wie wil weten
of de pijplijn nú nog draait, kijkt in de Actions-tab; dat is conform §18 de
eerste monitoringlaag. De inhoudelijke versheid van de data staat in
`source_modified`.

## Last-known-good

Bij een mislukte fetch, parserfout of validatiefout wordt er **niets** in
`docs/` aangeraakt: geen leeggemaakte `latest.json`, geen verwijderde
weekbestanden, geen gedeeltelijke data. De workflow eindigt rood, de ruwe
response wordt als artifact bewaard voor debugging, en de volgende schedule-run
probeert het opnieuw.

## GitHub Pages instellen

```
Settings → Pages → Deploy from branch
Branch: main
Folder: /docs
```

De E2E-verificatiejob leidt de publieke URL af uit `GITHUB_REPOSITORY`. Wijkt je
Pages-URL daarvan af (bijvoorbeeld bij een custom domain), zet dan een
repository variable `PAGES_BASE_URL`, bijvoorbeeld
`https://gebruiker.github.io/repo`.

Er worden geen secrets of persoonsgegevens opgeslagen; de repository mag publiek
zijn. De workflow gebruikt alleen de standaard `GITHUB_TOKEN` met
`permissions: contents: write`. Broninhoud wordt uitsluitend als data behandeld:
er wordt geen HTML of script uit `content.rendered` uitgevoerd.

## Consumerclient voor de publieke API

`src/kistje_client.py` is een lichtgewicht referentieclient voor het
gepubliceerde contract: read-only HTTP GET naar de publieke Pages-URL, meer
niet. Hij is niet aan een specifieke afnemer gebonden en wordt binnen deze
repository gebruikt door `tests/test_e2e.py` om de publieke JSON uit te
oefenen.

```python
from datetime import date
from src.kistje_client import get_box_for_plan_start

box = get_box_for_plan_start(date(2026, 8, 31))   # maandag

box.week            # "2026-W35"
box.vegetables      # 4 producten
box.fruit           # 4 producten
box.produce         # alle 8, het volledige 2-persoonskistje
box.shopping_items()  # kistinhoud + de vaste 6 eieren
```

Vanaf de commandline:

```bash
python -m src.kistje_client --plan-start 2026-08-31
python -m src.kistje_client --week 2026-W35
```

### Toegangsbeperking

De base URL is configureerbaar, maar wordt gevalideerd voordat er een request
uitgaat: alleen `https`, alleen host `jortgroen.github.io`, alleen paden onder
`/weekhap/api/`. Een verkeerd geconfigureerde base URL faalt dus meteen in
plaats van ergens anders data op te halen.

### Foutgedrag

Bij een mislukte request, ongeldige JSON of een week die niet exact overeenkomt
geeft de client een expliciete fout — `KistjeUnavailable` of
`KistjeContractError`. Er wordt **nooit** stilzwijgend teruggevallen op een
andere week en **nooit** kistinhoud verzonnen.

Retry is beperkt en selectief: tijdelijke problemen (timeout, DNS, 5xx) worden
maximaal 3× geprobeerd met 1s en 3s backoff. Een **404 wordt niet herhaald** —
die betekent dat de week niet gepubliceerd is, en dat verandert niet door het
nog eens te vragen.

## OpenAPI-schema voor een AI-assistent

Draait de maaltijdplanner als custom GPT of AI-assistent met tool-calling, dan
kan die het schema rechtstreeks importeren:

```
https://jortgroen.github.io/weekhap/openapi.json
```

Twee read-only operaties: `getKistjeWeek` (kistinhoud per ISO-week) en
`getKistjeStatus` (welke weken beschikbaar zijn). Geen authenticatie.

Geef de assistent daarbij deze regels mee:

> Het kistje wordt op donderdag opgehaald. Bepaal de meest recente donderdag op
> of vóór de planstart, neem het ISO-jaar en ISO-weeknummer van die donderdag, en
> roep `getKistjeWeek` aan met bijvoorbeeld `2026-W35`. Controleer dat het veld
> `week` exact overeenkomt met wat je opvroeg. Gebruik alle producten uit
> `vegetables` en `fruit` — dit is een 2-persoonskistje, dus producten met
> `excluded_from_one_person_box: true` horen er wél bij. Voeg de 6 eieren uit
> `planner_additions` toe. Krijg je een 404 of komt de week niet overeen, meld
> dat dan expliciet; gebruik nooit een andere week en verzin nooit kistinhoud.

## Tests

```bash
python -m pytest -q -m "not live"      # unit- en fixturetests, geen netwerk
python -m pytest tests/test_e2e.py -v -s   # echte HTTP naar de publieke URL
```

## Scope van deze repository

De kern is smal en bewust zo gehouden:

```
fetch → parse → validate → publish → verify
```

De pijplijn hangt van geen enkele consument af. `src/kistje_client.py` en
`docs/openapi.json` zijn optionele hulpmiddelen voor wie de data afneemt, geen
onderdeel van de publicatieketen. De client wordt wel door `tests/test_e2e.py`
gebruikt om de publieke JSON te verifiëren; `openapi.json` is puur documentatie
van het gepubliceerde contract en wordt door niets in deze repo aangeroepen.
Beide kunnen worden verwijderd zonder dat de pijplijn stopt met werken.

## Versheid bewijzen na publicatie

GitHub Pages is eventually consistent: na de push kan het publieke eindpunt nog
even de vórige versie serveren. Die oude versie heeft een geldig schema en een
geldig contract en zou dus zonder extra bewijs groen worden — de workflow zou
succes melden terwijl de nieuwe data nog nergens staat.

Daarom draagt elk weekbestand een `content_hash`: een SHA-256 over de inhoud,
berekend **zonder** `source.fetched_at` en zonder het hashveld zelf. Twee runs
over dezelfde bron geven daardoor exact dezelfde hash. `status.json` bevat de
`week_hashes` plus een `publication_hash` over die hashes samen: één waarde die
de gehele publicatie identificeert.

```
update-job    publiceert → leest publication_hash uit status.json
              → geeft door als job-output
verify-job    krijgt EXPECTED_PUBLICATION_HASH binnen
              → pollt status.json tot die hash wordt geserveerd
                (backoff 15/30/60/90/120s, ruim vijf minuten)
              → controleert per week dat content_hash overeenkomt
              → herberekent de hash uit de geserveerde inhoud
              → faalt expliciet als Pages de oude versie blijft leveren
```

`fetched_at` wordt bewust **niet** als versheidssignaal gebruikt: dat is
runtime-metadata en zegt niets over de identiteit van de bron. De hash doet dat
wel, en de herberekening bewijst bovendien dat de meegeleverde hash de inhoud
werkelijk dekt.

Bij een handmatige run buiten de workflow is de variabele leeg; dan wordt alleen
het contract gecontroleerd en niet op versheid gewacht.

## Keepalive voor scheduled workflows

GitHub documenteert:

> In a public repository, scheduled workflows are automatically disabled when no
> repository activity has occurred in 60 days.

Dit geldt **alleen voor publieke repositories** — en deze is publiek, dus het is
van toepassing. GitHub documenteert echter **niet** wat als "repository
activity" telt. Of de wekelijkse commits van `github-actions[bot]` meetellen is
dus onbekend, en de autonome werking mag daar niet van afhangen.

`.github/workflows/keepalive.yml` draait daarom maandelijks (`23 4 3 * *`) en:

1. controleert via de Actions API of `update-kistje.yml` nog `state: active` is
   en faalt luid als dat niet zo is;
2. schrijft een tijdstempel naar `.github/keepalive` en commit dat met
   `chore: scheduled workflow keepalive [skip ci]`.

Bewust géén aanpassing van `latest.json`, `status.json` of historische
weekbestanden: keepalive-activiteit blijft geïsoleerd en herkenbaar, en de
gepubliceerde data krijgt geen ruis.

**Dit is best-effort, geen garantie.** Telt GitHub botcommits niet als
activiteit, dan wordt ook deze workflow uitgeschakeld — een scheduled workflow
kan zichzelf niet uit die toestand redden. De statuscheck maakt het zichtbaar.

**Handmatig herstellen** als scheduled runs toch stoppen: open de workflow in de
Actions-tab en kies *Enable workflow*, of draai hem één keer via *Run workflow*
(`workflow_dispatch`). Elke gewone push werkt ook. Daarna loopt het schema weer.
