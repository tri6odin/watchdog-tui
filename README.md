# Watchdog TUI

Простое консольное приложение на Python для мониторинга домашней инфраструктуры.
История хранится в SQLite, интерфейс рисуется через `curses`.

## Запуск

Для Raspberry Pi/Linux проверкам нужны системные утилиты `ping`, `curl`,
`smbclient` и `nmcli` из NetworkManager.

```bash
python3 watchdog_tui.py
```

По умолчанию база создается рядом с приложением:

```text
watchdog.sqlite3
```

По умолчанию адреса, таймауты и секреты читаются из:

```text
watchdog.conf
```

Другой путь к конфигу:

```bash
python3 watchdog_tui.py --config /path/to/watchdog.conf
```

Другой путь к базе:

```bash
python3 watchdog_tui.py --db /path/to/watchdog.sqlite3
```

Один раз выполнить проверки и выйти без TUI:

```bash
python3 watchdog_tui.py --once
```

## Управление

- `←` / `h` - экран назад по карусели.
- `→` / `l` - экран вперед по карусели.
- `Space` / `Enter` - ручное обновление.
- `q` - выход.

После запуска приложение всегда открывает экран `ЧАСЫ`. При попадании на экран
`ОБНОВИТЬ` запускается проверка, экран блокируется до завершения, затем TUI
автоматически переключается на `ЧАСЫ`.

Штатное автообновление привязано к границам 10-минутных блоков истории, поэтому
на экране `ЧАСЫ` новая правая ячейка закрашивается сразу после очередной
автоматической проверки.

Первые проверки уже реальные для Raspberry Pi/Linux: `LCL NET` делает `ping`,
`INT NET` проверяет TCP/TLS handshake до `ya.ru:443`, `VPN` проверяет TCP/TLS
handshake до `instagram.com:443`, `WIFI` и `IOT WIFI` по очереди подключаются
к заданным SSID через `nmcli` и пингуют домашний шлюз через Wi-Fi интерфейс,
`HAOS` проверяет Home Assistant REST API `/api/config`, `NAS` проверяет
TCP-порт SMB и наличие шары через `smbclient`.
Все адреса, порты, таймауты, токены и пароли лежат в `watchdog.conf`; шаблон
без реальных секретов лежит в `watchdog.example.conf`. Токен Home Assistant
передается в `curl` через stdin-конфиг, Wi-Fi пароль передается в `nmcli --ask`
через stdin, а SMB-пароль передается в `smbclient` через переменную окружения,
а не через аргументы процесса. `ZIGBEE` пока остается заглушкой в `check_zigbee`.

В ветке Raspberry Pi/Linux карусель можно листать горизонтальными свайпами
тачскрина. По умолчанию `event_device = auto`: Watchdog ищет тачскрин через
`/sys/class/input` и `/proc/bus/input/devices`, потому что `/dev/input/event0`
может меняться после перезагрузки или изменения набора input-устройств.

Если в 10-минутном интервале уже был `FAIL` или `UNKNOWN`, ручная перепроверка
с `OK` не затирает этот статус: Watchdog сохраняет худшее состояние интервала.

## Проверка кода

```bash
python3 -m py_compile watchdog_tui.py watchdog_config.py watchdog_core.py watchdog_db.py watchdog_ui.py touch_swipe.py test_watchdog_tui.py
python3 -m unittest
```
