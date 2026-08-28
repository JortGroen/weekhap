# Projectplan — Autonome Kistje Vol Smaak-data via GitHub Actions

## 1. Doel

Bouw een volledig autonome, pluginloze datapijplijn die de actuele inhoud van het **Hoeve Biesland / Kistje Vol Smaak** rechtstreeks uit de openbare WordPress REST API haalt, valideert, normaliseert en publiceert als eenvoudige JSON-bestanden die de maaltijdplanner betrouwbaar kan uitlezen.

Na initiële deployment is **geen handmatige invoer, copy/paste, actieve computer, ChatGPT-plugin of externe login** nodig.

## 2. Uitgangspunten

### Bron

Primaire bron:

```text
https://hoevebiesland.nl/wp-json/wp/v2/pages/19172
```

Alternatieve discovery-route:

```text
https://hoevebiesland.nl/wp-json/wp/v2/pages?slug=deze-week-in-je-kistje
```

Relevante velden uit de WordPress-response:

- `id` = `19172`
- `modified`
- `slug`
- `link`
- `content.rendered`

`content.rendered` bevat de twee gepubliceerde weken, met per week:

- weeknummer;
- datumrange;
- groente;
- fruit;
- productherkomst;
- bio/biodynamisch;
- `*`-markering voor producten die niet in het 1-persoonskistje zitten.

### Plannerregels

- Het huishouden gebruikt een **2-persoons groente & fruit-kistje**.
- Producten met `*` horen dus **wel** bij het kistje.
- Er zitten volgens de vaste plannerregel **altijd 6 eieren** bij het kistje.
- Een kistje wordt op **donderdag** opgehaald.
- Het donderdagkistje mag vanaf die donderdag worden gebruikt.
- Als de maaltijdplanning pas maandag start, wordt het kistje van de **donderdag ervoor** gebruikt.
- De relevante kistweek is daarom de ISO-week van de relevante ophaaldonderdag, niet automatisch de ISO-week van de eerste maandag van het maaltijdplan.

Voorbeeld:

```text
planstart:      maandag 31-08-2026
ophaaldag:      donderdag 27-08-2026
relevante week: 2026-W35
```

## 3. Gewenste architectuur

```text
Hoeve Biesland WordPress REST API
                |
                v
       GitHub Actions runner
                |
        fetch + retries
                |
                v
       parser + validator
                |
                v
      normalized week JSON
                |
        commit naar repository
                |
                v
          GitHub Pages
                |
                v
         Maaltijdplanner
```

Alles draait op GitHub-hosted infrastructuur.

## 4. Repositorystructuur

Maak een repository met minimaal:

```text
.
├── .github/
│   └── workflows/
│       └── update-kistje.yml
├── src/
│   ├── fetch_kistje.py
│   ├── parse_kistje.py
│   ├── normalize_kistje.py
│   └── planner_week.py
├── tests/
│   ├── fixtures/
│   │   └── wordpress_response.json
│   ├── test_parser.py
│   ├── test_week_selection.py
│   └── test_e2e_fixture.py
├── docs/
│   └── api/
│       ├── latest.json
│       ├── status.json
│       └── by-week/
├── requirements.txt
├── README.md
└── .gitignore
```

Gebruik `docs/` als GitHub Pages-root zodat er geen aparte deploymentservice nodig is.

## 5. Fetch-implementatie

### 5.1 Request

`src/fetch_kistje.py` moet:

1. `GET https://hoevebiesland.nl/wp-json/wp/v2/pages/19172` uitvoeren.
2. Header gebruiken:

```text
Accept: application/json
User-Agent: maaltijd-planner-kistje-fetch/1.0
```

3. Timeout instellen, bijvoorbeeld 20 seconden.
4. Alleen HTTP 2xx accepteren.
5. JSON valideren voordat verwerking start.

### 5.2 Retries

Gebruik minimaal 3 pogingen met backoff, bijvoorbeeld:

```text
10 seconden
30 seconden
90 seconden
```

Een tijdelijke netwerkfout mag de laatst bekende goede dataset niet overschrijven.

### 5.3 Geen secrets

De Hoeve Biesland API is publiek. Gebruik hiervoor geen API-key en geen repository secret.

Gebruik voor commits de standaard GitHub Actions `GITHUB_TOKEN`.

## 6. Parser

### 6.1 Input

Parse:

```python
response["content"]["rendered"]
```

De inhoud bevat HTML gecombineerd met Visual Composer-shortcodes.

### 6.2 Parserstrategie

Vermijd één grote fragiele regex.

Aanbevolen:

1. HTML entities decoderen met `html.unescape`.
2. Weeksecties vinden op:

```html
<h3><strong>Week 35</strong></h3>
```

3. Iedere weeksectie isoleren tot de volgende `Week N`-sectie.
4. Binnen iedere sectie de `<li>`-elementen uitlezen.
5. Producten scheiden op basis van de omliggende labels:
   - `Kistjes Groente`
   - `Kistje Fruit`
6. Productnaam uit `<b>` / `<strong>` halen.
7. Volledige regel bewaren voor oorsprong/certificering.
8. `*` afzonderlijk opslaan.

Gebruik bijvoorbeeld `beautifulsoup4` voor HTML-verwerking.

## 7. Genormaliseerd datamodel

Publiceer intern per product bijvoorbeeld:

```json
{
  "name": "Regenboogwortel",
  "category": "vegetable",
  "origin": "Familie Hospers uit de Noordoostpolder",
  "certification": "biologisch",
  "excluded_from_one_person_box": true,
  "included_in_two_person_box": true
}
```

Weekbestand:

```json
{
  "schema_version": 1,
  "week": "2026-W35",
  "week_number": 35,
  "source": {
    "page_id": 19172,
    "url": "https://hoevebiesland.nl/kistjevolsmaak/deze-week-in-je-kistje/",
    "api_url": "https://hoevebiesland.nl/wp-json/wp/v2/pages/19172",
    "modified": "2026-08-27T12:20:54",
    "fetched_at": "2026-08-28T13:15:00+02:00"
  },
  "date_range_text": "24 t/m 30 aug",
  "vegetables": [],
  "fruit": [],
  "planner_additions": [
    {
      "name": "Eieren",
      "quantity": 6,
      "unit": "stuks",
      "origin": "planner_rule"
    }
  ]
}
```

Gebruik overal ISO-jaar + weeknummer (`2026-W35`) om conflicten rond jaarwisselingen te vermijden.

## 8. Jaar- en weekinferentie

De bron noemt alleen `Week 35`, niet expliciet het jaar.

Implementeer jaarinferentie op basis van:

1. `modified` uit WordPress;
2. de datumrange die naast het weeknummer staat;
3. ISO-weeklogica.

Schrijf tests voor december/januari-overgangen.

Nooit blind `datetime.now().year` gebruiken.

## 9. Publicatie

Genereer bij iedere succesvolle run:

```text
docs/api/by-week/2026-W35.json
docs/api/by-week/2026-W36.json
docs/api/latest.json
docs/api/status.json
```

### Waarom week-specifieke bestanden

De maaltijdplanner kent vooraf de relevante ophaaldonderdag en dus de benodigde week.

Hij kan daarom rechtstreeks ophalen:

```text
https://<github-user>.github.io/<repo>/api/by-week/2026-W35.json
```

Dit is robuuster dan uitsluitend afhankelijk zijn van een steeds overschreven `latest.json`.

### `latest.json`

Bevat de volledige meest recent opgehaalde bron met alle momenteel gepubliceerde weken.

### `status.json`

Voorbeeld:

```json
{
  "status": "ok",
  "last_success": "2026-08-28T13:15:00+02:00",
  "source_modified": "2026-08-27T12:20:54",
  "published_weeks": [
    "2026-W35",
    "2026-W36"
  ]
}
```

## 10. GitHub Actions-workflow

Workflownaam:

```text
Update Kistje Vol Smaak
```

Triggers:

```yaml
on:
  workflow_dispatch:
  schedule:
    - cron: "17 6,9,12,15,18 * * *"
```

Cron draait in UTC.

Reden voor meerdere runs per dag:

- publicatiemomenten kunnen veranderen;
- GitHub scheduled Actions kunnen vertraagd starten;
- de bron bleek in de praktijk al op donderdag te kunnen wijzigen;
- meerdere goedkope GET-requests per dag zijn robuuster dan één exact tijdstip.

Optimalisatie later is toegestaan, maar betrouwbaarheid gaat voor minimale run-count.

Workflowstappen:

1. checkout;
2. Python installeren;
3. dependencies installeren;
4. unit tests draaien;
5. live fetch uitvoeren;
6. parser/validator draaien;
7. JSON-bestanden genereren;
8. controleren of bestanden werkelijk gewijzigd zijn;
9. alleen bij wijzigingen committen;
10. pushen naar dezelfde branch;
11. GitHub Pages serveert `docs/`.

Commitmessage:

```text
chore(kistje): update published box contents [skip ci]
```

Gebruik geen `push`-trigger voor deze workflow om loops te voorkomen.

## 11. Validatie vóór publicatie

Een nieuwe response mag alleen als geldig worden gepubliceerd als minimaal:

- `id == 19172`;
- `slug == "deze-week-in-je-kistje"`;
- `modified` aanwezig en parseerbaar is;
- `content.rendered` niet leeg is;
- minimaal één weeksectie is gevonden;
- iedere gevonden week minimaal één groente en één fruitproduct bevat;
- weeknummers uniek zijn;
- productnamen niet leeg zijn.

Aanvullende kwaliteitscontrole:

- verwacht normaliter 4 groenten + 4 fruitsoorten;
- afwijking hiervan geeft een warning;
- afwijking mag niet automatisch tot corrupte output leiden als de bron aantoonbaar een andere geldige samenstelling publiceert.

## 12. Last-known-good-beleid

Bij een mislukte fetch of parserfout:

**niet**:

- `latest.json` leegmaken;
- bestaande weekbestanden verwijderen;
- oude data overschrijven met incomplete data.

Wel:

1. workflow laten falen;
2. `status.json` in de repository niet automatisch vervangen door onbetrouwbare data;
3. fout in GitHub Actions-log tonen;
4. eventueel raw response als workflow artifact opslaan voor debugging;
5. volgende schedule-run opnieuw proberen.

De maaltijdplanner moet zelf controleren of de benodigde week bestaat. Een oude week mag nooit als een andere week worden geïnterpreteerd.

## 13. Planner-weekselectie

Implementeer in `src/planner_week.py`:

```python
def pickup_date_for_plan_start(plan_start):
    ...
```

Regels:

- als planstart donderdag is: dezelfde donderdag;
- als planstart maandag is: donderdag ervoor;
- algemeen: neem de meest recente donderdag op of vóór de planstart, tenzij toekomstige plannerregels expliciet anders bepalen.

Daarna:

```python
pickup_iso_year, pickup_iso_week, _ = pickup_date.isocalendar()
key = f"{pickup_iso_year}-W{pickup_iso_week:02d}"
```

De maaltijdplanner vraagt vervolgens exact dat weekbestand op.

### Verplichte testcases

```text
Planstart 2026-08-27 → pickup 2026-08-27 → 2026-W35
Planstart 2026-08-31 → pickup 2026-08-27 → 2026-W35
Planstart 2026-09-03 → pickup 2026-09-03 → 2026-W36
```

Voeg ook jaarwisselingstests toe.

## 14. E2E-test

Maak een echte live integratietest die niet uitsluitend een fixture gebruikt.

### Test

1. GitHub-hosted runner start.
2. Runner doet live REST-call naar Hoeve Biesland.
3. Controleert HTTP-response.
4. Controleert `modified`.
5. Parseert alle gepubliceerde weken.
6. Genereert weekbestanden.
7. Publiceert naar `docs/api/by-week/`.
8. Verifieert na deployment via HTTPS dat het gepubliceerde JSON-bestand bereikbaar is.
9. Parseert dat publieke JSON opnieuw.
10. Controleert dat source page-ID `19172` is.
11. Controleert dat de verwachte week aanwezig is.
12. Controleert dat `planner_additions` 6 eieren bevat.

De E2E-test is pas geslaagd als de data opnieuw via de **publieke eind-URL** is opgehaald.

## 15. Fixture-tests

Bewaar een echte, eerder succesvolle WordPress-response in:

```text
tests/fixtures/wordpress_response.json
```

Gebruik die uitsluitend voor deterministische unit tests.

Test minimaal:

- week 35 wordt gevonden;
- week 36 wordt gevonden;
- groente en fruit worden correct gescheiden;
- `*` wordt correct gedetecteerd;
- `*`-producten blijven opgenomen voor het 2-persoonskistje;
- oorsprong wordt niet als onderdeel van de productnaam opgeslagen;
- biologische/biodynamische aanduiding wordt herkend;
- parser crasht niet op HTML entities en Visual Composer-shortcodes;
- ontbrekende week geeft expliciete fout;
- lege content wordt afgewezen.

## 16. GitHub Pages

Configureer:

```text
Settings → Pages → Deploy from branch
Branch: main
Folder: /docs
```

Verwachte publieke endpoints:

```text
https://<user>.github.io/<repo>/api/status.json
https://<user>.github.io/<repo>/api/latest.json
https://<user>.github.io/<repo>/api/by-week/2026-W35.json
```

De repository mag publiek zijn; er worden geen persoonsgegevens of secrets opgeslagen.

## 17. Consumercontract voor de maaltijdplanner

De maaltijdplanner mag de originele Hoeve Biesland-webpagina niet meer als primaire kistjesbron gebruiken.

Volgorde:

```text
1. bepaal planstart;
2. bepaal relevante ophaaldonderdag;
3. bereken ISO-week van die donderdag;
4. haal het week-specifieke GitHub Pages JSON op;
5. controleer schema_version;
6. controleer week-key;
7. controleer source.modified en fetched_at;
8. gebruik vegetables + fruit;
9. voeg planner_additions toe;
10. ga daarna pas door met recepten/boodschappenplanning.
```

Als het benodigde weekbestand niet bestaat:

1. probeer `latest.json`;
2. accepteer alleen data waarvan de week-key exact overeenkomt;
3. verzin nooit kistinhoud;
4. gebruik nooit een oudere week als vervanging voor een ontbrekende actuele week.

## 18. Monitoring

Gebruik GitHub Actions zelf als eerste monitoringlaag.

Minimum:

- failed workflows zichtbaar in Actions;
- expliciete exception messages;
- status/freshness in `status.json`.

Optioneel later:

- GitHub Issue automatisch aanmaken na bijvoorbeeld 3 opeenvolgende failures;
- README badge met laatste workflowstatus;
- externe uptimecheck op `status.json`.

Geen monitoringcomponent is noodzakelijk voor versie 1 zolang de workflowfailure duidelijk zichtbaar is.

## 19. Security

- Geen secrets nodig voor de bron.
- Alleen standaard `GITHUB_TOKEN` gebruiken voor repository-write.
- Workflow permissions beperken tot:

```yaml
permissions:
  contents: write
```

- Geen shell-evaluatie van broncontent.
- Broninhoud uitsluitend als data behandelen.
- Geen scripts/HTML uit `content.rendered` uitvoeren.
- JSON-output veilig serialiseren via Python `json`.

## 20. Implementatiefases voor de developer AI agent

### Fase 1 — Repository en parser

- repositorystructuur maken;
- dependencies instellen;
- fixture opslaan;
- parser schrijven;
- normalizer schrijven;
- unit tests groen krijgen.

### Fase 2 — Live fetch

- WordPress GET implementeren;
- timeout/retries toevoegen;
- `modified` en source metadata opslaan;
- failure behavior implementeren.

### Fase 3 — Weeklogica

- relevante donderdag berekenen;
- ISO-week-key genereren;
- unit tests voor donderdag/maandag/jaarwisseling.

### Fase 4 — Publicatie

- weekbestanden genereren;
- `latest.json`;
- `status.json`;
- GitHub Pages configureren.

### Fase 5 — GitHub Action

- schedule + manual dispatch;
- tests;
- fetch;
- generate;
- diff;
- commit/push.

### Fase 6 — E2E-validatie

- workflow handmatig uitvoeren;
- Actions-run moet groen zijn;
- publieke Pages-URL uitlezen;
- weekdata vanaf publieke URL opnieuw valideren;
- controleren dat geen handmatige data nodig was.

### Fase 7 — Integratie maaltijdplanner

- maaltijdplanner laten rekenen met relevante ophaaldonderdag;
- rechtstreeks week-specifiek JSON-bestand ophalen;
- bestaande webpagina alleen nog als diagnostische fallback gebruiken.

## 21. Definition of Done

Het project is pas klaar als aan **alle** voorwaarden is voldaan:

- [ ] Een GitHub-hosted Action haalt zelfstandig de live WordPress REST API op.
- [ ] Geen plugin is vereist.
- [ ] Geen eigen computer/server hoeft aan te staan.
- [ ] Geen handmatige copy/paste is vereist.
- [ ] Geen Hoeve Biesland-login of API-key is vereist.
- [ ] Parser verwerkt de actuele WordPress-response.
- [ ] `modified` wordt opgeslagen.
- [ ] Gepubliceerde weeknummers worden automatisch herkend.
- [ ] Groente en fruit worden correct gescheiden.
- [ ] `*`-producten worden correct verwerkt voor het 2-persoonskistje.
- [ ] De vaste 6 eieren zijn expliciet als plannerregel opgenomen.
- [ ] Data wordt per ISO-jaar/week gepubliceerd.
- [ ] GitHub Pages biedt publieke JSON-endpoints.
- [ ] De planner selecteert de week op basis van de relevante ophaaldonderdag.
- [ ] Bij fouten blijft last-known-good behouden.
- [ ] Oude data wordt nooit stilzwijgend als actuele week gebruikt.
- [ ] Unit tests zijn groen.
- [ ] Een echte live E2E-test is groen.
- [ ] De E2E-test haalt als laatste stap het resultaat opnieuw via de publieke GitHub Pages-URL op.
- [ ] De volledige keten werkt zonder menselijke tussenkomst.

## 22. Einddoel

De operationele keten moet uiteindelijk volledig automatisch zijn:

```text
Hoeve Biesland wijzigt WordPress-pagina
              ↓
GitHub Action detecteert nieuwe content
              ↓
REST-response wordt gevalideerd
              ↓
weken worden geparsed
              ↓
week-JSON wordt gepubliceerd
              ↓
maaltijdplanner bepaalt ophaaldonderdag
              ↓
maaltijdplanner haalt correcte week-JSON op
              ↓
kistje + 6 eieren beschikbaar voor planning
```

Geen tussenkomst van de gebruiker is onderdeel van de normale werking.
