#pragma once
using gpio_num_t=int;
constexpr int RTC_GPIO_MODE_INPUT_ONLY=0;
inline void rtc_gpio_deinit(int) {}
inline void rtc_gpio_init(int) {}
inline void rtc_gpio_set_direction(int,int) {}
inline void rtc_gpio_pullup_en(int) {}
inline void rtc_gpio_pulldown_dis(int) {}
