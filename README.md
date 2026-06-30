# Watchdog TUI

Консольный TUI-watchdog для домашней инфраструктуры на Raspberry Pi. Интерфейс
рисуется через `curses`, история хранится в SQLite, проверки запускаются по
порядку и сохраняют худший статус в текущий 10-минутный интервал.

![Watchdog TUI](docs/watchdog-tui.png)

Текущая ветка `linux` рассчитана на Raspberry Pi OS Lite и запуск на `tty1`
через `kmscon`.

## Оглавление

- [Что это](#что-это)
- [Быстрая установка](#быстрая-установка)
- [Обновление на Raspberry Pi](#обновление-на-raspberry-pi)
- [Host-setup скрипт](#host-setup-скрипт)
- [Конфиг Watchdog](#конфиг-watchdog)
- [Автозапуск и KMSCON](#автозапуск-и-kmscon)
- [Что проверяется](#что-проверяется)
- [Управление](#управление)
- [Тачскрин](#тачскрин)
- [Отладка Wi-Fi](#отладка-wi-fi)
- [Отладка KMSCON](#отладка-kmscon)
- [Разработка](#разработка)

## Что это

Watchdog показывает состояние домашней сети и сервисов в виде компактной
истории:

```text
LCL NET, INT NET, VPN, WIFI, IOT WIFI, NAS, HAOS, ZIGBEE
```

Основные свойства:

- главный экран после запуска - `HOURS`;
- автообновление привязано к границам 10-минутных блоков;
- ручная перепроверка не стирает уже пойманный `fail` или `unknown` в текущем
  10-минутном интервале;
- Wi-Fi-проверки рассчитаны на root-запуск, потому что NetworkManager требует
  права для переключения сетей;
- Wi-Fi radio просыпается один раз на проверку `WIFI + IOT WIFI` и выключается
  после нее;
- секреты не передаются через аргументы процессов.

Рекомендуемая система: Raspberry Pi OS Lite 64-bit. Desktop-окружение не нужно:
setup-скрипт настраивает CLI-загрузку и выводит TUI на `tty1`.

## Быстрая установка

### 1. Подготовить SSH key для приватной GitHub-репы

На Raspberry Pi под пользователем, который будет делать `git pull`:

```bash
ssh-keygen -t ed25519 -C "watchdog-pi"
cat ~/.ssh/id_ed25519.pub
```

Можно оставить путь `~/.ssh/id_ed25519` и пустой passphrase.

Добавь public key в GitHub:

```text
watchdog-tui -> Settings -> Deploy keys -> Add deploy key
```

Для Raspberry Pi достаточно read-only deploy key. Проверка:

```bash
ssh -T git@github.com
```

Нормальный ответ GitHub может говорить, что shell access не предоставляется.
Главное, чтобы не было `Permission denied (publickey)`.

### 2. Скачать репозиторий

```bash
cd "$HOME"
git clone -b linux git@github.com:tri6odin/watchdog-tui.git
cd watchdog-tui
```

### 3. Запустить системную настройку

```bash
sudo scripts/configure_raspberry_pi_host.sh
```

Скрипт поставит пакеты, настроит `kmscon`, установит шрифт, создаст runner для
автозапуска и интерактивно соберет `watchdog.conf`.

Обычный setup спрашивает только поля, которые отличаются от дома к дому:

```text
Local gateway address
Internet TLS host
VPN TLS host
Home Assistant address
Home Assistant long-lived token
NAS address
NAS SMB user/password/share
Main Wi-Fi SSID/password
IoT Wi-Fi SSID/password
```

Порты, таймауты, Home Assistant API path, Wi-Fi interface и прочие технические
поля заполняются дефолтами. Расширенный режим:

```bash
sudo env ADVANCED_WATCHDOG_CONF=1 scripts/configure_raspberry_pi_host.sh
```

### 4. Проверить конфиг и один прогон

```bash
sudo nano watchdog.conf
sudo python3 watchdog_tui.py --once
```

Если один прогон выглядит нормально, запускай TUI на `tty1`:

```bash
sudo systemctl restart kmsconvt@tty1
```

## Обновление на Raspberry Pi

```bash
cd "$HOME/watchdog-tui"
git pull origin linux
sudo systemctl restart kmsconvt@tty1
```

Если после обновления менялся setup-скрипт или системные зависимости, можно
перезапустить подготовку хоста:

```bash
sudo scripts/configure_raspberry_pi_host.sh
```

Если `watchdog.conf` уже существует, setup-скрипт оставит его как есть.

## Host-setup скрипт

Скрипт [scripts/configure_raspberry_pi_host.sh](scripts/configure_raspberry_pi_host.sh)
рассчитан на уже клонированную репу.

Он делает:

- ставит базовые системные пакеты;
- ставит `kmscon`;
- при необходимости временно подключает Debian Bookworm только для `kmscon`;
- устанавливает `docs/fonts/JetBrainsMono-Compact.ttf`;
- пишет `/etc/kmscon/kmscon.conf`;
- пишет `/usr/local/bin/kmscon-watchdog`;
- пишет systemd override для `kmsconvt@tty1`;
- отключает `getty@tty1` и GUI target;
- создает `watchdog.conf`, если его еще нет.

Базовые пакеты:

```text
git python3 iproute2 iputils-ping curl smbclient network-manager iw rfkill fontconfig
```

Пакеты для TUI на `tty1`:

```text
kmscon libtsm4 libutempter0
```

Если `kmscon` недоступен в текущих репозиториях, скрипт временно добавляет
Bookworm source с pinning-ом только для:

```text
kmscon libtsm4 libutempter0
```

После установки Bookworm source удаляется автоматически. Оставить его можно так:

```bash
sudo env REMOVE_BOOKWORM_AFTER_INSTALL=0 scripts/configure_raspberry_pi_host.sh
```

Проверить, что временный source удален:

```bash
test ! -f /etc/apt/sources.list.d/debian-bookworm.list
test ! -f /etc/apt/preferences.d/99-kmscon-from-bookworm
dpkg -l kmscon libtsm4 libutempter0
```

Полезные переменные:

```text
APP_DIR=<путь к репозиторию>
WATCHDOG_CONFIG_PATH=$APP_DIR/watchdog.conf
RUNNER_PATH=/usr/local/bin/kmscon-watchdog
KMSCON_FONT_SIZE=16
ADVANCED_WATCHDOG_CONF=0
OVERWRITE_WATCHDOG_CONF=0
CONFIGURE_WATCHDOG_CONF=1
```

Примеры:

```bash
sudo env APP_DIR=/opt/watchdog-tui scripts/configure_raspberry_pi_host.sh
sudo env OVERWRITE_WATCHDOG_CONF=1 scripts/configure_raspberry_pi_host.sh
sudo env CONFIGURE_WATCHDOG_CONF=0 scripts/configure_raspberry_pi_host.sh
sudo env KMSCON_FONT_SIZE=14 scripts/configure_raspberry_pi_host.sh
```

## Конфиг Watchdog

Основной конфиг: `watchdog.conf`.

Шаблон: [watchdog.example.conf](watchdog.example.conf).

`watchdog.conf` содержит реальные адреса, токены и пароли. Он не должен
попадать в git. Setup-скрипт пишет его с правами `0600`, поэтому редактировать
обычно нужно через `sudo nano watchdog.conf`.

Секции:

```ini
[global]
http_user_agent = watchdog-tui/1.0

[lcl_net]
address = 192.168.50.1
timeout_seconds = 1.0

[int_net]
host = ya.ru
port = 443
timeout_seconds = 1.0

[vpn]
host = instagram.com
port = 443
timeout_seconds = 5.0

[haos]
address = 192.168.50.4
port = 8123
api_path = /api/config
timeout_seconds = 2.0
token = paste_home_assistant_long_lived_token_here

[nas]
address = 192.168.50.3
port = 445
tcp_timeout_seconds = 1.0
smb_timeout_seconds = 5.0
smb_user = watchdog
smb_pass = paste_smb_password_here
smb_share = BACKUP

[wifi]
ssid = example-wifi
password = paste_wifi_password_here
gateway = 192.168.50.1
interface = auto
connect_timeout_seconds = 25.0
ping_timeout_seconds = 1.0
disconnect_after_check = true

[iot_wifi]
ssid = example-iot
password = paste_iot_wifi_password_here
gateway = 192.168.50.1
interface = auto
connect_timeout_seconds = 25.0
ping_timeout_seconds = 1.0
disconnect_after_check = true

[touch]
enabled = true
event_device = auto
swipe_min_dx = 1200
swipe_max_dt_sec = 2.2
swipe_horizontal_ratio = 1.1
```

Для Wi-Fi `interface = auto` означает автоопределение первого Wi-Fi интерфейса
через NetworkManager. Если нужно зафиксировать интерфейс:

```ini
interface = wlan0
```

`disconnect_after_check = true` отключает Wi-Fi interface и выключает Wi-Fi
radio после проверки. Это сделано специально: основной канал держит Ethernet,
а Wi-Fi-модуль просыпается только на время проверки.

## Автозапуск и KMSCON

TUI запускается на `tty1` через `kmsconvt@tty1`.

Шрифт лежит в репе:

```text
docs/fonts/JetBrainsMono-Compact.ttf
```

Setup-скрипт устанавливает его в:

```text
/usr/local/share/fonts/JetBrainsMono/JetBrainsMono-Compact.ttf
```

KMSCON config:

```text
/etc/kmscon/kmscon.conf
```

Дефолтное содержимое:

```text
font-name=JetBrains Mono Compact
font-size=16
font-dpi=96
```

Если на экране нужно больше символов, можно уменьшить `font-size`:

```bash
sudo nano /etc/kmscon/kmscon.conf
sudo systemctl restart kmsconvt@tty1
```

Проверить имя шрифта:

```bash
fc-scan --format '%{family[0]} %{style[0]}\n' docs/fonts/JetBrainsMono-Compact.ttf
```

Runner:

```text
/usr/local/bin/kmscon-watchdog
```

Пример runner:

```sh
#!/bin/sh
set -eu

APP_DIR=/path/to/watchdog-tui
WATCHDOG_CONFIG_PATH=/path/to/watchdog-tui/watchdog.conf

cd "$APP_DIR"
exec /usr/bin/python3 watchdog_tui.py --config "$WATCHDOG_CONFIG_PATH" --db "$APP_DIR/watchdog.sqlite3"
```

Watchdog запускается от root. Это нужно для Wi-Fi-проверок через `nmcli`.
Обычный пользователь может видеть Wi-Fi и делать `ping`, но без дополнительных
прав часто не может выполнять `nmcli device wifi connect`,
`nmcli device disconnect` и `nmcli radio wifi off`.

Нюанс: после root-автозапуска файл `watchdog.sqlite3` может принадлежать root.
Для ручного запуска с той же базой используй `sudo` или отдельный путь к базе.

## Что проверяется

Порядок обновления:

```text
LCL NET, INT NET, VPN, WIFI, IOT WIFI, NAS, HAOS, ZIGBEE
```

Исторические экраны разложены в две колонки:

```text
LCL NET   IOT WIFI
INT NET   NAS
VPN       HAOS
WIFI      ZIGBEE
```

`LCL NET`: ICMP ping до домашнего шлюза.

```bash
ping -c 1 -W <timeout> <address>
```

`INT NET`: TCP connect и TLS handshake до `[int_net]`, по умолчанию
`ya.ru:443`. HTTP-страница не скачивается.

`VPN`: TCP connect и TLS handshake до `[vpn]`, по умолчанию
`instagram.com:443`. Эта проверка не зависит от `INT NET`.

`WIFI` и `IOT WIFI`: включить Wi-Fi radio один раз, проверить обе сети по
очереди, потом выключить radio. Для подключения watchdog сначала ищет
сохраненный NetworkManager connection profile по SSID и делает
`nmcli connection up`. Если profile не найден или не поднялся, используется
fallback через `nmcli device wifi connect`.

```bash
nmcli -t -f DEVICE,TYPE,STATE device status
nmcli --wait <timeout> radio wifi on
nmcli --wait <timeout> connection up id <profile> ifname <wlan>
# fallback:
nmcli --wait <timeout> --ask device wifi connect <ssid> ifname <wlan>
ip -4 -o addr show dev <wlan> scope global
ping -c 3 -W <timeout> -I <wlan-ipv4> <gateway>
nmcli --wait 5 device disconnect <wlan>
nmcli --wait 5 radio wifi off
```

Важно: ping идет через IPv4-адрес `wlan0`, а не просто через имя интерфейса.
Это защищает от ситуации, когда Linux отправляет пакет через `wlan0`, но
выбирает source address с другого интерфейса.

Wi-Fi пароль передается в `nmcli --ask` через stdin только при fallback на
`device wifi connect`. Если найден saved profile, пароль берется из
NetworkManager profile.

`NAS`: TCP connect до SMB-порта, потом проверка, что нужная шара видна через
`smbclient -L`.

```bash
smbclient -L //<address> -U <user> -g
```

SMB-пароль передается через переменную окружения `PASSWD`.

`HAOS`: HTTP API Home Assistant, по умолчанию `/api/config`.

```bash
curl --config - --max-time <timeout> --connect-timeout <timeout> http://<address>:<port>/<api_path>
```

Bearer token передается в `curl` через stdin config.

`ZIGBEE`: пока заглушка, всегда `ok`.

Если `LCL NET` красный, все остальные проверки в этом прогоне становятся
`unknown`: без локальной сети остальные результаты были бы шумом.

## Управление

Клавиатура:

```text
Left / h       экран назад
Right / l      экран вперед
Space / Enter  ручное обновление
q              выход
```

После запуска открывается экран `HOURS`. При попадании на экран `UPDATE`
запускается проверка, экран блокируется до завершения, затем TUI возвращается
на `HOURS`.

Автообновление привязано к границам 10-минутных блоков: 12:00, 12:10, 12:20 и
так далее. Новая правая ячейка на экране `HOURS` закрашивается после очередной
автоматической проверки.

Если в одном 10-минутном интервале уже был `fail` или `unknown`, ручная
перепроверка с `ok` не затирает его. В истории остается худший статус
интервала.

## Тачскрин

Карусель можно листать горизонтальными свайпами.

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

В текущей установке приложение запускается от root, поэтому отдельные права на
`/dev/input/event*` не нужны. Если запускать не от root, пользователю нужны
права на чтение input device. Обычно помогает группа `input`:

```bash
sudo usermod -aG input "$USER"
```

После этого нужен logout/login или reboot.

## Отладка Wi-Fi

Проверить NetworkManager:

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

Проверить адрес Wi-Fi-интерфейса и ping через него:

```bash
ip -4 -o addr show dev wlan0 scope global
ping -I <wlan0-ipv4> -c 3 -W 1 192.168.50.1
```

Проверить маршрут, который выбрал бы Linux:

```bash
ip route get 192.168.50.1
```

Если `nmcli` не видит сеть, но сеть точно есть в эфире:

```bash
sudo iw dev wlan0 scan ssid example-iot
```

Если без `sudo` команды `nmcli connection up ...` или
`nmcli device wifi connect ...` падают с `Not authorized`, это нормально для
обычного пользователя. Текущая установка решает это root-автозапуском.

## Отладка KMSCON

Проверить unit:

```bash
systemctl cat kmsconvt@tty1
systemctl status kmsconvt@tty1 --no-pager -l
journalctl -u kmsconvt@tty1 -b --no-pager -n 120
```

Перезапустить TUI:

```bash
sudo systemctl restart kmsconvt@tty1
```

Проверить, что `getty@tty1` выключен:

```bash
systemctl status getty@tty1 --no-pager -l || true
```

Если открыт pager у `systemctl` или `journalctl`, выйти можно клавишей `q`.

## Разработка

Структура:

```text
watchdog_tui.py      # argparse, загрузка конфига, запуск once/TUI
watchdog_config.py   # чтение watchdog.conf
watchdog_core.py     # статусы, порядок проверок, агрегация и runner
watchdog_checks/     # реальные проверки: network, wifi, nas, haos, zigbee
watchdog_db.py       # SQLite история
watchdog_ui.py       # curses UI
touch_swipe.py       # Linux touch input
```

Компиляция:

```bash
python3 -m py_compile watchdog_tui.py watchdog_config.py watchdog_core.py watchdog_db.py watchdog_ui.py touch_swipe.py test_watchdog_tui.py watchdog_checks/*.py
```

Тесты:

```bash
python3 -m unittest
```
