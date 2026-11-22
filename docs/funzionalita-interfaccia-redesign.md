# Salotto · Inventario delle funzionalità per il redesign

Riferimento: codice locale esaminato il 9 novembre 2026. Questo documento descrive le funzionalità implementate nell'interfaccia, non certifica il funzionamento di ogni comando sulla TV fisica. Include le modifiche locali presenti al momento dell'analisi.

## Scopo e contesto

L'applicazione è un telecomando web in italiano per una TV Samsung, utilizzabile da smartphone e PC sulla stessa rete locale. Il server Python gira su Raspberry Pi o computer. Il modello di riferimento indicato nel progetto è Samsung UE49KS7000.

Il redesign deve conservare le funzioni elencate, le differenze fra le viste e i messaggi sul grado di conferma delle operazioni. Le sezioni seguenti costituiscono l'inventario dello stato attuale; non sono nuove richieste di funzionalità.

## File frontend e schermate

I collegamenti seguenti sono relativi a questo documento, quindi funzionano anche all'interno del repository.

| Schermata / componente | File | Responsabilità |
| --- | --- | --- |
| **Telecomando principale** · `/` | **[web/index.html](../web/index.html)** | Struttura HTML, CSS inline e quasi tutta la logica JavaScript del telecomando |
| Ricerca YouTube | [web/keyboard.js](../web/keyboard.js) | Validazione del campo e invio della query completa |
| Condivisione dal PC · `/share` | [web/share.html](../web/share.html) | Interfaccia di avvio, impostazioni e anteprima della condivisione |
| Logica di condivisione | [web/share.js](../web/share.js) | Cattura, apertura ricevitore, trasmissione, statistiche e arresto |
| Ricevitore TV · `/screen` | [web/screen.html](../web/screen.html) | Visualizzazione dello schermo condiviso e comandi del ricevitore |
| Logica del ricevitore | [web/screen.js](../web/screen.js) | Connessione, riproduzione video/immagini e modalità compatibile |
| Audio condiviso | [web/audio-share.js](../web/audio-share.js), [web/audio-receiver.js](../web/audio-receiver.js) | Stati audio, trasmissione e riproduzione sul ricevitore |
| Elaborazione multimediale | [web/share-worker.js](../web/share-worker.js), [web/mp4.js](../web/mp4.js), [web/audio-capture.js](../web/audio-capture.js), [web/audio-worker.js](../web/audio-worker.js) | Elaborazione fuori dall'interfaccia principale, codifica e confezionamento dei flussi |
| Icone delle app | [web/assets/](../web/assets/) | Immagini locali di YouTube, Netflix e Prime Video |

Il frontend usa HTML, CSS e JavaScript senza framework. Il telecomando viene servito da [server.py](../server.py); le due pagine di condivisione vengono composte anche tramite [sharing_pages.py](../sharing_pages.py). I file HTML sono template: per una prova funzionale usare il server, che sostituisce le chiavi e gli URL dinamici.

## 1. Telecomando: elementi sempre disponibili

- Identità dell'ambiente: titolo **Salotto.**, nome pagina **Salotto · Telecomando** e riferimento al modello Samsung nel footer.
- Indicazione di utilizzo nella sola rete locale.
- Stato sintetico della connessione: inizialmente “Locale”, poi “Socket connesso”, “Server pronto” oppure “Server non raggiungibile”.
- Due pulsanti di accensione separati: **On** invia il risveglio via rete; **Off** richiede lo standby con una pressione breve Power.
- Barra di apertura sorgenti/app: **TV antenna**, **YouTube**, **Netflix**, **Prime Video**, **PC · HDMI 1**.
- Evidenziazione della sorgente associata alla vista corrente e attributo accessibile `aria-current`.
- Pulsante **Apri schermo sulla TV**: richiede l'apertura della pagina ricevente nel browser TV.
- Link **Condividi dal PC**: apre la pagina di cattura; usa l'indirizzo HTTPS quando configurato.
- Titolo contestuale dei comandi e selettore della vista.
- Messaggio sul contesto: vista manuale, salvata, dedotta dall'ultimo comando, incerta o predefinita dopo la richiesta di accensione; può includere l'ultimo canale richiesto e l'uso di memoria temporanea.
- Area di esito dei comandi, annunciata con `role="status"` e `aria-live="polite"`.
- Sezioni espandibili **Altri comandi**, **Impostazioni · Raspberry e server** e **Terminale comandi**.

### Apertura sorgenti e selezione della vista

Sono due azioni distinte:

- I pulsanti sorgente/app inviano una richiesta alla TV e adattano il layout in base alla richiesta.
- Il selettore **adatta soltanto l'interfaccia**: non cambia la sorgente sulla TV. Le opzioni sono Tutti i comandi, TV · canali, YouTube, Netflix, Prime Video, PC · HDMI 1 e Schermo PC · rete.

La vista è condivisa attraverso il server, viene salvata quando possibile e viene recuperata ai successivi accessi. Non rappresenta una lettura dello schermo TV: un altro telecomando può modificare la sorgente senza aggiornare la dashboard. Una richiesta di accensione imposta la vista antenna come default; Home, Sorgenti ed Esci (e Indietro fuori dalle viste app) riportano il contesto a incerto/tutti i comandi, mentre nelle viste YouTube, Netflix e Prime Video il pulsante Indietro invia il comando alla TV mantenendo la vista attuale.

## 2. Matrice di visibilità dei comandi

La matrice riproduce la logica di `renderContext()` in `web/index.html`.

| Vista | Ricerca YouTube | Navigazione | Canali e preferiti | Riproduzione | Volume separato |
| --- | --- | --- | --- | --- | --- |
| Tutti i comandi | Sì | Sì | Sì | Sì | No: presente nel pannello canali |
| TV · canali | No | No | Sì | No | No: presente nel pannello canali |
| YouTube | Sì | Sì | No | Sì | Sì |
| Netflix | No; messaggio sulla ricerca dalla TV | Sì | No | Sì | Sì |
| Prime Video | No; messaggio sulla ricerca dalla TV | Sì | No | Sì | Sì |
| PC · HDMI 1 | No | No | No | No | Sì |
| Schermo PC · rete | No | Sì | No | No | Sì |

La barra sorgenti, l'accensione, i collegamenti di condivisione e le sezioni espandibili restano disponibili in tutte le viste. Per navigare nei menu mentre si guarda l'antenna, l'interfaccia invita a scegliere **Tutti i comandi**.

## 3. Navigazione sulla TV

- Pad direzionale con **Su**, **Giù**, **Sinistra**, **Destra**.
- Pulsante centrale **OK**.
- Collegamenti rapidi **Indietro**, **Home**, **Esci**.
- Etichette accessibili e tooltip per i pulsanti a icona.

## 4. Canali antenna e guida programmi

### Preferiti

Sei schede selezionabili, definite nel codice e senza editor nell'interfaccia:

| Numero | Canale |
| --- | --- |
| 5 | Canale 5 |
| 6 | Italia 1 |
| 8 | TV8 |
| 9 | NOVE |
| 20 | 20 Mediaset |
| 52 | DMAX |

Ogni scheda mostra numero, nome, titolo del programma in onda, intervallo orario e programma successivo con orario. Selezionare la scheda richiede il cambio al relativo canale. Tooltip ed etichetta accessibile includono le informazioni del programma.

### Guida TV

- Caricamento automatico del palinsesto dei preferiti.
- Indicazione della fonte, data/ora del download e data/ora del file sorgente quando disponibile.
- Indicazione di cache da aggiornare, download fallito con uso della cache, guida non disponibile o irraggiungibile.
- Indicazione dell'aggiornamento automatico e del prossimo tentativo, quando restituiti dal server.
- Orari dei programmi nel fuso **Europe/Rome**.
- Segnaposto per programma, prossimo programma e orari mancanti.
- Canali selezionabili anche quando la guida non è disponibile.
- La pagina rilegge la guida ogni minuto mentre è visibile. Il server aggiorna la fonte indipendentemente dal browser: normalmente ogni 24 ore, con nuovo tentativo dopo 6 ore in caso di download fallito.

La guida è un palinsesto esterno previsto, non il contenuto rilevato dalla TV.

### Selezione canali

- Pulsanti **Canale precedente** e **Canale successivo**.
- Campo per il numero del canale e pulsante **Vai**.
- Inserimento diretto da 1 a 4 cifre, con tastiera numerica suggerita sui dispositivi mobili.
- Nove pulsanti rapidi **1–9**: ciascuno seleziona direttamente quel canale. Non sono una tastiera che accumula cifre; i numeri composti si inseriscono nel campo.
- Validazione del numero prima dell'invio; gli inserimenti non validi vengono segnalati anche nel log del browser.

## 5. Volume e riproduzione

- **Volume −2**, **Muto**, **Volume +2**.
- Nella vista antenna, volume e canale precedente/successivo sono riuniti in un'unica riga.
- Nelle altre viste previste dalla matrice è disponibile una riga volume dedicata.
- Riproduzione: **Indietro veloce**, **Pausa**, **Riprendi / Play**, **Avanti veloce**.
- **Stop** disponibile nella sezione Altri comandi.

L'interfaccia non mostra un livello numerico del volume, uno stato mute confermato, una barra di avanzamento o uno stato play/pausa rilevato dalla TV: sono pulsanti di invio comandi.

## 6. Ricerca YouTube dalla tastiera del dispositivo

- Campo **Cerca un video sulla TV** nelle viste YouTube e Tutti i comandi.
- Uso della normale tastiera del telefono o del PC.
- Invio con **Cerca** o invio del form dalla tastiera.
- Query obbligatoria, massimo **128 caratteri**; il campo rifiuta una ricerca composta solo da spazi. Il server verifica anche i caratteri non ammessi.
- Invio dell'intera query in una sola richiesta; digitare non invia singoli caratteri alla TV.
- Apertura dei risultati nell'app YouTube sulla TV; selezione del video tramite il pad direzionale.
- Esito dedicato dopo l'invio e messaggio di attesa se un altro comando è già in corso.
- Contenuto della query escluso dai log applicativi e dal contesto UI salvato; nessun salvataggio esplicito della query nello storage del browser.
- In Netflix e Prime Video il campo è nascosto e compare l'indicazione di usare la ricerca sulla TV.

Non esistono nell'interfaccia una ricerca universale, risultati video sul telefono, sincronizzazione del testo con il campo TV o calibrazione di una tastiera a griglia.

## 7. Altri comandi

Pannello espandibile con nove pulsanti: **Menu**, **Sorgenti**, **Guida**, **Info**, **Lista canali**, **Stop**, **Canale −**, **Canale +**, **Home**.

Questa sezione resta disponibile anche quando alcuni comandi sono già presenti nel layout principale.

## 8. Impostazioni · Raspberry e server

Pannello informativo espandibile con:

- Stato del dispositivo server, etichettato **Raspberry** quando riconosciuto.
- Stato applicativo: API funzionante ed eventuale comando in corso.
- Stato socket verso la TV; un socket chiuso può essere aperto al comando successivo.
- Indirizzo della dashboard corrente.
- Modello o nome del dispositivo.
- Tempo di accensione del dispositivo e tempo di attività del processo server.
- Memoria usata dal server e RAM disponibile/totale dell'host.
- Stato dell'avvio automatico e del servizio di sistema.
- Stato del refresh giornaliero, con riferimento alle **11:00 Italia** e alla prossima esecuzione se disponibile.
- Data e ora dell'ultima risposta.
- Pulsanti **Verifica ora** e **Ricarica pagina**.
- Messaggi per stati non disponibili, non verificabili o non raggiungibili.

Controllo automatico ogni **10 secondi** mentre la pagina è visibile. Un cambio di sessione del server o della chiave della pagina può provocare un ricaricamento automatico per ripristinare i comandi.

Il pannello è di consultazione: non modifica IP, Wi-Fi, servizi o timer e non riavvia il Raspberry. Ricorda che l'indirizzo stabile si configura con una prenotazione DHCP sul router. Senza risposta il browser non distingue dispositivo spento, rete assente e servizio arrestato. Una risposta API non conferma l'effetto dei comandi sulla TV.

## 9. Feedback, attese ed errori

- Un solo comando alla volta: i controlli interessati assumono un aspetto di attesa e gli ulteriori invii vengono ignorati.
- Messaggi distinti per invio alla TV e cambio della sola vista.
- Esito con tempi complessivi e messaggi specifici per apertura app, accensione, spegnimento, apertura ricevitore e ricerca YouTube.
- Indicazione esplicita quando l'operazione non è confermata.
- Nessun reinvio automatico dei comandi dopo errori o timeout: la TV potrebbe aver già ricevuto la richiesta.
- Aggiornamento dello stato sintetico e del contesto ogni **3 secondi** mentre la pagina è visibile; aggiornamento sospeso durante un comando.
- Al ritorno sulla pagina vengono aggiornati stato, diagnostica, log e guida.

Preservare la distinzione fra **richiesta inviata**, **socket connesso** e **risultato visibile sulla TV**. Il frontend non dispone di una conferma visiva generale della sorgente, della riproduzione o del canale effettivo.

## 10. Terminale comandi

- Pannello espandibile con timeline **Browser → Server → TV**.
- Ultime **200 righe** conservate nel browser.
- Orario locale con millisecondi, mittente, identificativo breve della richiesta quando presente e messaggio.
- Evidenziazione di errori e avvisi.
- Tempi HTTP complessivi e tempi server quando restituiti.
- Lettura incrementale degli eventi server ogni **secondo** mentre la pagina è visibile.
- Avviso per nuova sessione del server e per log irraggiungibile.
- Scorrimento automatico se l'utente è già vicino al fondo; conservazione della posizione quando sta leggendo righe precedenti.
- Pulsante **Copia log**; se gli appunti non sono disponibili, indicazione di selezionare manualmente le righe.

## 11. Condivisione schermo dal PC · `/share`

Schermata separata con titolo **Condividi lo schermo** e link di ritorno al telecomando.

### Avvio e accesso al ricevitore

- Istruzioni per scegliere **schermo, finestra o scheda** nel dialogo nativo del browser.
- Pulsante **Apri sulla TV** e stato dell'apertura richiesta.
- Sezione espandibile **Indirizzo del ricevitore**, con link apribile in una nuova scheda.
- Verifica della disponibilità del relay e delle capacità del browser.
- Invito ad aprire la versione HTTPS quando necessario e disponibile; messaggi dedicati per HTTPS non configurato o browser non compatibile.
- Pulsante **Condividi**, abilitato quando la pagina soddisfa i requisiti.
- Richiesta dei permessi nel dialogo del browser a ogni cattura.
- Apertura automatica del ricevitore TV all'avvio se non risulta già collegato.
- Stati di selezione sorgente, collegamento, attesa TV, trasmissione, errore e chiusura.

### Impostazioni prima dell'avvio

| Controllo | Opzioni | Default |
| --- | --- | --- |
| Larghezza massima | 640, 960, 1280 px | 1280 px |
| Immagini al secondo richieste | 5, 10, 15, 30, 60, 90, 120 | 30 |
| Modalità | Automatica; Compatibile · immagini adattive | Automatica |
| Condividi anche l'audio | Attivo / disattivo | Attivo |
| Qualità audio | Musica e video · stereo; Voce · mono, meno banda | Musica e video |

Questi controlli vengono disabilitati durante la condivisione e riabilitati alla fine. La cattura ha altezza massima di 720 px e mantiene le proporzioni. La frequenza scelta è una richiesta: il percorso video arriva al massimo a 60 immagini/s, quello compatibile a 30. Le opzioni 90 e 120 non garantiscono quelle frequenze effettive.

### Monitoraggio e anteprima

- Sezione espandibile **Anteprima sul PC**, con video locale senza riproduzione audio locale nell'elemento di anteprima.
- Indicazione del percorso usato: H.264 oppure immagini adattive.
- Statistiche aggiornate durante l'invio: immagini/s confermate, banda video in Mb/s, tempo di conferma della TV in ms e buffer video quando pertinente.
- In alcuni browser, apertura dell'anteprima e invito a mantenere attiva la scheda.
- Passaggio automatico alla modalità immagini compatibile quando il ricevitore/percorso video lo richiede.
- Messaggi per ricevitore congestionato, mancata risposta della TV e problemi di cattura, codifica o connessione.

I tempi di conferma e il buffer sono dati diagnostici; non misurano il ritardo fisico fra schermo PC e pannello TV.

### Audio

- Abilitazione facoltativa dell'audio offerto dal browser per la sorgente scelta.
- Istruzioni per attivare l'audio della scheda o del sistema nel dialogo di cattura.
- Nessuna acquisizione del microfono.
- Stato separato per audio pronto, assente dalla selezione, in attesa TV, avvio, codec utilizzato, stereo/mono e buffer.
- Indicazione di premere **Attiva audio** sulla TV se la riproduzione automatica è bloccata.
- Gestione di browser/TV non compatibili, sorgente audio terminata o connessione audio interrotta.
- Le immagini continuano se l'audio non è disponibile o fallisce.

La disponibilità della cattura audio dipende da sistema operativo, browser e sorgente. Il trasporto sceglie AAC o un percorso ADPCM compatibile; la sincronizzazione audio/video esatta non è garantita.

### Arresto

- Pulsante **Interrompi**.
- Gestione dello stop dal controllo nativo di cattura del browser e della normale uscita dalla pagina.
- Arresto di cattura, audio, anteprima e connessioni.
- Richiesta di chiusura del ricevitore/browser sulla TV con relativo feedback; un errore di chiusura viene mostrato come non confermato.

La condivisione è sperimentale, destinata al browser di un PC; la cattura dello schermo da telefono non è supportata. È prevista una sola sorgente di cattura e un solo ricevitore alla volta.

## 12. Ricevitore sulla TV · `/screen`

- Visualizzazione dello schermo condiviso su sfondo nero, mantenendo le proporzioni.
- Riproduzione video oppure immagini/canvas secondo la compatibilità.
- Pannello di stato per connessione al Raspberry, attesa avvio dal PC, stop, chiusura, errori e browser non compatibile.
- Pannello nascosto durante la normale visualizzazione dei contenuti.
- Pulsante **Ricollega** per riprovare il collegamento.
- Pulsante **Schermo intero**, quando l'API del browser lo consente.
- Pulsante **Attiva audio** mostrato se serve un gesto dell'utente per abilitare il suono.
- Gestione della fine condivisione e richiesta di chiusura della finestra; coordinamento della chiusura fra pagine riceventi gestite della stessa origine.

Non sono previsti controlli di mouse o tastiera del PC dalla TV. La pulizia delle schede persistenti del browser TV non è garantita.

## 13. Presentazione, dispositivi e accessibilità esistenti

- Lingua italiana e tema scuro.
- Telecomando a colonna compatta, larghezza massima 360 px, centrato anche su desktop.
- Adattamento agli schermi più stretti e rispetto delle safe area del dispositivo.
- Pulsanti e campi adatti all'uso touch; molti controlli principali hanno altezza minima di 44 px, mentre alcuni secondari sono più piccoli.
- Stati hover, pressione, focus visibile, selezione e attesa.
- Icone SVG per i comandi e immagini locali per le app.
- Etichette ARIA per pulsanti a icona, campi e gruppi; alcune etichette di supporto sono visivamente nascoste.
- Feedback annunciato nelle aree di stato, sezioni espandibili native e terminale raggiungibile con il focus.
- Rispetto di `prefers-reduced-motion` per l'effetto di pressione dei pulsanti.

Questi sono accorgimenti presenti nel codice, non un'attestazione di conformità accessibile completa.

## 14. Integrazioni da mantenere nel redesign

| Endpoint | Uso nell'interfaccia |
| --- | --- |
| `GET /api/status` | Connessione socket e contesto condiviso della vista |
| `POST /api/command` | Invio comandi TV e cambio della vista |
| `GET /api/epg` | Programmi dei canali preferiti e stato della guida |
| `GET /api/health` | Diagnostica host, server e servizi |
| `GET /api/logs?after=…&epoch=…` | Timeline incrementale dei comandi |
| `GET /api/share` | Configurazione e stato della condivisione |

Payload attualmente usati dall'interfaccia per `/api/command`: `key`, `channel`, `app`, `power`, `volume`, `source`, `screen`, `context`, `youtube_search`. La condivisione usa inoltre WebSocket separati per i flussi video e audio sul relay esistente.

Preservare le chiavi di controllo generate dal server, gli header `X-Remote-Key` e gli ID di correlazione delle richieste dove usati. I segnaposto `__CSRF__`, `__SHARE_URL__` e `__CONFIG__` devono continuare a ricevere i valori dinamici corretti. Il controllo è locale, senza login personale o codice di abbinamento nell'interfaccia.

Il server carica i file frontend in memoria all'avvio: dopo le modifiche occorre riavviarlo e ricaricare le pagine per verificare il risultato.

## 15. Checklist funzionale per l'agente di redesign

- [ ] Conservare tutte e tre le schermate: telecomando, condivisione PC e ricevitore TV.
- [ ] Coprire tutte e sette le viste e la matrice di visibilità dei comandi.
- [ ] Distinguere cambio della vista e comando reale di apertura sorgente/app.
- [ ] Conservare i sei preferiti, i programmi attuale/successivo e l'accesso ai canali con guida indisponibile.
- [ ] Conservare inserimento canale composto, pulsanti diretti 1–9, volume a passi di due e comandi avanzati.
- [ ] Mantenere ricerca da tastiera solo per YouTube e la spiegazione nelle viste Netflix/Prime Video.
- [ ] Coprire stati di caricamento, comando in corso, successo della richiesta, errore, dati mancanti e server irraggiungibile.
- [ ] Non trasformare uno stato presunto o una richiesta inviata in una conferma della TV.
- [ ] Conservare diagnostica, copia log e aggiornamenti mentre la pagina è visibile.
- [ ] Conservare scelta sorgente di cattura, impostazioni video/audio, anteprima e arresto della condivisione.
- [ ] Coprire HTTPS necessario, browser non supportato, ricevitore assente e audio non disponibile.
- [ ] Conservare i comandi del ricevitore: ricollegamento, schermo intero e attivazione audio.
- [ ] Mantenere percorsi, contratti API e protezioni esistenti durante una modifica solo grafica.
- [ ] Verificare layout stretto da smartphone, uso da PC, focus da tastiera ed etichette accessibili.

Per provare il telecomando con dispositivi e palinsesti simulati, il progetto offre `python -m tools.demo`, che espone normalmente `http://127.0.0.1:8766`. La demo non convalida il funzionamento fisico della TV né il flusso reale di condivisione.

## Fonti locali dell'inventario

Analisi di [web/index.html](../web/index.html), [web/keyboard.js](../web/keyboard.js), [web/share.html](../web/share.html), [web/share.js](../web/share.js), [web/screen.html](../web/screen.html), [web/screen.js](../web/screen.js), dei moduli audio/video collegati, di [server.py](../server.py), [view_state.py](../view_state.py), [text_input.py](../text_input.py), [tools/demo.py](../tools/demo.py) e del [README](../README.md). Le immagini dimostrative esistenti sono in [docs/images/](images/); la fonte per le funzionalità correnti resta il codice.
