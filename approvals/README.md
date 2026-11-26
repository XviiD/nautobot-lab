# Destination Approvals

Nautobot-app för aktivt godkännande av labbets trafikbeställningar. Mina godkännanden visar giltiga LAB-FLOW-regler för användarens destinationsteam. Läsbehörighet till regeln och medlemskap i en godkännargrupp krävs även vid direktlänk och inskick. Ett beslut krävs per destinationsteam och per separat riskansvar, även om samma grupp har båda rollerna. Medlemskap i godkännargruppen och läsåtkomst till regeln kontrolleras på servern för varje beslut.

## Installation i Docker

Kopiera katalogen approvals från denna branch till ditt befintliga Docker-byggsammanhang. Lägg till följande i Dockerfilen i det byggsteg där Python-paketen för Nautobot installeras.

```dockerfile
COPY approvals /tmp/destination-approvals
RUN python -m pip install --no-deps /tmp/destination-approvals
```

Lägg till `destination_approvals` i den befintliga PLUGINS-listan i nautobot_config.py. Bygg och distribuera samma image till webben och workers. Kör `nautobot-server post_upgrade` med den nya imagen och konfigurationen. Git-synken för det befintliga importjobbet och appinstallationen är separata steg.

## Användargrupper

Förvalt matchas taggen linux-team mot användargruppen linux-team, och motsvarande gäller dba-team, windows-team och network-team. Använd befintliga teamgrupper när deras medlemmar har mandat att godkänna, eller ange särskilda grupper i nautobot_config.py.

```python
PLUGINS_CONFIG["destination_approvals"] = {
    "team_groups": {
        "linux-team": "Linux Approvers",
        "dba-team": "DBA Approvers",
        "windows-team": "Windows Approvers",
        "network-team": "Network Approvers",
    }
}
```

Ge användarna samma begränsade View på Policy Rule som i labbet. Godkännaren behöver vara medlem i rätt grupp även om kontot är superuser. En beställare som också är behörig godkännare kan godkänna sitt eget teams del.

## Test

Riskklassa först testreglerna som low enligt avsnittet Riskbaserade godkännanden nedan för att testa den ursprungliga tabellen.

Öppna appens startsida under /plugins/destination-approvals/ (lägg till eventuellt URL-prefix för din installation). Välj en regel och klicka på Skicka för godkännande.

| Regel | Godkännande |
| --- | --- |
| LAB-FLOW-001 | Linux |
| LAB-FLOW-002 | Linux en gång för alla tio gruppmedlemmar |
| LAB-FLOW-003 | Linux och DBA i valfri ordning |
| LAB-FLOW-004 | DBA |

Testa 003 med separata Windows-, Linux- och DBA-konton. Windows ser ingen regel i denna kö när Windows endast äger källan. Linux skickar in och godkänner. Status är då pending. Efter DBA:s godkännande blir status approved. Vid avslag blir den denied och en ny beställning kan skickas. Ändra därefter en destination eller tjänst och ladda om vyn. En ändrad beställning visas som superseded och kräver nya beslut för ändrade granskningsunderlag. Grupp- och ägarändringar upptäcks när vyn öppnas eller ett beslut skickas. Godkännandet sparas i appens egen tabell. Labbet gör inga externa anrop och installerar ingen brandväggspolicy.

## Simulerade ACI- och NSX-grupper

Grupper med tom medlemslista och exakt en ägartagg representerar externa grupper i labbet. Vanliga grupper använder medlemmarnas ägartaggar. Gruppen kan väljas som Source Address Group eller Destination Address Group. Destinationens ägare godkänner.

För att teamet ska kunna läsa regeln, lägg till följande två alternativ i den befintliga OR-listan för teamets begränsade View på Policy Rule. Exemplet gäller DBA. Behåll de tidigare alternativen för adresser och grupper med medlemmar.

```json
[
  {"source_address_groups__tags__name": "dba-team", "source_address_groups__address_objects__isnull": true},
  {"destination_address_groups__tags__name": "dba-team", "destination_address_groups__address_objects__isnull": true}
]
```


## Riskbaserade godkännanden (0.2.0)

Skapa Custom Field på **Policy Rule** i Extensibility > Custom Fields:
key `risk_level`, typ Selection, val `low`, `medium`, `high`, inget default.
Riskklassificera befintliga regler innan fältet görs Required; saknade/ogiltiga värden blockeras även om fältet är frivilligt. Importjobbet sätter inte riskklass eller skriver över en befintlig klassning. Klassificera nyimporterade regler efter import (om fältet görs Required måste importjobbet anpassas för nya regler). Tidigare snapshots saknar riskklass och måste godkännas på nytt.

Lägg till grupperna och komplettera befintlig PLUGINS_CONFIG (behåll team_groups):

```python
PLUGINS_CONFIG["destination_approvals"]["risk_groups"] = {
    "medium": "Network Approvers",
    "high": "Security Approvers",
}
```

Low kräver enbart destinationsgodkännanden; medium dessutom Network, high Security (inte Network om det inte är destinationsägare). Från 0.3.0 krävs ett separat riskbeslut även om samma Django-grupp är destinationsgodkännare. Saknad godkännargrupp blockerar regeln. Godkännanden sker parallellt i befintliga Mina godkännanden; ingen ny databasmodell krävs.

Ge Network Approvers respektive Security Approvers separata Object Permissions med Object Type Policy Rule, Action view och respektive constraint:

```json
{"request_id__startswith":"LAB-FLOW-","_custom_field_data__risk_level":"medium"}
```

```json
{"request_id__startswith":"LAB-FLOW-","_custom_field_data__risk_level":"high"}
```

Behåll befintliga teambehörigheter. Approvers ska normalt bara ha View. Riskklass ska sättas av en betrodd API-identitet med Change på Policy Rule. Nautobots vanliga objektbehörigheter ger inte fältspecifik skrivkontroll. Ge inte obetrodda beställare Add/Change på Policy Rule om de kan ange eller sänka risk själva. Denna version innehåller inte en separat fältskyddad riskklassningsendpoint eller automatisk riskanalys; använd betrodd portal/klassificeringstjänst.

PATCHa PolicyRule-resursens URL (ta exakt URL från GET eller /api/docs/):

```bash
curl --fail-with-body -X PATCH "$RULE_URL" \
  -H "Authorization: Token $CLASSIFIER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"custom_fields":{"risk_level":"medium"}}'
```

Skicka regeln för godkännande i befintliga vyn. Ändrad riskklass ändrar snapshoten; oförändrade destinationsgodkännanden kan återanvändas enligt delta-flödet nedan. Avslag blockerar tills en ny beställning skickas. Som tidigare är jämförelsen snapshotbaserad: ändring och återställning utan registrerad mellanversion upptäcks inte som en egen revision. Självgodkännande är fortfarande tillåtet för behöriga gruppmedlemmar enligt tidigare labbpolicy.

### Deployment gate för AAP

Nytt read-only API (Nautobot token, View på Policy Rule):

```bash
curl --fail-with-body \
  -H "Authorization: Token $AAP_TOKEN" \
  "$NAUTOBOT_URL/api/plugins/destination-approvals/rules/$RULE_ID/approval-status/"
```

AAP ska stoppa om HTTP-anropet misslyckas, deployment_ready inte är exakt true eller approved_snapshot saknas. Bygg deployment från approved_snapshot, inte ett senare oberoende GET av PolicyRule. Kontrollera gate igen omedelbart före deployment; undvik samtidiga ändringar under installation. Snapshoten gäller samma labbfält som tidigare (inte externa gruppers verkliga medlemskap, policyplacering eller regelordning). Appen gör inga faktiska brandväggsinstallationer; AAP måste aktivt använda denna gate.

### Verifikation

1. LAB-FLOW-001 low: endast Linux.
2. LAB-FLOW-002 medium: Linux + Network; Network ser regeln trots att det inte äger destinationen. Bara Linux godkänner: deployment_ready=false; Network godkänner: true.
3. LAB-FLOW-003 high: Linux + DBA + Security. Network utan destinationsägarskap får inte se eller godkänna. Testa direktlänk och manipulerad teamnyckel i POST.
4. Ändra 002 från medium till high: gate false, tidigare snapshot superseded; skicka på nytt, Linux återanvänds om redan godkänt och Security behöver besluta.
5. Saknad grupp eller risk: gate false. Token utan View får 404 (eller nekad autentisering).
6. Avslag blockerar. Endast ändrade regler behöver nya beslut efter initial backfill.

Kör enhetstester i installerad Nautobot-miljö med `nautobot-server test destination_approvals.tests` (kontrollera testdatabas/inställningar). Verifiera GUI/API och Object Permissions i labbet innan deployment.

## Inkrementella godkännanden (0.3.0)

Varje godkännande jämför sitt eget underlag med närmast föregående inskickade version av samma regel:

- Destinationsteam: egen destinationsmängd (inklusive ägare, adress och gruppreferenser), källa, tjänster, action, loggning, status, beställnings-ID och egen godkännargrupp. Riskklass och andra teams destinationsmängd påverkar inte detta beslut.
- Riskansvar: hela trafikflödet, riskklass, samma gemensamma fält och egen godkännargrupp. Ändrad trafik eller riskklass kräver nytt riskbeslut. Risk- och destinationsbeslut är separata även om samma grupp har båda rollerna.
- Regelns visningsnamn kräver inget nytt beslut på egen hand. Nytt beställnings-ID kräver däremot nya beslut.

Ändra regeln via ordinarie API som tidigare och öppna Mina godkännanden. Ändrade regler visas som Ändrad. Giltiga tidigare beslut visas med ursprungligt namn/tid och gransknings-ID. Vid inskick skapas en ny Review med aktuell helhetssnapshot och kopierade positiva beslut från närmast föregående Review. Avslag kopieras inte. Tidigare Review och dess beslutshistorik skrivs inte över (dess state kan markeras superseded). Om alla beslut fortfarande är giltiga blir den nya versionen direkt approved utan manuell omgranskning. Annars väntar bara kvarstående beslut. Ändringar i ett annat teams destinationer kräver inte att redan godkända team gör om sina beslut. UI visar vilka granskningsfält som ändrats; hela regelns nuvarande trafik visas också som tidigare.

AAP:s befintliga gate returnerar false för ändrad helhetssnapshot tills den aktuella versionen skickats in och alla krav är uppfyllda. API:t exponerar dessutom pending_approvals och retained_approvals. En tom pending_approvals-lista ersätter inte deployment_ready (t.ex. kan den aktuella snapshoten ännu vara oskickad). AAP måste fortfarande stoppa om deployment_ready inte är exakt true.

Tidigare 0.2.0-godkännanden återanvänds om deras befintliga snapshot har tillräckliga fält och underlaget är oförändrat. Om en riskgrupp tidigare deduplicerats mot destinationsteamet krävs ett separat riskbeslut en första gång. Ingen databasändring eller ny permission krävs. Ingen automatisk risknedklassningskontroll införs; betrodd API-skrivare krävs fortsatt. Snapshotjämförelse upptäcker inte mellanliggande ändringar som aldrig skickats in och sedan återställts. Ingen historisk sökning efter äldre matchande beslut görs. Externa gruppers verkliga medlemskap ligger fortfarande utanför snapshoten.

Testa särskilt LAB-FLOW-003 medium -> high efter att alla godkänt: Linux och DBA kvarstår, Security väntar, Network tas bort. Lägg därefter till en Linux-destination: DBA kvarstår, Linux och Security behöver besluta. Ändra tjänst: alla aktuella team behöver besluta. Kontrollera stale POST, direktlänkar och AAP-gate. Enhetstester i destination_approvals/tests/test_delta.py.


## Approval REST API (0.4.0)

GUI och API använder samma workflow.py med transaktionslås på PolicyRule, View-kontroll, aktuell gruppmedlemskapskontroll och aktuell snapshot. Inga nya modeller eller migrationer. Alla operationer gäller fortfarande LAB-FLOW-regler.

Bas-URL: `$NAUTOBOT_URL/api/plugins/destination-approvals/rules/$RULE_ID`.

1. GET approval-status/ med användarens token. Befintlig AAP-gate är oförändrad (review_id är fortfarande endast satt när approved). active_review_id är senaste review oavsett state. my_pending_approvals visar beslutsnycklar för inloggad användare, endast när pending. can_submit visar om användarens grupp får skicka in aktuell version (token måste dessutom ha Write enabled för POST). Aktuella godkännare får även current_snapshot, snapshot_hash, decisions, changed_fields och my_approval_scopes. Läs snapshoten i portalen innan beslut.
2. POST submit/ med expected_review_id (null första gången, annars senaste active_review_id) och snapshot_hash från GET. Behörighet: samma som GUI, minst ett aktuellt godkännandeansvar. Kvarvarande beslut ärvs via delta-flödet.
3. POST decision/ med review_id (active_review_id från GET), team (exakt nyckel från my_pending_approvals), action (approve eller deny) och valfri comment (max 2000 tecken). Användaridentitet och tid sätts på servern. Godkännande per scope, inte per hela regel.

```bash
BASE="$NAUTOBOT_URL/api/plugins/destination-approvals/rules/$RULE_ID"
curl --fail-with-body -sS "$BASE/approval-status/" -H "Authorization: Token $USER_TOKEN"
# Exempel: ersätt ID och hash med värden från GET som användaren granskat.
curl --fail-with-body -sS -X POST "$BASE/submit/" \
  -H "Authorization: Token $USER_TOKEN" -H "Content-Type: application/json" \
  -d '{"expected_review_id":null,"snapshot_hash":"<64 hextecken från GET>"}'
curl --fail-with-body -sS -X POST "$BASE/decision/" \
  -H "Authorization: Token $USER_TOKEN" -H "Content-Type: application/json" \
  -d '{"review_id":42,"team":"risk:high","action":"approve","comment":"Granskat enligt säkerhetspolicy"}'
```

POST returnerar aktuell approval-status och processed_review_id. 400 vid ogiltig payload/riskklass, 403 vid saknad grupp eller skrivaktiverad token, 404 vid saknad View/regeln finns ej, 409 vid inaktuell snapshot/review, upprepat beslut eller redan inskickad version. Oautentiserade anrop nekas (401 eller 403 beroende på autentiseringsklass). Vid 409: läs om, visa aktuellt underlag och begär nytt mänskligt beslut om så krävs; återspela inte gamla beslut automatiskt. AAP ska fortsatt använda endast deployment_ready och approved_snapshot.

Token tillhör användaren som faktiskt godkänner. Write enabled krävs för POST, men användaren behöver inte Change på PolicyRule eller direkt skrivbehörighet på Review. Lämna AAP-token read-only. Skicka aldrig en användares token till klientloggar eller lagra den i webbläsarens localStorage; använd TLS och skyddad portal-backend. Ingen impersonation via payload eller generisk service-token som kan godkänna för alla team. Om portalen använder SSO utan användarspecifika Nautobot-identiteter behövs separat betrodd identitetsintegration; denna version implementerar inte delegerad OAuth/token exchange. Befintlig möjlighet till självgodkännande, snapshotbegränsningar och riskklassningsskydd är oförändrade.

Testa GUI/API-korsning: skicka via API, godkänn Linux i GUI, godkänn risk via API. Testa read-only-token, fel grupp, fel review_id, ändrad regel före beslut, dubbla POST, avslag, oförändrade delgodkännanden och AAP-gate. Enhetstester i tests/test_workflow.py, kör i labbet innan produktionssättning.
