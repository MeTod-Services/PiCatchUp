# Raspberry Pi hardware

**Status: IMPLEMENTED — REQUIRES PHYSICAL RPI VERIFICATION.** The adapters and
state transitions are covered by hardware-free tests; this repository has not
physically exercised your exact Pi, microphone, OLED, LED, button, or PiSugar.

## Target

- Raspberry Pi Zero 2 W, Raspberry Pi OS Lite 64-bit, Python 3.11+
- PiSugar 3 battery board
- USB Audio Class microphone on the Pi Zero **USB OTG/DATA** port
- SSD1306-compatible 128×64 monochrome OLED on I²C bus 1 (default `0x3C`)
- normally-open recording button
- low-current red LED with a **330 ohm series resistor**

`GPIO` numbers below are Broadcom (BCM) numbers. A physical pin number is the
position on the 40-pin header. They are not interchangeable in configuration.
PiCatchUp consistently configures BCM numbers.

## Wiring table

| Function | BCM GPIO | Physical pin | Connection |
|---|---:|---:|---|
| OLED power | — | 1 | 3.3 V only |
| OLED SDA | GPIO2 / SDA1 | 3 | OLED SDA |
| OLED SCL | GPIO3 / SCL1 | 5 | OLED SCL |
| OLED ground | GND | 6 | OLED GND |
| Button | GPIO17 | 11 | one button terminal |
| Button ground | GND | 6 (or any GND) | other button terminal |
| Red LED drive | GPIO27 | 13 | through 330 Ω to LED anode |
| Red LED ground | GND | 14 | LED cathode |

### Button (normally open, internal pull-up)

```text
GPIO17 / physical 11 ---- normally-open button ---- GND / physical 6
```

No external pull-up is required. A press connects GPIO17 to ground. Never wire
this button to 5 V.

### OLED

```text
Pi pin 1  (3.3 V) -------- OLED VCC
Pi pin 3  (GPIO2/SDA1) --- OLED SDA
Pi pin 5  (GPIO3/SCL1) --- OLED SCL
Pi pin 6  (GND) ---------- OLED GND
```

The address is configured with `OLED_I2C_ADDRESS=0x3C`. PiCatchUp opens that
address directly and does not repeatedly scan the bus. PiSugar also uses I²C;
do not change its address or wiring to match the OLED. A one-time `i2cdetect`
is only a diagnostic.

### Red recording LED

```text
GPIO27 / physical 13 ---- 330 Ω ---- LED anode (+)
                                      LED cathode (-) ---- GND / physical 14
```

The resistor can be on either side but must be in series.

> **Never connect the LED directly to GPIO without the resistor.**
>
> **Never connect GPIO pins directly to 5 V.**

LED behavior: OFF when ready; 250 ms ON / 250 ms OFF while recording; three
short flashes after a valid finalized recording; deterministic error flashes
on a local recording failure; OFF during clean shutdown.

## PiSugar 3

Install the PiSugar according to its manufacturer instructions. Battery
telemetry is optional: an unavailable battery reading is shown as `--` and
must not block recording. The OLED adapter does not scan I²C continuously.
Confirm both devices' documented addresses before changing any address.

## Enable interfaces and permissions

The installer enables I²C when `raspi-config` is available. Manually:

```bash
sudo raspi-config nonint do_i2c 0
sudo usermod -aG gpio,i2c,audio picatchup
sudo reboot
```

After reboot:

```bash
pinout
ls -l /dev/gpiomem /dev/i2c-1 /dev/snd/
i2cdetect -y 1          # diagnostic only; expect OLED at 3c
```

## USB microphone diagnostics

Connect the microphone to the **DATA/OTG** port, not the power-only port.
Raspberry Pi OS Lite uses ALSA directly; PulseAudio/PipeWire is not required.

```bash
lsusb
arecord -l
arecord -D hw:1,0 -f S16_LE -r 16000 -c 1 -d 5 /tmp/mic.wav
ffprobe -v error -show_entries format=duration /tmp/mic.wav
aplay /tmp/mic.wav      # optional, if an output device is connected
```

Use `AUDIO_INPUT_FORMAT=alsa`. Use `AUDIO_INPUT_DEVICE=auto` only with exactly
one capture device; it resolves through ALSA's `plughw` conversion. Otherwise
set `plughw:CARD,DEVICE` using the numbers reported by `arecord -l`.

## Application checks

```bash
sudo -u picatchup /opt/picatchup/venv/bin/picatchup doctor
sudo -u picatchup /opt/picatchup/venv/bin/picatchup status
journalctl -u picatchup-device -f
```

For laptop development set `GPIO_ENABLED=false` and `DISPLAY_ENABLED=false`.
Imports and tests never require Raspberry Pi libraries.
