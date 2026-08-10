# Raspberry Pi physical acceptance checklist

**IMPLEMENTED — REQUIRES PHYSICAL RPI VERIFICATION.** Record date, Pi serial,
OS version, OLED model/address, microphone model, and tester with the results.

## Installation

- [ ] Raspberry Pi OS Lite 64-bit and Python 3.11+ are installed.
- [ ] Button, OLED and LED match `docs/HARDWARE.md`; LED has a 330 Ω resistor.
- [ ] USB microphone is connected to the OTG/DATA port.
- [ ] `sudo scripts/install-rpi.sh` finishes and `picatchup doctor` identifies
      only expected missing configuration before `.env` is completed.
- [ ] `/etc/picatchup/picatchup.env` is completed and mode 0640 or stricter.
- [ ] `sudo scripts/install-rpi.sh --enable-services` is safe on a second run.

## Boot

- [ ] Power the Pi from PiSugar and boot without SSH intervention.
- [ ] OLED shows `PiCatchUp / Avvio...`, then reaches `PRONTO`.
- [ ] PRONTO reports microphone and Wi-Fi state; unavailable battery shows `--`.
- [ ] Red LED is OFF at PRONTO.
- [ ] `systemctl --failed` has no PiCatchUp unit.

## Button and recording

- [ ] Press once: OLED immediately shows REC and elapsed time starts increasing.
- [ ] Red LED blinks about 250 ms on / 250 ms off for the entire recording.
- [ ] Record speech for at least 15 seconds.
- [ ] Rapid accidental presses inside debounce do not start extra recordings.
- [ ] Press again: OLED shows SAVING and ffmpeg exits cleanly.
- [ ] LED flashes three times and turns OFF.
- [ ] A new MP3 exists in `/var/lib/picatchup/inbox/`.
- [ ] `ffprobe -v error -show_entries format=duration <file>` succeeds.
- [ ] Play/copy the MP3 and confirm understandable speech.
- [ ] A third and fourth press successfully record a second file.

## Error and shutdown behavior

- [ ] Disconnect microphone, press: no fake recording starts; OLED shows a clear
      microphone error and LED shows the deterministic error pattern.
- [ ] Reconnect microphone and confirm the next attempt works.
- [ ] During recording run `sudo systemctl stop picatchup-device`; active audio
      is finalized when possible and LED ends OFF.
- [ ] Disconnect OLED: recording and Telegram still work; failure is in journal.
- [ ] PiSugar telemetry unavailable: device still reaches READY with battery `--`.

## Processing

Run:

```bash
sudo -u picatchup bash -lc \
  'set -a; source /etc/picatchup/picatchup.env; picatchup daily'
```

- [ ] OLED/device status shows TRANSCRIBING with real chunk counts when known.
- [ ] It then shows SUMMARIZING, SENDING and DONE (no fake percentages).
- [ ] Lesson, chunks/FTS and recap exist in SQLite.
- [ ] Re-running `daily` does not retranscribe, duplicate a lesson, or ordinarily
      resend the same recap.
- [ ] With internet disabled, local recording still works and processing fails
      clearly; after restoring internet, a later run succeeds.

## Telegram and Q&A

- [ ] Authorized private user receives the recap.
- [ ] `/start`, `/aiuto`, `/privacy`, `/riassunto` respond in Italian.
- [ ] A relevant question returns `📖 Dalla lezione`, subject/date/minute source,
      and a separate `💡 Spiegazione aggiuntiva` block.
- [ ] An absent topic explicitly says no lesson evidence was found.
- [ ] An unauthorized user receives no subject, date, recap, transcript or class info.
- [ ] Adding the bot to a group exposes no class content and the bot leaves.
- [ ] A long recap is split into Telegram-safe messages without broken UTF-8.

## Backup and privacy

- [ ] `picatchup backup` creates a timestamped DB in the backup directory.
- [ ] `sqlite3 BACKUP 'PRAGMA integrity_check'` prints `ok`.
- [ ] Seeded old audio is removed even when Groq/Telegram are unavailable.
- [ ] `recap DATE --force` invalidates old delivery rows and sends new text once.
- [ ] Removing a user blocks future bot access; verify documentation does not
      claim that Telegram messages already on their phone were deleted.

## Reboot and timers

```bash
sudo reboot
systemctl status picatchup-device picatchup-bot
systemctl list-timers 'picatchup-*'
journalctl -u picatchup-device -u picatchup-bot --since today
```

- [ ] Device and bot return automatically.
- [ ] Daily timer is Monday-Friday at 15:00 and persistent.
- [ ] Backup timer is nightly and persistent.
- [ ] No secret, transcript or raw question content appears in journal.
