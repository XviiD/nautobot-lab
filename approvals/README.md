# Destination Approvals

Nautobot-app för aktivt godkännande av labbets trafikbeställningar. Mina godkännanden visar giltiga LAB-FLOW-regler för användarens destinationsteam. Läsbehörighet till regeln och medlemskap i en godkännargrupp krävs även vid direktlänk och inskick. Ett beslut krävs per unikt destinationsteam. Medlemskap i godkännargruppen och läsåtkomst till regeln kontrolleras på servern för varje beslut.

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

Öppna appens startsida under /plugins/destination-approvals/ (lägg till eventuellt URL-prefix för din installation). Välj en regel och klicka på Skicka för godkännande.

| Regel | Godkännande |
| --- | --- |
| LAB-FLOW-001 | Linux |
| LAB-FLOW-002 | Linux en gång för alla tio gruppmedlemmar |
| LAB-FLOW-003 | Linux och DBA i valfri ordning |
| LAB-FLOW-004 | DBA |

Testa 003 med separata Windows-, Linux- och DBA-konton. Windows ser ingen regel i denna kö när Windows endast äger källan. Linux skickar in och godkänner. Status är då pending. Efter DBA:s godkännande blir status approved. Vid avslag blir den denied och en ny beställning kan skickas. Ändra därefter en destination eller tjänst och ladda om vyn. En ändrad beställning visas som superseded och kräver nya beslut. Grupp- och ägarändringar upptäcks när vyn öppnas eller ett beslut skickas. Godkännandet sparas i appens egen tabell. Labbet gör inga externa anrop och installerar ingen brandväggspolicy.
