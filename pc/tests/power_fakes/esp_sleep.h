#pragma once
#include <stdint.h>
using esp_err_t = int;
constexpr int ESP_OK=0, ESP_SLEEP_WAKEUP_EXT1=1, ESP_EXT1_WAKEUP_ANY_LOW=0;
constexpr int ESP_PD_DOMAIN_RTC_PERIPH=0,ESP_PD_OPTION_ON=1;
int esp_sleep_get_wakeup_cause();
int esp_sleep_enable_ext1_wakeup(uint64_t,int);
void esp_sleep_pd_config(int,int);
void esp_deep_sleep_start();
