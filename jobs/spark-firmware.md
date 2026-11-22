# Firmwareinventering för Quantum Spark

Jobben riktar sig till lokalt hanterade Gaia Embedded-enheter och kontaktar enhetens Primary IP på TCP 4434. Den manuella körningen kan även schemaläggas för en vald enhet.

1. Synka Git-repot i Nautobot med Jobs aktiverat. Aktivera **Läs firmware från Quantum Spark** och **Inventera ny eller ändrad Quantum Spark** under Jobs.
2. Skapa plattformen **Check Point Gaia Embedded** och tilldela den till brandväggen. Sätt enhetens Primary IP till dess managementadress.
3. Aktivera REST på gatewayen med `set rest-api mode on`. Använd ett konto med läsåtkomst som får köra `show software-version`. Lägg användare och lösenord i två Nautobot Secrets. Koppla dessa till en Secrets Group med access type **REST** och secret types **Username** respektive **Password**. Tilldela gruppen till enheten.
4. Workern behöver nå gatewayen direkt på TCP 4434. Med standardinställningen ska certifikatet gälla för enhetens management-IP (IP SAN). Om workern behöver en egen CA, montera CA-filen där och sätt miljövariabeln `SPARK_CA_BUNDLE` till sökvägen inuti containern.
5. Kontrollera att status **Active** gäller för **DCIM → Software Version**. För att även spara build skapar du ett valfritt custom field av typen **Text**, med nyckeln `spark_firmware_build` på **DCIM → Device**.
6. Kör **Läs firmware från Quantum Spark** med **Provläge** markerat. Kontrollera release och build i jobbloggen. Kör igen utan provläge för att spara.
7. Under **Jobs → Job Hooks** väljer du **DCIM → Device**, händelserna **Create** och **Update** och mottagaren **Inventera ny eller ändrad Quantum Spark**. Ge användare eller integrationskonton som ska trigga hooken Run på mottagarjobbet. Ett uppdaterat Primary IP, plattform eller Secrets Group kan då starta insamlingen när alla uppgifter finns.

Vid nätverksfel, API-fel eller okänt versionsformat misslyckas jobbet före skrivningen. Efter att felet är löst kan det manuella jobbet köras igen.

[Check Points REST-dokumentation](https://sc1.checkpoint.com/documents/SMB_R81.10.X/AdminGuides_Locally_Managed/EN/Content/Topics/RESTful-API.htm) · [Versionskommandot](https://sc1.checkpoint.com/documents/SMB_R81.10.X/CLI/EN/Content/Topics/show-software-version.htm)

## Labbgateway med ogiltigt certifikat

Skapa ett valfritt custom field av typen **Boolean** för **DCIM → Device** med nyckeln `spark_skip_tls_verify` och standardvärdet **False**. Sätt fältet till **True** på den aktuella labbgatewayen och kör inventeringsjobbet. Både manuella körningar och Job Hook läser inställningen från enheten vid varje inventering. Med True används HTTPS utan kontroll av gatewayens identitet. Sätt tillbaka fältet till False när certifikatet är på plats.
