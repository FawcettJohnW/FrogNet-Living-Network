// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// loop.c -- a closed loop through the memory, from an ESP32 with nothing but an HTTP client.
//
//   sensor      writes its reading into its own cell (esp, reading, !<name>) every period; holds a read on its own
//               adjustment cell (esp, adjust, !<name>) and applies what lands there (period, offset) to the readings
//               that follow. No hardware sensor: the payload is what the chip knows of itself -- free heap, minimum
//               free heap, uptime, CPU MHz, chip model and cores, IDF version, reset reason, a "temperature" that is
//               the chip's internal sensor where there is one and a modelled drift on the Linux target.
//   controller  holds a read on (esp, reading, *); for each reading that lands it computes the adjustment -- a longer
//               period when the sensor's heap headroom is thin, a shorter one when it is ample; an offset that cancels
//               the sensor's temperature drift -- and writes (esp, adjust, !<sensor>). It keeps nothing: the readings
//               are the sensors' cells, the adjustments are its own.
//
// Both roles hold reads: a GET with wait_s and min_rows that returns when the cell changes. Nobody polls, nobody
// talks to anybody; the loop closes in the memory. The same source runs on the ESP-IDF Linux target (several at once,
// against meshtastic-ram here) and on a real chip over Wi-Fi.
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_chip_info.h"
#include "esp_http_client.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"
#include "esp_event.h"
#if CONFIG_IDF_TARGET_LINUX
#include <pthread.h>
#include <time.h>
#include <unistd.h>
#else
#include "esp_mac.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#endif

static const char *TAG = "loop";
#define API CONFIG_LOOP_RAM "/meshtastic-api"

// ------------------------------------------------------------------------------------------------ the memory, over HTTP
typedef struct {
    char *buf;
    size_t len, cap;
} Body;

static esp_err_t on_http(esp_http_client_event_t *e)
{
    if (e->event_id == HTTP_EVENT_ON_DATA) {
        Body *b = e->user_data;
        if (b->len + e->data_len + 1 > b->cap) {
            b->cap = (b->len + e->data_len + 1) * 2;
            b->buf = realloc(b->buf, b->cap);
        }
        memcpy(b->buf + b->len, e->data, e->data_len);
        b->len += e->data_len;
        b->buf[b->len] = 0;
    }
    return ESP_OK;
}

// one request; the reply body (malloc'd) or NULL, status in *status
static char *http(const char *method, const char *url, const char *post, int timeout_ms, int *status)
{
    Body b = {calloc(1, 256), 0, 256};
    esp_http_client_config_t cfg = {.url = url, .event_handler = on_http, .user_data = &b, .timeout_ms = timeout_ms};
    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    esp_http_client_set_method(c, post ? HTTP_METHOD_POST : HTTP_METHOD_GET);
    if (post) {
        esp_http_client_set_header(c, "Content-Type", "application/json");
        esp_http_client_set_post_field(c, post, strlen(post));
    }
    esp_err_t err = esp_http_client_perform(c);
    *status = err == ESP_OK ? esp_http_client_get_status_code(c) : -1;
    esp_http_client_cleanup(c);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "%s %s: %s", method, url, esp_err_to_name(err));
        free(b.buf);
        return NULL;
    }
    return b.buf;
}

// write (esp, variable, instance) = bag; the cell is this session's, the memory keeps it while the session lives
static bool write_cell(const char *variable, const char *instance, cJSON *bag)
{
    cJSON *body = cJSON_CreateObject();
    cJSON_AddStringToObject(body, "service", "esp");
    cJSON_AddStringToObject(body, "variable", variable);
    cJSON_AddStringToObject(body, "instance", instance);
    cJSON_AddItemToObject(body, "bag", bag);
    char *text = cJSON_PrintUnformatted(body);
    cJSON_Delete(body);
    int status;
    char *r = http("POST", API "?op=write", text, 5000, &status);
    free(text);
    free(r);
    return status == 200;
}

// a held read on (esp, variable, instance|*): what was written past `after`, waiting up to wait_s for the first row.
// Returns the reply's cells array (caller deletes the root) and the newest id seen in *newest.
static cJSON *hold(const char *variable, const char *instance, long long after, int wait_s, cJSON **root, long long *newest)
{
    char url[320];
    snprintf(url, sizeof url, API "?op=read&service=esp&variable=%s%s%s&after=%lld&wait_s=%d&min_rows=1", variable,
             instance ? "&instance=" : "", instance ? instance : "", after, wait_s);
    int status;
    char *r = http("GET", url, NULL, (wait_s + 10) * 1000, &status);
    if (!r)
        return NULL;
    *root = cJSON_Parse(r);
    free(r);
    cJSON *cells = *root ? cJSON_GetObjectItem(*root, "cells") : NULL;
    if (cells)
        for (cJSON *c = cells->child; c; c = c->next) {
            cJSON *id = cJSON_GetObjectItem(c, "id");
            if (id && (long long)id->valuedouble > *newest)
                *newest = (long long)id->valuedouble;
        }
    return cells;
}

// ------------------------------------------------------------------------------------------------ what the chip knows
static void my_name(char *out, size_t n)
{
    if (CONFIG_LOOP_NAME[0]) {
        snprintf(out, n, "!%s", CONFIG_LOOP_NAME);
        return;
    }
#if CONFIG_IDF_TARGET_LINUX
    snprintf(out, n, "!linux%ld", (long)(time(NULL) % 100000)); // several Linux instances: distinct names
#else
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(out, n, "!%02x%02x%02x%02x", mac[2], mac[3], mac[4], mac[5]);
#endif
}

static const char *model_name(esp_chip_model_t m)
{
    switch (m) {
    case CHIP_ESP32: return "ESP32";
    case CHIP_ESP32S2: return "ESP32-S2";
    case CHIP_ESP32S3: return "ESP32-S3";
    case CHIP_ESP32C3: return "ESP32-C3";
    case CHIP_ESP32C6: return "ESP32-C6";
    case CHIP_ESP32H2: return "ESP32-H2";
    default: return "linux";
    }
}

// the "temperature": the chip's internal sensor where one exists; the Linux target drifts upward with uptime so the
// controller has something to correct
static uint32_t uptime_s(void) { return (uint32_t)(xTaskGetTickCount() / configTICK_RATE_HZ); }

static float temperature_c(void)
{
#if CONFIG_IDF_TARGET_LINUX
    return 25.0f + (float)uptime_s() * 0.05f;
#else
    return 25.0f + (float)(uptime_s() % 60) * 0.1f; // placeholder where no driver is wired
#endif
}

static cJSON *reading(uint32_t seq, int period_ms, float offset_c)
{
    esp_chip_info_t ci;
    esp_chip_info(&ci);
    cJSON *b = cJSON_CreateObject();
    cJSON_AddNumberToObject(b, "seq", seq);
    cJSON_AddNumberToObject(b, "uptime_s", (double)uptime_s());
    cJSON_AddNumberToObject(b, "heap_free", esp_get_free_heap_size());
    cJSON_AddNumberToObject(b, "heap_min", esp_get_minimum_free_heap_size());
#ifdef CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ
    cJSON_AddNumberToObject(b, "cpu_mhz", CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
#else
    cJSON_AddNumberToObject(b, "cpu_mhz", 0); // the Linux target has no configured CPU clock
#endif
    cJSON_AddStringToObject(b, "model", model_name(ci.model));
    cJSON_AddNumberToObject(b, "cores", ci.cores);
    cJSON_AddStringToObject(b, "idf", IDF_VER);
    cJSON_AddNumberToObject(b, "reset_reason", esp_reset_reason());
    float raw = temperature_c();
    cJSON_AddNumberToObject(b, "temp_raw_c", raw);
    cJSON_AddNumberToObject(b, "temp_c", raw + offset_c); // the adjusted reading: what the controller asked for applied
    cJSON_AddNumberToObject(b, "offset_c", offset_c);
    cJSON_AddNumberToObject(b, "period_ms", period_ms);
    return b;
}

// ------------------------------------------------------------------------------------------------ sensor
typedef struct {
    char name[24];
    volatile int period_ms;
    volatile float offset_c;
} Sensor;

// the sensor's held read on its own adjustment cell: each row that lands changes the readings that follow
static void sensor_hold_task(void *arg)
{
    Sensor *s = arg;
    long long after = 0;
    for (;;) {
        cJSON *root = NULL;
        cJSON *cells = hold("adjust", s->name, after, 30, &root, &after);
        if (cells)
            for (cJSON *c = cells->child; c; c = c->next) {
                cJSON *bag = cJSON_GetObjectItem(c, "bag");
                cJSON *p = bag ? cJSON_GetObjectItem(bag, "period_ms") : NULL;
                cJSON *o = bag ? cJSON_GetObjectItem(bag, "offset_c") : NULL;
                if (p) s->period_ms = (int)p->valuedouble;
                if (o) s->offset_c = (float)o->valuedouble;
                ESP_LOGI(TAG, "adjust landed: period %d ms, offset %.2f C", s->period_ms, s->offset_c);
            }
        if (root) cJSON_Delete(root);
        if (!cells) {
#if CONFIG_IDF_TARGET_LINUX
            sleep(2);
#else
            vTaskDelay(pdMS_TO_TICKS(2000));
#endif
        }
    }
}

static void sensor(void)
{
    static Sensor s;
    my_name(s.name, sizeof s.name);
    s.period_ms = CONFIG_LOOP_PERIOD_MS;
    s.offset_c = 0;
    ESP_LOGI(TAG, "sensor %s: readings every %d ms to (esp, reading, %s); holding (esp, adjust, %s)", s.name, s.period_ms,
             s.name, s.name);
#if CONFIG_IDF_TARGET_LINUX
    // the Linux port's FreeRTOS runs its tasks on one host thread, so a blocking socket read in a task stalls every task:
    // the held read gets a host thread of its own here. On a chip lwIP yields and a task is the right thing.
    pthread_t th;
    pthread_create(&th, NULL, (void *(*)(void *))sensor_hold_task, &s);
#else
    xTaskCreate(sensor_hold_task, "hold", 8192, &s, 5, NULL);
#endif
    for (uint32_t seq = 1;; seq++) {
        if (write_cell("reading", s.name, reading(seq, s.period_ms, s.offset_c)))
            ESP_LOGI(TAG, "reading %" PRIu32 " written (period %d ms, offset %.2f)", seq, s.period_ms, s.offset_c);
        vTaskDelay(pdMS_TO_TICKS(s.period_ms));
    }
}

// ------------------------------------------------------------------------------------------------ controller
static void controller(void)
{
    char name[24];
    my_name(name, sizeof name);
    ESP_LOGI(TAG, "controller %s: holding (esp, reading, *); adjustments to (esp, adjust, !<sensor>)", name);
    long long after = 0;
    for (;;) {
        cJSON *root = NULL;
        cJSON *cells = hold("reading", NULL, after, 30, &root, &after);
        if (cells)
            for (cJSON *c = cells->child; c; c = c->next) {
                cJSON *inst = cJSON_GetObjectItem(c, "instance"), *bag = cJSON_GetObjectItem(c, "bag");
                if (!inst || !bag) continue;
                double heap = cJSON_GetObjectItem(bag, "heap_free") ? cJSON_GetObjectItem(bag, "heap_free")->valuedouble : 0;
                double raw = cJSON_GetObjectItem(bag, "temp_raw_c") ? cJSON_GetObjectItem(bag, "temp_raw_c")->valuedouble : 25;
                double period = cJSON_GetObjectItem(bag, "period_ms") ? cJSON_GetObjectItem(bag, "period_ms")->valuedouble : 2000;
                // the adjustment: thin heap -> slower (up to 10 s); ample -> faster (down to 1 s); cancel the drift
                int want = heap < 40000 ? (int)(period * 1.5) : heap > 200000 ? (int)(period * 0.8) : (int)period;
                if (want < 1000) want = 1000;
                if (want > 10000) want = 10000;
                float offset = (float)(25.0 - raw);
                cJSON *adj = cJSON_CreateObject();
                cJSON_AddNumberToObject(adj, "period_ms", want);
                cJSON_AddNumberToObject(adj, "offset_c", offset);
                cJSON_AddStringToObject(adj, "by", name);
                cJSON_AddNumberToObject(adj, "for_seq", cJSON_GetObjectItem(bag, "seq") ? cJSON_GetObjectItem(bag, "seq")->valuedouble : 0);
                if (write_cell("adjust", inst->valuestring, adj))
                    ESP_LOGI(TAG, "%s: heap %.0f raw %.2f C -> period %d ms, offset %.2f C", inst->valuestring, heap, raw, want, offset);
            }
        if (root) cJSON_Delete(root);
        if (!cells) vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

// ------------------------------------------------------------------------------------------------ start
#if !CONFIG_IDF_TARGET_LINUX
static void wifi_up(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t wc = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wc));
    wifi_config_t sta = {0};
    strncpy((char *)sta.sta.ssid, CONFIG_LOOP_WIFI_SSID, sizeof sta.sta.ssid);
    strncpy((char *)sta.sta.password, CONFIG_LOOP_WIFI_PASS, sizeof sta.sta.password);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_connect());
    vTaskDelay(pdMS_TO_TICKS(5000)); // enough for DHCP on a known network; a real deployment waits for IP_EVENT_STA_GOT_IP
}
#endif

void app_main(void)
{
    ESP_LOGI(TAG, "loop on the memory at %s, role %s", CONFIG_LOOP_RAM, CONFIG_LOOP_ROLE);
#if CONFIG_IDF_TARGET_LINUX
    ESP_ERROR_CHECK(esp_event_loop_create_default()); // the HTTP client posts its events here
#else
    wifi_up();
#endif
    if (strcmp(CONFIG_LOOP_ROLE, "controller") == 0)
        controller();
    else
        sensor();
}
