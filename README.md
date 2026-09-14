# ERGOMS SECURE CONNECTION

Клиент и серверные скрипты для выхода в интернет через корпоративный Squid (`192.0.2.10:3128`) на **свой VPS** по **VLESS+Reality** (sing-box на порту 443).

Цепочка:

**офис:** программа → sing-box → Squid CONNECT → VPS TCP :443 (VLESS+Reality) → интернет

**дом:** программа → sing-box → VPS UDP :8443 (Hysteria2) → интернет

**дом (AmneziaWG):** программа → sing-box AWG → VPS UDP :51820 (AmneziaWG) → интернет

Домашний провайдер часто глотает TLS Reality; Hysteria2 — QUIC. Без **salamander** TSPU читает SNI из QUIC Initial и рвёт handshake (`timeout: no recent network activity`). Офисный Squid UDP не проводит, поэтому Reality там остаётся. На уже установленном VPS: `bash modes/vps/enable_hysteria2.sh` (пароль + `HY2_OBFS` в `credentials.env` → `transport.hysteria2`).

AmneziaWG — третий домашний dial (`transport.dial=amneziawg`). Официальный sing-box 1.11.15 его не говорит, клиент качает отдельную AWG-сборку (`download-sing-box-awg`, `tools/sing-box-awg` / `ergoms-tun-awg.exe`). На VPS это **отдельный** сервис рядом с sing-box, не замена Reality: `bash modes/vps/enable_amneziawg.sh`. Чужой туннель AmneziaVPN по-прежнему конфликт; наш AWG идёт gVisor-endpoint внутри sing-box и свой `wireguard` NIC не поднимает.

**Что проверено**

- корпоративный VPN (VLESS+Reality через Squid) — работает
- дом, Hysteria2 + salamander, SNI не Cloudflare — **работает** (Ethernet, Россия)
- дом, VLESS+Reality напрямую — DPI глотает TLS ClientHello, не использовать

---

## Подготовка VPS (один раз)

С консоли хостинга:

```bash
bash modes/vps/disable_sshd_443.sh
bash modes/vps/bootstrap_singbox_443.sh
```

Скрипт напечатает блок `server` + `transport` для `config.json`.

Проверка: `systemctl status sing-box`, `ss -lntp | grep ':443'`.

Дом, AmneziaWG (после bootstrap):

```bash
bash modes/vps/enable_amneziawg.sh
```

Скрипт напечатает `transport.amneziawg` и кусок `.conf` для вставки в настройки клиента. Проверка: `systemctl status ergoms-amneziawg`, `ss -lunp | grep ':51820'`. В панели хостинга открыть **UDP 51820**.

Подсказки: `.\deploy.ps1` / `./deploy.sh`.

---

## Клиент (Windows / Linux)

```powershell
.\ergoms-secure-connection.ps1 init
# в config.json: server.host + transport из bootstrap
.\ergoms-secure-connection.ps1 probe АДРЕС_СЕРВЕРА 443
.\ergoms-secure-connection.ps1 on
.\ergoms-secure-connection.ps1 status
.\ergoms-secure-connection.ps1 off
```

То же: `python -m desktop …` или `./ergoms-secure-connection.sh …`.

Автозапуск (служба, одна команда — спросит админа/sudo):

```powershell
.\ergoms-secure-connection.ps1 install-service     # Windows (WinSW, LocalSystem, TUN без UAC)
# снять: .\ergoms-secure-connection.ps1 uninstall-service
```

```bash
./ergoms-secure-connection.sh install-service      # Linux (systemd)
# снять: ./ergoms-secure-connection.sh uninstall-service
```

В `config.json` по умолчанию `tun.enabled` и `kill_switch` включены: TUN поднимается вместе с `on`, при обрыве интернет блокируется. Для окна: `poetry install --extras gui`, затем `python -m desktop gui` (или `poetry run python -m desktop gui`). Сборка Windows: `.\.vscode\setup.ps1 -Target build` → папка `dist/ERGOMS SECURE CONNECTION/` и установщик `dist/ERGOMS SECURE CONNECTION-Setup.exe`.

Локально после `on`: SOCKS `:1080`, HTTP `:1088`, PAC `:1089`.

Окно: в трее три пункта — **Открыть**, **Подключить / Отключить** (текст и доступность меняются по статусу), **Выход**. Во вкладке «Журнал» кнопка **Копировать всё** кладёт текущий лог в буфер обмена. Служба Amnezia без поднятого туннеля Hy2 не ломает. Если Amnezia-туннель всё же включён — в split добавьте IP VPS `/32` (домены и exe не нужны). Kill switch Amnezia (`WinError 10013`) UDP всё равно может резать.

---

## Дом (Россия) / Windows — что реально ломалось

Проверено на домашнем Ethernet: TCP до VPS `:443` живой, SOCKS CONNECT до `1.1.1.1:443` тоже, а сайты мёртвые (`HTTPS probe timeout` / `no recent network activity`). Это **не** «клиент не стартовал».

Что помогло (без этого дома не поднимается):

1. **Hysteria2 UDP :8443**, не Reality. Домашний DPI глотает VLESS+Reality на TCP :443 (`TLS handshake timed out`, HTTP 400 с VPS при этом живой). Пустой `transport.hysteria2.password` = клиент молча идёт в Reality. На VPS: `ss -lunp | grep 8443` и `HY2_PASSWORD` / `HY2_OBFS` из `/var/lib/ops-content-singbox/credentials.env`. `HY2_PORT` в этом файле может врать (`443`) — смотреть фактический listen.
2. **Salamander (obfs) на VPS и в клиенте.** TSPU расшифровывает QUIC Initial и читает SNI; порт 8443 сам по себе больше не обход (фильтр на все UDP). Без obfs — `timeout: no recent network activity` / SOCKS `rep=1`, даже когда Amnezia выключена и VPS слушает. В конфиге: `transport.hysteria2.obfs_password` = `HY2_OBFS`. На VPS снова: `bash modes/vps/enable_hysteria2.sh`.
3. **Hy2 SNI отдельно от Reality, не Cloudflare.** Reality dest остаётся `www.cloudflare.com`. В QUIC Initial `www.cloudflare.com` — стоп-лист РКН/TSPU. Hy2: `www.microsoft.com` + `insecure: true`. Поле в настройках: «Hysteria2 SNI», не путать с Reality SNI.
4. **Дом: сначала Hy2 без TUN/PAC/kill switch.** Windows `auto_route` и PAC крадут UDP до handshake. TUN split-default и watchdog — **только после** `проверка выхода: OK`. Если handshake не прошёл, kill switch не ставить (иначе интернет мёртвый, а VPN тоже).
5. **Чужой туннель с default `0.0.0.0/0`.** Amnezia / Tailscale через `100.x` / `10.13.13.2` metric 5 перехватывают QUIC. Idle-служба Amnezia (туннель не поднят) на Hy2 не влияет. В `route print` у persistent-строки слово `Default` вместо метрики — клиент такие снимает. Если второй VPN нужен: split `/32` на IP VPS через Ethernet.
6. На VPS в панели хостинга открыть **UDP 8443** (не путать с TCP 443).

В журнале при норме: `дом: Hy2 salamander — QUIC Initial без открытого SNI`, затем `проверка выхода: OK`, затем `Hysteria2 живой — ставлю TUN split default`. Песочница (`python -m desktop sandbox`): `hy2 CONNECT` + `hy2 HTTPS` OK.

---

## Настройки

Всё в одном файле `config.json` (образец: `config/config.example.json`).

| Ключ | Назначение |
|------|------------|
| `server.host` | IP/hostname VPS |
| `transport` | VLESS: uuid, public_key, short_id, `server_name` (Reality dest). Дом: `hysteria2.password`, `obfs_password`, свой `server_name`. Либо `amneziawg` (ключи, UDP-порт, Jc/H*) |
| `socks_scope` | `full` или `github` (область PAC) |
| `tun.enabled` / `tun.elevate` | TUN вместе с `on` (по умолчанию вкл.), запрос прав |
| `kill_switch` | при обрыве резать интернет (по умолчанию вкл.; нужен TUN) |
| `git_proxy` | проксировать git через VPN (в корпоративном пресете вкл., иначе выкл.) |
| `docker_proxy` | проксировать Docker Desktop / CLI через VPN (в корпоративном пресете вкл., иначе выкл.) |
| `corporate_proxy` | корпоративный Squid |
| `tun.sing_box_path` | пусто = авто `tools/sing-box` |
| `reverse_ssh.enabled` | проброс sshd клиента на `127.0.0.1:listen_port` VPS |

Старый `.env` при `init` один раз мигрируется в `config.json`.

### Передача конфига (шифрование)

```powershell
.\ergoms-secure-connection.ps1 encrypt                  # → config.json.enc (спросит пароль)
.\ergoms-secure-connection.ps1 encrypt share.enc -p '…' # свой путь / пароль в аргументе
# на другом ПК:
.\ergoms-secure-connection.ps1 decrypt share.enc
```

Формат: пароль + PBKDF2-HMAC-SHA256 + HMAC-CTR + HMAC-SHA256 (без внешних зависимостей).

---

## Команды

| Команда | Смысл |
|---------|--------|
| `init` | Создать `config.json` |
| `on` / `off` | Включить / выключить |
| `status` / `probe` / `test` | Состояние и проверки |
| `sandbox` | Песочница: путь до VPS мимо TUN/Amnezia |
| `tun-on` / `tun-off` | TUN |
| `reverse-on` / `reverse-off` | SSH с VPS на этот ПК |
| `encrypt` / `decrypt` | Зашифровать / расшифровать конфиг |
| `download-sing-box` | Скачать официальный sing-box 1.11 в `tools/` |
| `download-sing-box-awg` | Скачать AWG-сборку (AmneziaWG) |
| `docker-env` / `docker-test` | Прокси для контейнеров |
| `install-service` / `uninstall-service` | служба VPN (Windows / Linux) |
| `gui` | Окно |
| `deploy` | Подсказки по VPS |
| `help` | Справка |

---

## SSH на клиент без публичного IP

По умолчанию включено (дом и офис): пока VPN поднят, с VPS можно зайти на этот ПК. Офисный Squid рвёт прямые соединения на VPS `:22`, поэтому клиент открывает обратный туннель **через SOCKS** (Hy2 или Reality).

На клиенте нужны OpenSSH Server и ключ в `creds/` (тот же, что в `authorized_keys` на VPS). В настройках: **SSH с сервера**.

После обновления один раз `off` / `on`.

```powershell
.\ergoms-secure-connection.ps1 on
.\ergoms-secure-connection.ps1 status
```

С этого VPS:

```bash
bash modes/vps/ssh-to-client.sh 2222 ПОЛЬЗОВАТЕЛЬ_КЛИЕНТА
# то же: ssh -p 2222 ПОЛЬЗОВАТЕЛЬ_КЛИЕНТА@127.0.0.1
```

Слушает только `127.0.0.1` на VPS. Несколько клиентов — разные `reverse_ssh.listen_port`.

---

## Окружение (Poetry)

Нужны Python 3.10–3.14 и [Poetry](https://python-poetry.org/docs/#installation). Виртуальное окружение создаётся в `.venv`.

```powershell
poetry install              # CLI
poetry install --extras gui # + окно (PySide6)
.\ergoms-secure-connection.ps1 status
```

То же: `poetry run python -m desktop …`. Обёртки `ergoms-secure-connection.ps1` / `ergoms-secure-connection.sh` берут Python из `.venv`, если оно есть.

---

## Структура

```
ERGOMS SECURE CONNECTION/
├── desktop/           CLI/GUI-клиент (VLESS+Reality)
├── ergoms-secure-connection.ps1/.sh
├── deploy.ps1/.sh
├── config/            образцы
├── lib/               connect.py (ProxyCommand), pac.py
├── installer/         Inno Setup (Windows)
├── modes/linux/       systemd-служба клиента
├── modes/windows/     WinSW-служба клиента
└── modes/vps/         bootstrap sing-box на :443
```

Не коммитьте `config.json`, `config.json.enc`, `creds/`, `logs/`, `var/`.

---

## Сборка (Windows)

```powershell
.\.vscode\setup.ps1 -Target build
```

Результат:

| Путь | Что это |
|------|---------|
| `dist/ERGOMS SECURE CONNECTION/ERGOMS SECURE CONNECTION.exe` | one-dir клиент (рядом `_internal/`) |
| `dist/ERGOMS SECURE CONNECTION-Setup.exe` | установщик Inno Setup |

Установщик кладёт программу в `Program Files\ERGOMS SECURE CONNECTION` (или per-user), ярлыки, опциональный автозапуск (`--autostart`). Перед установкой новой версии снимает предыдущую, правила firewall, задачи, автозапуск и каталоги данных (`%LOCALAPPDATA%\ERGOMS SECURE CONNECTION` и наследие `ERGOMS VPN` / `ops-content`) — конфиг нужно импортировать заново. То же при удалении, плюс `off` (kill switch / PAC / git).

Только папка без Setup: `.\.vscode\setup.ps1 -Target pyinstaller`. Только Setup (после сборки): `-Target installer`. Если Inno Setup нет — ставится через `winget` (`JRSoftware.InnoSetup`).

Linux: `./.vscode/setup.sh build` → `dist/ERGOMS SECURE CONNECTION/ERGOMS SECURE CONNECTION`.

### GitHub Release (локально)

Сборка без GitHub Actions: Windows на хосте, Linux в WSL, файлы в `artifacts/` и на [GitHub Releases](https://github.com/DohaoSTR/ergoms-secure-connection/releases). `dist/` и `artifacts/` в git не коммитятся.

```powershell
.\.vscode\release.ps1
```

Только Windows: `-Target windows`. Только Linux (WSL Ubuntu-24.04): `-Target linux`. Только загрузка уже собранных файлов: `-Target publish`.
