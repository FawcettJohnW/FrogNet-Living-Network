# ESP32 on the memory — a closed loop with nothing but an HTTP client

`loop/` is one ESP-IDF project, two roles, that closes a control loop through the shared memory. No Meshtastic, no
FrogNet host software, no library on the chip: the memory has an HTTP front, and an ESP32 can write a cell and hold a
read on one with `esp_http_client`.

| role | writes | holds a read on | does |
|---|---|---|---|
| sensor | `(esp, reading, !<name>)` every period | `(esp, adjust, !<name>)` | applies period and offset to the readings that follow |
| controller | `(esp, adjust, !<sensor>)` per reading | `(esp, reading, *)` | period from heap headroom, offset cancelling the temperature drift |

No hardware sensor: the payload is what the chip knows of itself — free and minimum heap, uptime, CPU MHz, chip
model and cores, IDF version, reset reason, a temperature that is the internal sensor where one exists and a modelled
drift (+0.05 °C/s) on the Linux target, so the controller has something to correct.

A held read over HTTP: `GET /meshtastic-api?op=read&service=esp&variable=adjust&instance=!x&after=<id>&wait_s=30&min_rows=1`
returns when a row lands past `after` (or at `wait_s`), and the chip asks again from the newest id. Nobody polls.

## Run it here (ESP-IDF Linux target)

The same source builds for a real chip (`idf.py set-target esp32`, Wi-Fi from `menuconfig` → "Closed loop on the
memory") and for Linux, where several instances run as processes against `bin/meshtastic-ram`:

    . ~/esp/esp-idf/export.sh                      # IDF 5.3; the Linux target needs no Xtensa toolchain
    cd esp32/loop
    idf.py --preview set-target linux && idf.py build                                   # the sensor
    idf.py -B build-ctl -D SDKCONFIG=sdkconfig.ctl -D SDKCONFIG_DEFAULTS=sdkconfig.defaults.ctl --preview set-target linux
    idf.py -B build-ctl -D SDKCONFIG=sdkconfig.ctl build                                 # the controller
    ../../bin/meshtastic-ram --listen 127.0.0.1:18899 &
    build/loop.elf & build-ctl/loop.elf

What it shows (30 s, 2026-11-01):

    sensor:      reading 1 written (period 2000 ms, offset 0.00)
    controller:  !linux59471: heap … raw 25.00 C -> period 1600 ms, offset 0.00 C
    sensor:      adjust landed: period 1600 ms, offset 0.00 C
                 reading 2 written (period 1600 ms, offset 0.00)
    controller:  … raw 25.10 C -> period 1280 ms, offset -0.10 C
    …            period 1024 → 1000 ms (floor); offset tracks the drift, temp_c stays at 25.0

Each round trip is one reading period. The cells, as the memory holds them:

    (esp, reading, !linux59471) {"seq":38,"uptime_s":38,"model":"linux","cores":1,"idf":"v5.3.2","reset_reason":1,
                                 "temp_raw_c":26.9,"temp_c":25.05,"offset_c":-1.85,"period_ms":1000,…}
    (esp, adjust,  !linux59471) {"period_ms":1000,"offset_c":-1.9,"by":"!ctl","for_seq":38}

## Other ways to simulate the chip

- **QEMU (Espressif's fork)**: a real ESP32 image with a network device — true to the silicon, slower, needs the Xtensa
  toolchain and the QEMU build (both from dl.espressif.com, which this workspace cannot reach).
- **Wokwi** (CLI or browser): hosted, Wi-Fi through their gateway; good for showing someone, not for a test here.

The Linux target is the one that runs in CI and here; the code is identical on the chip.

## Found on the way

- IDF's `install.sh` wants a constraints file from dl.espressif.com; `IDF_PYTHON_CHECK_CONSTRAINTS=no` skips it, and then
  pip's newest `idf-component-manager` (3.x) and `esp-idf-kconfig` (3.x) are too new for IDF 5.3 — pin `<2.0` and
  `<3.0`.
- `esp_http_client` posts its events to the default event loop; on the Linux target nothing creates it unless you do.
- The Linux port's FreeRTOS runs every task on one host thread, so a blocking socket read in a task stalls the
  scheduler: the sensor's held read runs on a host thread there and a FreeRTOS task on a chip.
- The Linux target has no heap accounting (`heap_free` reads as 2^32−1) and no `esp_timer`; uptime comes from ticks.
