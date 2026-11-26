# Destination Approvals

Nautobot-app för aktivt godkännande av labbets trafikbeställningar. Mina godkännanden visar giltiga LAB-FLOW-regler för användarens destinationsteam. Läsbehörighet till regeln och medlemskap i en godkännargrupp krävs även vid direktlänk och inskick. Ett beslut krävs per destinationsteam och ytterligare riskgrupp om den inte redan ingår som destinationsteam. Medlemskap i godkännargruppen och läsåtkomst till regeln kontrolleras på servern för varje beslut.

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

Testa 003 med separata Windows-, Linux- och DBA-konton. Windows ser ingen regel i denna kö när Windows endast äger källan. Linux skickar in och godkänner. Status är då pending. Efter DBA:s godkännande blir status approved. Vid avslag blir den denied och en ny beställning kan skickas. Ändra därefter en destination eller tjänst och ladda om vyn. En ändrad beställning visas som superseded och kräver nya beslut. Grupp- och ägarändringar upptäcks när vyn öppnas eller ett beslut skickas. Godkännandet sparas i appens egen tabell. Labbet gör inga externa anrop och installerar ingen brandväggspolicy.

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

Low kräver enbart destinationsgodkännanden; medium dessutom Network, high Security (inte Network om det inte är destinationsägare). Om samma Django-grupp redan är destinationsgodkännare krävs inte ett extra riskbeslut. Saknad godkännargrupp blockerar regeln. Godkännanden sker parallellt i befintliga Mina godkännanden; ingen ny databasmodell krävs.

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

Skicka regeln för godkännande i befintliga vyn. Ändrad riskklass ändrar snapshoten och tidigare beslut gäller inte den nya versionen. Avslag blockerar tills en ny beställning skickas. Som tidigare är jämförelsen snapshotbaserad: ändring och återställning utan registrerad mellanversion upptäcks inte som en egen revision. Självgodkännande är fortfarande tillåtet för behöriga gruppmedlemmar enligt tidigare labbpolicy.

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
4. Ändra 002 från medium till high: gate false, tidigare snapshot superseded; skicka på nytt, kräv Linux + Security.
5. Saknad grupp eller risk: gate false. Token utan View får 404 (eller nekad autentisering).
6. Avslag blockerar. Endast ändrade regler behöver nya beslut efter initial backfill.

Kör enhetstester i installerad Nautobot-miljö med `nautobot-server test destination_approvals.tests` (kontrollera testdatabas/inställningar). Verifiera GUI/API och Object Permissions i labbet innan deployment.
