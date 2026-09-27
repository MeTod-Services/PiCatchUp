# Lezioni e conversazioni

Il servizio `picatchup device` parte sempre in modalità **LEZIONE**. Tre clic
brevi sul pulsante PiSugar cambiano modalità in **CONVERSAZIONE** e viceversa,
solo quando il dispositivo è pronto, ha finito o mostra un errore.

| Gesto | Risultato |
| --- | --- |
| Un clic breve | Avvia/ferma la registrazione nella modalità visualizzata |
| Tre clic brevi ravvicinati | Cambia modalità se il dispositivo è inattivo |
| Pressione lunga (almeno 1 secondo) | Non gestita dall'app; comportamento hardware invariato |
| Due clic | Conservano il significato di due pressioni singole |

La sequenza si chiude dopo 450 ms senza altri clic. Il clic singolo ha quindi
un ritardo di circa mezzo secondo. Durante una registrazione il triplo clic
non cambia modalità e non ferma la registrazione: usare un clic singolo.
Durante l'elaborazione i comandi sono ignorati. Il listener GPIO legacy
`picatchup buttons` resta invariato; lo switch riguarda il servizio PiSugar.

L'OLED mostra LEZIONE/CONVERSAZIONE nella schermata pronta, il tipo di
registrazione durante REC e il tempo trascorso. Al riavvio torna a LEZIONE.
Il campo `mode` nel file principale `device-status.json` indica la modalità attiva.
Le impostazioni eventualmente assegnate fuori dall'app nel firmware/servizio
PiSugar non sono ispezionate né modificate da questo codice: verificare sul
proprio dispositivo che il triplo clic non abbia già un'azione esterna.

## Secondo bot e whitelist

1. Creare un bot distinto inviando `/newbot` a
   [BotFather](https://core.telegram.org/bots/features#botfather), quindi scegliere
   nome e username. Il bot delle lezioni rimane quello esistente.
2. Aggiungere il token al file `.env` usato dall'app oppure, con l'installazione
   systemd, a `/etc/picatchup/picatchup.env`:

   ```dotenv
   CONVERSATION_TELEGRAM_BOT_TOKEN=token_del_secondo_bot
   ```

   Non usare il token delle lezioni. Un token uguale viene rifiutato; un token
   mancante non viene mai sostituito automaticamente con quello delle lezioni.
3. Inizializzare il database separato:

   ```sh
   picatchup --mode conversation init
   ```

4. Far inviare `/start` al nuovo bot dai contatti desiderati. Con il suo servizio
   di polling fermo, leggere gli ID e aggiungere esplicitamente i destinatari:

   ```sh
   picatchup --mode conversation user ids
   picatchup --mode conversation user add 123456789 "Nome contatto"
   picatchup --mode conversation user list
   ```

   Ripetere `user add` per ogni contatto. Nessun utente viene copiato dalla
   whitelist delle lezioni. Per revocare l'accesso:

   ```sh
   picatchup --mode conversation user remove 123456789
   ```

5. Avviare il secondo bot, indipendente dalla modalità fisica corrente:

   ```sh
   picatchup --mode conversation bot
   ```

Il bot accetta chat private degli utenti autorizzati. La conversazione tra
più persone è quella registrata dal microfono: non occorre un gruppo Telegram.
Senza token o senza destinatari attivi, il dispositivo mostra rispettivamente
`Configura bot conv.` o `Whitelist vuota` e non avvia la registrazione.

### Installazione systemd

L'installer installa anche `picatchup-conversation-bot.service` e inizializza
l'archivio conversazioni, ma non abilita automaticamente il secondo bot prima
che siano configurati token e whitelist. Dopo aver installato questa versione,
usare i comandi amministrativi come utente di servizio e nella sua directory:

```sh
cd /var/lib/picatchup
sudo -u picatchup /opt/picatchup/venv/bin/picatchup --mode conversation user ids
sudo -u picatchup /opt/picatchup/venv/bin/picatchup --mode conversation user add 123456789 "Nome"
sudo systemctl enable --now picatchup-conversation-bot.service
sudo systemctl restart picatchup-device.service
```

`user ids` va usato prima di avviare il polling, o dopo aver fermato temporaneamente
`picatchup-conversation-bot.service`. Dopo cambi al token riavviare sia il device
sia il secondo bot. Le modifiche alla whitelist sono lette dal database senza riavvio.

## Dati e riepiloghi

La modalità lezione mantiene percorsi, token e whitelist esistenti. La modalità
conversazione usa `DATA_DIR/conversation/` con database, inbox, audio, PDF,
whitelist e offset Telegram separati. Anche i comandi `recap`, `send`, `daily`,
`backup` e le domande del bot lavorano sull'archivio scelto con `--mode`.
L'opzione globale va prima del comando; senza opzione si lavora sulle lezioni.

Il riepilogo automatico riguarda solo la registrazione appena terminata:
temi discussi, posizioni, decisioni, azioni e questioni aperte. Non garantisce
il riconoscimento dei parlanti e non assegna nomi o responsabilità non espliciti
nel testo. Il bot conserva l'ultimo riepilogo per data per `/riassunto` e permette
di interrogare le trascrizioni conservate tramite la ricerca esistente.

Testo e PDF vengono inviati esclusivamente con il secondo token ai destinatari
della seconda whitelist. Le conversazioni non vengono inviate alla destinazione
NAS delle lezioni e non sono cancellate dopo l'invio: restano nell'archivio
separato. I timer esistenti continuano a gestire solo le lezioni; non viene
attivata una nuova politica automatica di backup o cancellazione. Il comando
manuale `--mode conversation daily` applica la retention audio configurata anche
all'archivio conversazioni; `--mode conversation backup` crea un backup locale.

## Verifica sul dispositivo

Dopo la configurazione: controllare che un triplo clic cambi la scritta OLED
senza avviare REC; registrare una breve conversazione con un clic per avviare e
uno per fermare; verificare testo e PDF sul solo bot conversazioni. Tornare a
LEZIONE con tre clic e verificare che la registrazione successiva segua il bot
originale. Il triplo clic durante REC deve lasciare invariata la modalità.
