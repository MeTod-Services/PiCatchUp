#!/bin/sh
amixer -c 0 sset Mic 12 >/dev/null 2>&1 || true
amixer -c 0 sset 'Auto Gain Control' off >/dev/null 2>&1 || true
