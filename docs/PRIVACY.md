# Privacy di PiCatchUp

Questo documento descrive il comportamento tecnico del sistema. **Non è un
consenso e non è un parere legale.** Prima di registrare una classe reale, gli
operatori devono ottenere le autorizzazioni richieste dalla scuola, dagli
insegnanti, dagli studenti e, quando necessario, dai genitori o tutori.
Finché ciò non avviene, usare solo registrazioni sintetiche o delle persone che
stanno sviluppando il progetto.

## Che cosa viene registrato

Il microfono registra l'audio della lezione quando qualcuno preme il pulsante e
smette alla pressione successiva. Lo schermo e il LED rosso rendono visibile lo
stato di registrazione. PiCatchUp non identifica automaticamente chi sta
parlando e non deve essere usato per registrazioni nascoste.

## Che cosa viene inviato a Groq

Groq è un servizio esterno di elaborazione AI. In base alla funzione usata,
PiCatchUp gli invia:

- i file audio, eventualmente divisi in parti, per ottenere la trascrizione;
- il testo trascritto delle lezioni del giorno per creare il riassunto;
- la domanda dello studente e pochi brani pertinenti recuperati localmente per
  creare una risposta.

Le chiavi API non sono incluse nei contenuti e non vengono stampate nei log.
Prima dell'uso reale, l'operatore deve verificare condizioni, localizzazione dei
dati e accordi di trattamento applicabili al proprio account Groq.

## Che cosa riceve Telegram

Telegram riceve l'identificativo della chat necessario alla consegna e il testo
dei riassunti, delle risposte e dei messaggi di servizio. Le domande arrivano
prima al bot Telegram. I messaggi già consegnati possono restare nei sistemi di
Telegram e sul telefono dell'utente secondo le regole di Telegram.

Il bot risponde con contenuti della classe solo in chat private e solo a utenti
presenti nel database locale con `is_active = 1`. Nei gruppi non espone lezioni,
date, materie o riassunti.

## Dati conservati localmente

SQLite (`picatchup.db`) è la fonte persistente principale. Contiene:

- trascrizioni e parti temporizzate delle lezioni;
- data, materia e metadati tecnici della registrazione;
- riassunti;
- elenco degli utenti autorizzati;
- prova tecnica delle consegne accettate da Telegram.

I log descrivono eventi e errori tecnici, non il contenuto delle lezioni o delle
domande. Il piccolo file di stato del display è temporaneo e non è un secondo
archivio dell'applicazione.

## Conservazione

`AUDIO_RETENTION_DAYS` vale 7 giorni per impostazione predefinita. All'inizio di
ogni esecuzione di `picatchup daily`, l'audio più vecchio viene eliminato e il
relativo percorso viene cancellato dal database. Anche file falliti, parziali
o rimasti senza una riga SQLite dopo un crash sono soggetti alla stessa pulizia.
Questa avviene prima di Groq e Telegram: un loro errore non prolunga la
conservazione. Se il Pi resta
spento e il timer non parte, la pulizia avviene alla prima esecuzione successiva.

Trascrizioni e riassunti restano in SQLite finché l'operatore non elimina il
database o i dati interessati. I backup contengono gli stessi dati testuali;
quelli locali sono ruotati conservando per impostazione predefinita i 14 più
recenti. Se `BACKUP_REMOTE` è configurato, ogni snapshot verificato viene anche
copiato via rsync/SSH sul NAS indicato (Tailscale fornisce solo il collegamento
privato). PiCatchUp non elimina automaticamente le copie remote: protezione,
retention e cancellazione sul NAS restano responsabilità dell'operatore.

## Rimozione di un utente

`picatchup user remove ID` elimina localmente la voce di autorizzazione e la
cronologia locale delle consegne associata. Da quel momento l'ID non può più
leggere i contenuti tramite il bot. La rimozione:

- non elimina i contenuti condivisi della classe;
- non elimina automaticamente messaggi già presenti su Telegram o sul telefono;
- non controlla copie o screenshot fatti in precedenza.

Per richieste più ampie l'operatore deve seguire la procedura stabilita dalla
scuola e valutare anche backup e servizi esterni.

## Responsabilità dell'operatore

Prima dell'uso reale occorre almeno: documentare la finalità, ottenere e poter
dimostrare le autorizzazioni, limitare gli amministratori, proteggere `.env` e i
backup, controllare periodicamente l'allowlist, informare chiaramente sulla
registrazione e definire una procedura di revoca/cancellazione. Il fatto che il
software applichi queste misure tecniche non significa che il consenso sia già
stato ottenuto.
