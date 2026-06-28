# Watchdog TUI

Консольный TUI-watchdog для домашней инфраструктуры. Интерфейс рисуется через
`curses`, история статусов хранится в SQLite, проверки запускаются по порядку и
пишут худший статус в текущий 10-минутный интервал.

![Watchdog TUI](docs/watchdog-tui.png)

Эта ветка `linux` рассчитана на Raspberry Pi. Для Mac используется отдельная
ветка `mac`.

## Что проверяется

Порядок обновления и порядок отображения в интерфейсе:

```text
LCL NET, INT NET, VPN, WIFI, IOT WIFI, NAS, HAOS, ZIGBEE
```

На исторических экранах строки разложены в две колонки:

```text
LCL NET   IOT WIFI
INT NET   NAS
VPN       HAOS
WIFI      ZIGBEE
```

`LCL NET`: один ICMP ping до домашнего шлюза из `[lcl_net]`.

Команда:

```bash
ping -c 1 -W <timeout> <address>
```

`INT NET`: TCP connect и TLS handshake до хоста из `[int_net]`, сейчас
`ya.ru:443`. HTTP-страница не скачивается, проверяется именно нормальный TLS
handshake с SNI.

`VPN`: TCP connect и TLS handshake до хоста из `[vpn]`, сейчас
`instagram.com:443`. Эта проверка больше не зависит от `INT NET`: если обычный
интернет красный, VPN все равно проверяется отдельно.

`WIFI`: поиск Wi-Fi интерфейса через NetworkManager, подключение к SSID из
`[wifi]`, ping шлюза через Wi-Fi интерфейс и, если включено, disconnect после
проверки.

Команды по смыслу:

```bash
nmcli -t -f DEVICE,TYPE,STATE device status
nmcli --wait <timeout> radio wifi on
nmcli --wait <timeout> --ask device wifi connect <ssid> ifname <wlan>
ping -c 1 -W <timeout> -I <wlan> <gateway>
nmcli --wait 5 device disconnect <wlan>
```

Wi-Fi пароль передается в `nmcli --ask` через stdin, а не через аргументы
процесса.

`IOT WIFI`: та же логика, что `WIFI`, но с секцией `[iot_wifi]`.

`NAS`: сначала TCP connect до SMB-порта из `[nas]`, потом проверка, что нужная
шара видна через `smbclient -L`.

Команды по смыслу:

```bash
smbclient -L //<address> -U <user> -g
```

SMB-пароль передается через переменную окружения `PASSWD`, а не через аргументы
процесса.

`HAOS`: HTTP API Home Assistant из `[haos]`, сейчас `/api/config` на
`192.168.50.4:8123`. Bearer token передается в `curl` через stdin config, а не
через аргументы процесса.

Команда по смыслу:

```bash
curl --config - --max-time <timeout> --connect-timeout <timeout> http://<address>:<port>/<api_path>
```

`ZIGBEE`: пока заглушка, всегда `ok`.

Если `LCL NET` красный, все остальные проверки в этом прогоне становятся
`unknown`: без локальной сети остальные результаты были бы шумом.

## Зависимости

Python-зависимости внешними пакетами не ставятся: используются стандартные
модули `curses`, `sqlite3`, `socket`, `ssl`, `subprocess`.

Системные пакеты для Raspberry Pi/Linux:

```bash
sudo apt update
sudo apt install -y git python3 iputils-ping curl smbclient network-manager iw rfkill
```

Для TUI на `tty1` в текущей установке используется `kmscon`. Если поднимаешь
малинку с нуля и хочешь такой же полноэкранный консольный режим, нужен пакет
`kmscon` и systemd override, описанный ниже.

## Установка

Клонировать Linux-ветку:

```bash
git clone -b linux git@github.com:tri6odin/watchdog-tui.git
cd watchdog-tui
```

Подтянуть обновления:

```bash
git pull origin linux
```

Создать локальный конфиг:

```bash
cp watchdog.example.conf watchdog.conf
```

`watchdog.conf` содержит реальные адреса, токены и пароли. Он не должен
попадать в git.

## Конфиг

Шаблон лежит в `watchdog.example.conf`.

Основные секции:

`[global]`: общий `http_user_agent`.

`[lcl_net]`: адрес шлюза и timeout для ping.

`[int_net]`: host/port/timeout для TLS проверки обычного интернета.

`[vpn]`: host/port/timeout для TLS проверки VPN-доступа.

`[haos]`: адрес, порт, API path, timeout и long-lived access token Home
Assistant.

`[nas]`: адрес NAS, SMB-порт, TCP/SMB timeout, пользователь, пароль и имя шары.

`[wifi]`: основной Wi-Fi SSID, пароль, gateway, интерфейс и таймауты.

`[iot_wifi]`: IoT Wi-Fi SSID, пароль, gateway, интерфейс и таймауты.

`[touch]`: настройки свайпов для тачскрина.

Для Wi-Fi `interface = auto` означает автоопределение первого Wi-Fi интерфейса
через `nmcli device status`. Если надо зафиксировать интерфейс, укажи,
например:

```ini
interface = wlan0
```

`disconnect_after_check = true` отключает Wi-Fi интерфейс после проверки, чтобы
следующие проверки не зависели от того, какая Wi-Fi сеть была выбрана последней.
Ethernet при этом продолжает держать основное соединение.

## Запуск

Запустить TUI:

```bash
python3 watchdog_tui.py
```

Один раз выполнить проверки и выйти:

```bash
python3 watchdog_tui.py --once
```

Указать другой конфиг:

```bash
python3 watchdog_tui.py --config /path/to/watchdog.conf
```

Указать другую базу:

```bash
python3 watchdog_tui.py --db /path/to/watchdog.sqlite3
```

По умолчанию используются:

```text
watchdog.conf
watchdog.sqlite3
```

## Управление

Клавиатура:

```text
Left / h       экран назад
Right / l      экран вперед
Space / Enter  ручное обновление
q              выход
```

После запуска открывается экран `ЧАСЫ`. При попадании на экран `ОБНОВИТЬ`
запускается проверка, экран блокируется до завершения, затем TUI возвращается
на `ЧАСЫ`.

Автообновление привязано к границам 10-минутных блоков: 12:00, 12:10, 12:20 и
так далее. Поэтому на экране `ЧАСЫ` новая правая ячейка закрашивается сразу
после очередной автоматической проверки.

Если в одном 10-минутном интервале уже был `fail` или `unknown`, ручная
перепроверка с `ok` не затирает его. В истории остается худший статус
интервала, чтобы не потерять факт временной потери коннекта.

## Тачскрин

В Linux-ветке карусель можно листать горизонтальными свайпами.

Настройки:

```ini
[touch]
enabled = true
event_device = auto
swipe_min_dx = 1200
swipe_max_dt_sec = 2.2
swipe_horizontal_ratio = 1.1
```

`event_device = auto` ищет тачскрин через `/sys/class/input` и
`/proc/bus/input/devices`. Это нужно потому, что `/dev/input/event0` может
меняться после перезагрузки или изменения набора input-устройств.

Если приложение запускается не от root, пользователь должен иметь право читать
input device. Обычно помогает группа `input`:

```bash
sudo usermod -aG input example-wifi
```

После этого нужен logout/login или reboot. В текущем варианте автозапуск идет
от root, поэтому отдельные права на `/dev/input/event*` не требуются.

## Автозапуск на Raspberry Pi

На текущей Raspberry Pi TUI запускается на `tty1` через `kmsconvt@tty1`.
Проверить systemd unit:

```bash
systemctl cat kmsconvt@tty1
```

В override-е `ExecStart` указывает на wrapper:

```text
/usr/local/bin/kmscon-watchdog
```

Wi-Fi проверки используют `nmcli` для переключения сетей. Обычный пользователь
может видеть Wi-Fi и делать `ping`, но без дополнительных прав не может
выполнять `nmcli connection up/down`, `nmcli device disconnect` или
`nmcli device wifi connect`. Поэтому был выбран простой вариант: запускать сам
Watchdog из wrapper-а от root.

`/usr/local/bin/kmscon-watchdog`:

```sh
#!/bin/sh
APP_DIR=/home/pi/watchdog-tui

cd "$APP_DIR" || exit 1
exec /usr/bin/python3 watchdog_tui.py --config "$APP_DIR/watchdog.conf" --db "$APP_DIR/watchdog.sqlite3"
```

Применить wrapper:

```bash
sudo chmod +x /usr/local/bin/kmscon-watchdog
sudo systemctl restart kmsconvt@tty1
```

Проверить проверки без TUI:

```bash
sudo python3 watchdog_tui.py --once
```

Ожидаемый признак, что права на Wi-Fi достаточны:

```text
WIFI     ok
IOT WIFI ok
```

Если открыт вывод `systemctl cat ...` или другой pager, выйти можно клавишей
`q`.

Нюанс: после root-автозапуска файл `watchdog.sqlite3` может принадлежать root.
Для ручного запуска с той же базой используй `sudo` или отдельный путь к базе.

## Отладка Wi-Fi

Проверить состояние NetworkManager:

```bash
nmcli general status
nmcli device status
nmcli radio wifi
rfkill list
```

Просканировать сети:

```bash
sudo nmcli device wifi rescan ifname wlan0
sleep 3
nmcli -f IN-USE,SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY device wifi list ifname wlan0
```

Проверить, что ping идет именно через Wi-Fi:

```bash
ping -I wlan0 -c 3 -W 1 192.168.50.1
```

Если `nmcli` не видит сеть, но есть подозрение, что сеть в эфире:

```bash
sudo iw dev wlan0 scan ssid example-iot
```

Если без `sudo` команды `nmcli connection up ...` или
`nmcli device wifi connect ...` падают с `Not authorized`, это нормально для
обычного пользователя. Для текущей установки это решается root-автозапуском.

## Проверка кода

Компиляция:

```bash
python3 -m py_compile watchdog_tui.py watchdog_config.py watchdog_core.py watchdog_db.py watchdog_ui.py touch_swipe.py test_watchdog_tui.py
```

Тесты:

```bash
python3 -m unittest
```
