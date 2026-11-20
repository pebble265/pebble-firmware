// Copy this file to include/secrets.h and fill it in. secrets.h is git-ignored: never commit it.
#pragma once

// Wi-Fi (2.4 GHz)
#define WIFI_SSID "your-wifi"
#define WIFI_PASS "your-wifi-password"

// Pebble backend (the same URL the app uses, no trailing slash)
#define API_BASE "https://colonist-alkalize-duo.ngrok-free.dev"

// This robot's identity. In apps/api run: npm run build && node --env-file=.env dist/scripts/provision-device.js
// and copy PEBBLE_SERIAL / PEBBLE_SECRET here.
#define DEVICE_SERIAL "PBL-XXXXXXXX"
#define DEVICE_SECRET "the-secret-printed-by-provision-device"

// ThingsBoard (device → Credentials → Access token). Leave TB_TOKEN "" to skip ThingsBoard.
#define TB_HOST "demo.thingsboard.io"
#define TB_PORT 1883
#define TB_TOKEN ""
