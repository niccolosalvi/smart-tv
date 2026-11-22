# smart-tv

Questo progetto contiene il telecomando e la condivisione dello schermo per la TV.

## Accesso SSH e deploy sul Raspberry

- Prima di collegarti o effettuare un deploy, leggi `.local/ssh-access.json`.
  Contiene le credenziali fornite dall'utente e gli indirizzi disponibili.
- Quel file e l'intera directory `.local/` sono esclusi da Git. Non copiare la
  password in documenti versionati, log, messaggi, argomenti della riga di comando
  o script di deploy. Leggila dal file al momento dell'utilizzo.
- Per questo Raspberry usa l'utente indicato nel file privato. L'alias SSH
  Windows `raspberrypi` attualmente specifica un altro utente: quando lo usi,
  sovrascrivi esplicitamente l'utente con quello del file privato.
- L'indirizzo IP salvato e il nome `.local` non sono stati verificati in questa
  sessione. Un errore di risoluzione del nome o un blocco di rete non dimostra
  che la password sia errata.
- Conserva la verifica delle chiavi host SSH. Prima di pubblicare modifiche,
  verifica la directory del servizio sul Raspberry, crea un backup dei file
  interessati e preserva configurazione, certificati e token esistenti. Dopo il
  deploy verifica il servizio e gli endpoint HTTP; ripristina il backup se il
  servizio non riparte correttamente.
