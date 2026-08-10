```text
     ._________________________________.
   .'                                  `-.
  /     .--------------------.            `----.__     .---.
 |     /      PiCatchUp       \                   `====|###|
 |     \                      /             ___.--'====|###|
  \     `--------.  .--------'       ___.--'           `---'
   `-_____________\/_______________.--'
```

# PiCatchUp

PiCatchUp records a consenting school lesson, transcribes it in Italian, creates
one daily recap, sends it to allowlisted students on Telegram, and answers later
questions from SQLite FTS5 evidence with subject/date/minute citations.

It is a deliberately small Raspberry Pi application: Python, SQLite, ffmpeg,
systemd, one OLED, one button and one LED. Groq and Telegram networking is
confined to `src/picatchup/providers/`.

> Real classroom recording requires authorization. Read `docs/PRIVACY.md`
> before recording anyone. Software controls are not consent.

## Hardware

Target: Raspberry Pi Zero 2 W, Raspberry Pi OS Lite 64-bit, PiSugar 3, one USB
Audio Class microphone, SSD1306-compatible 128×64 I²C OLED, normally-open
button, and a low-current red LED with a 330 Ω resistor.

| Function | BCM | Physical pin |
|---|---:|---:|
| OLED SDA | GPIO2 | 3 |
| OLED SCL | GPIO3 | 5 |
| Button | GPIO17 | 11 |
| Red LED | GPIO27 | 13 |
| OLED 3.3 V | — | 1 |
| OLED GND | GND | 6 |
| LED GND | GND | 14 |

Never connect GPIO to 5 V. Never connect a bare LED directly to GPIO. Full
wiring, diagrams and verification commands are in `docs/HARDWARE.md`.

**Hardware status: IMPLEMENTED — REQUIRES PHYSICAL RPI VERIFICATION.** Use
`docs/RPI_CHECKLIST.md`; no claim is made that this checkout's exact hardware
has been physically tested.

## Raspberry Pi installation

From a fresh Raspberry Pi OS Lite checkout:

```bash
cd PiCatchUp
sudo scripts/install-rpi.sh
sudoedit /etc/picatchup/picatchup.env
# Fill GROQ_API_KEY, TEXT_MODEL and TELEGRAM_BOT_TOKEN.
sudo scripts/install-rpi.sh --enable-services
```

The idempotent installer installs ffmpeg/ALSA/I²C tools, creates the unprivileged
`picatchup` user and venv under `/opt/picatchup`, keeps data in
`/var/lib/picatchup`, installs systemd units, initializes SQLite, and runs the
offline doctor. It does not write credentials. Running it again updates the
package and units without replacing the existing environment file.

To enable services later:

```bash
sudo systemctl enable --now picatchup-device picatchup-bot \
  picatchup-daily.timer picatchup-backup.timer
```

The service user belongs to `audio`, `gpio` and `i2c`; services do not run as
root. If interface/group changes were just made, reboot once.

## Development installation

Python 3.11+ and ffmpeg/ffprobe are required.

```bash
sudo apt install ffmpeg alsa-utils
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
picatchup init
```

On a Pi use `pip install -e '.[dev,rpi]'`. For a laptop set:

```dotenv
GPIO_ENABLED=false
DISPLAY_ENABLED=false
AUDIO_INPUT_FORMAT=pulse
AUDIO_INPUT_DEVICE=default
```

## Configuration

`.env.example` documents every setting. Important Raspberry Pi values are:

```dotenv
DATA_DIR=/var/lib/picatchup
AUDIO_INPUT_FORMAT=alsa
AUDIO_INPUT_DEVICE=auto       # only when exactly one capture device exists
AUDIO_RETENTION_DAYS=7
GPIO_ENABLED=true
GPIO_BUTTON_PIN=17
GPIO_LED_PIN=27
DISPLAY_ENABLED=true
OLED_I2C_ADDRESS=0x3C
OLED_WIDTH=128
OLED_HEIGHT=64
BACKUP_RETENTION_COUNT=14
# Optional: rsync over SSH to a NAS reachable through Tailscale
BACKUP_REMOTE=backup@picatchup-nas.tailnet.ts.net:/volume1/backups/picatchup/
```

With multiple microphones, use explicit `plughw:CARD,DEVICE` based on `arecord -l`.
PiCatchUp refuses ambiguous `alsa:auto`. The OLED address is opened directly;
the application does not repeatedly scan I²C and does not require PiSugar
battery telemetry.

## Initialize and inspect

```bash
picatchup init
picatchup doctor       # comprehensive offline configuration/hardware checks
picatchup status       # concise database/device/inbox status
```

Installed-host admin commands run from the data directory so `.env` is found:

```bash
sudo -u picatchup bash -c 'cd /var/lib/picatchup && picatchup status'
```

Neither command prints tokens or API keys. `doctor` may fail checks on a laptop
unless disabled hardware is configured explicitly.

## Recording and the local UI

The autonomous service runs:

```bash
picatchup device
```

Press once to start, again to stop, then repeat for another lesson. GPIO
callbacks do no slow work. SIGINT/SIGTERM request a clean ffmpeg stop before
hardware closes. A recording is accepted only after ffmpeg succeeds and
ffprobe proves that it has playable duration.

The OLED shows BOOTING, READY, blinking REC with elapsed time, SAVING,
TRANSCRIBING (real chunk progress), SUMMARIZING, SENDING, DONE and ERROR. A
missing OLED degrades to logging and does not prevent recording. Battery `--`
is expected when PiSugar telemetry is unavailable.

Red LED behavior:

- READY: off;
- recording: 250 ms on / 250 ms off (about 2 Hz);
- valid finalized recording: three short flashes, then off;
- local recording error: deterministic rapid then slow flashes;
- shutdown: best-effort off and GPIO close.

Manual alternatives remain available:

```bash
picatchup record --subject Matematica
picatchup record --minutes 55
picatchup buttons                 # legacy button-only listener
```

Phone recordings may be copied into `data/inbox/`. For a slow/network copy,
write `lesson.m4a.part` and atomically rename it to the final audio suffix when
complete; `.part` files are never ingested. A name such as
`2026-08-07 Matematica.m4a` preserves that date. Date precedence is explicit:
CLI `--date` > filename date > daily/fallback date.

## Telegram bot and allowlist

No database user means no access. Before the bot service consumes polling
updates, ask a user to message the bot and find their numeric id:

```bash
picatchup user ids
picatchup user add 424242 'Ada'
picatchup user list
picatchup bot
```

With the service already running, an unauthorized attempt logs only the numeric
user id (`journalctl -u picatchup-bot`), never their message text. Add that id
locally and ask them to retry.

Supported private-chat commands are `/start`, `/aiuto` (`/help`), `/privacy`,
and `/riassunto`. Ordinary text is a grounded question. Non-private groups get
one neutral privacy response and the bot leaves; no class subjects, dates or
content are exposed. Telegram-safe splitting prefers paragraphs, then lines and
words, and never corrupts Unicode.

```bash
picatchup user remove 424242
```

Removal deletes local access and local delivery history only. It cannot delete
messages already stored by Telegram or on a student's phone.

## Grounded Q&A

The bot quotes safe search terms, retrieves the best five FTS5 chunks, and gives
only those chunks plus the question to the text model. Python—not the model—adds
citations such as `(Matematica, 15/09, minuto 23)`. Replies keep two explicit
trust levels:

```text
📖 Dalla lezione
[evidence-supported content]

🔎 Fonti: (Matematica, 15/09, minuto 23)

💡 Spiegazione aggiuntiva
[clearly separate general explanation]
```

No meaningful FTS result produces an Italian not-found answer without a model
call. Unstructured model output is discarded rather than mislabeled as teacher
evidence.

## Daily processing

```bash
picatchup daily
# or individual stages:
picatchup transcribe lesson.m4a --subject Fisica
picatchup recap 2026-09-15
picatchup send 2026-09-15
```

Order is retention cleanup, inbox ingestion/transcription, recap generation,
Telegram delivery and status reporting. Cleanup is before every provider call,
so a Groq/Telegram outage cannot extend audio retention. Failed audio remains
in the inbox. Saved transcripts/recaps survive later provider failures.

Without `--date`, a run processes newly ingested/unrecapped lesson dates and
pending deliveries, so a persistent timer that fires after reboot still catches
the filename's original school day. A known failed inbox file blocks recap
publication rather than sending a knowingly partial day.

Checksums, recap date uniqueness and delivery keys make reruns ordinarily
idempotent. A lesson arriving after an existing recap regenerates it and resets
old delivery state. `picatchup recap DATE --force` also replaces text and invalidates old
delivery rows, so the new text is not mistaken for the already delivered old
version. `send --force` intentionally resends.

Delivery is not distributed exactly-once: a crash after Telegram accepts a
message but before SQLite records it can theoretically duplicate that message
on retry. Likewise, if a split recap fails on a later part, retry can repeat the
earlier accepted parts. The MVP favors this explicit at-least-once edge case
over a persistent per-message outbox.

## systemd automation

Committed units are under `deploy/systemd/`:

- `picatchup-device.service` — button/OLED/LED, restart on failure;
- `picatchup-bot.service` — long polling, restart on failure;
- `picatchup-daily.timer` — persistent Monday-Friday at 15:00;
- `picatchup-backup.timer` — persistent nightly at 02:30.

```bash
systemctl status picatchup-device picatchup-bot
systemctl list-timers 'picatchup-*'
journalctl -u picatchup-device -u picatchup-daily -f
```

## Backups

```bash
picatchup backup
```

This uses SQLite's online backup API, verifies `PRAGMA integrity_check`, atomically
publishes a timestamped file under `DATA_DIR/backups`, and retains the newest
`BACKUP_RETENTION_COUNT` (14 by default). If `BACKUP_REMOTE` is set, the verified
snapshot is then copied with `rsync` over SSH. A Tailscale MagicDNS name or
`100.x.y.z` address works like any other SSH host; PiCatchUp never copies the
live WAL database directly.

For unattended NAS copies, first create a dedicated NAS account and destination
directory. Then configure SSH for the unprivileged service user (not root):

```bash
sudo install -d -o picatchup -g picatchup -m 0700 /var/lib/picatchup/.ssh
sudo -u picatchup ssh-keygen -t ed25519 -N '' \
  -f /var/lib/picatchup/.ssh/id_ed25519
# Add id_ed25519.pub to the NAS account's authorized_keys, then verify/trust it:
sudo -u picatchup ssh backup@picatchup-nas.tailnet.ts.net true
sudoedit /etc/picatchup/picatchup.env
# BACKUP_REMOTE=backup@picatchup-nas.tailnet.ts.net:/volume1/backups/picatchup/
sudo -u picatchup bash -c 'cd /var/lib/picatchup && picatchup backup'
```

The remote directory must already exist and the NAS user should have access only
to that backup directory. An rsync/SSH error makes the command fail so systemd
records it, while the verified local snapshot remains available. Remote files
are not deleted automatically; configure retention or snapshots on the NAS.
Use `journalctl -u picatchup-backup` to inspect failures.

Backups contain private transcript text and require the same protection as the
live database. To restore, stop the bot/device/timers, verify the chosen snapshot
with `PRAGMA integrity_check`, copy it to `DATA_DIR/picatchup.db` with owner
`picatchup:picatchup`, then start the services; keep the replaced database until
the restore is accepted.

## Privacy and failure modes

`docs/PRIVACY.md` explains in plain Italian what reaches Groq/Telegram, local
storage, retention, allowlisting, removal limits, and operator consent duties.
Important degraded modes are intentional: no OLED/LED/PiSugar does not stop core
recording; no internet does not stop local recording; Telegram failure leaves
the recap in SQLite; Groq failure leaves useful prior state and the inbox file.
A missing microphone never produces a fake successful recording.

## Tests and CI

Normal tests require no Raspberry Pi, API credit, token or external network:

```bash
pytest
ruff check .
mypy
python -m compileall -q src
```

GitHub Actions runs the same checks on supported generic Linux Python versions.
Provider tests use a fake HTTP server on localhost; GPIO, LED and OLED tests use
in-memory backends. Complete physical acceptance remains
`docs/RPI_CHECKLIST.md`.
