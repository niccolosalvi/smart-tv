# Ricevitore Tizen — da installare e verificare

Questo ricevitore usa AVPlay con video H.264/audio AAC dal Raspberry, evitando
sia il browser/MSE sia il buffer non configurabile del lettore DLNA. I sorgenti
e l'integrazione sono predisposti; installazione, deep link e prestazioni sulla
UE49KS7000 richiedono ancora la prova sul dispositivo.

AVPlay documenta un minimo di 4 secondi per il buffer play/resume. L'app imposta
quel minimo e un timeout iniziale di 3 secondi, API distinta che può completare
il buffering prima. Questo NON dimostra 1–3 secondi di ritardo end-to-end:
devono essere misurati dopo l'installazione. Fonte:
https://developer.samsung.com/smarttv/develop/api-references/samsung-product-api-references/avplay-api.html

## Configurazione una tantum

1. TV: aprire Apps / impostazioni Apps e digitare 12345. Abilitare Developer Mode,
   indicare l'IPv4 LAN del PC che eseguirà Tizen Studio e riavviare la TV.
2. PC: installare Tizen Studio, TV Extension e Samsung Certificate Extension.
   Collegare il TV con Device Manager. Questa fase richiede interazione sulla TV.
3. Certificate Manager: creare un profilo Samsung TV usando il proprio account
   Samsung, includendo il DUID della TV. Credenziali e chiavi restano private:
   non inserirle nel repository. Non riutilizzare certificati di un altro TV.
4. Importare `tv-receiver` come progetto TV Web, firmarlo con quel profilo e
   installarlo sulla TV. ID applicazione: `SmartTVRx1.Receiver`.
5. Dopo la verifica dell'installazione, aggiungere `SMART_TV_TIZEN_RECEIVER=1`
   all'ambiente privato del servizio Raspberry e riavviare `smart-tv`.
   La modalità «Ricevitore Tizen» diventa selezionabile sul PC.
6. In HTTPS, scegliere quella modalità, 1920 px e 60 immagini/s. Il Raspberry
   avvia l'app con il link temporaneo della sessione. Verificare video, suono,
   frame rate, ritardo, stop, riavvio e nuova condivisione. `Return` esce dall'app.

La modalità DLNA rimane disponibile senza installare l'app; nelle prove aveva
circa 10–12 secondi di ritardo. Il flag Tizen resta disattivato finché l'app non
è installata. Nessuna configurazione privata viene modificata dal packaging.

`python -m tools.package_tizen_receiver` crea un archivio **non firmato** in
`reports/`. Non è un pacchetto già installabile sulla TV: occorre la firma del
profilo Samsung corretto. La verifica XML/JavaScript non sostituisce la
validazione Tizen SDK, la firma e il test hardware.

Guide ufficiali:

- https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/tv-device.html
- https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/creating-certificates.html
