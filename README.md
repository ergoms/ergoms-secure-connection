# ERGOMS SECURE CONNECTION

Клиент и серверные скрипты для выхода в интернет через корпоративный HTTP-прокси (Squid) на **свой VPS** по **VLESS+Reality** (sing-box на порту 443).

Цепочка:

**офис:** программа → sing-box → Squid CONNECT → VPS TCP :443 (VLESS+Reality) → интернет

**дом:** программа → sing-box AWG → VPS UDP :51820 (AmneziaWG) → интернет

Домашний провайдер часто глотает TLS Reality. Офисный Squid UDP не проводит, поэтому Reality там остаётся. Дома — AmneziaWG.

AmneziaWG (`transport.dial=amneziawg`). Официальный sing-box 1.11.15 его не говорит, клиент качает отдельную AWG-сборку (`download-sing-box-awg`, `tools/sing-box-awg` / `ergoms-tun-awg.exe`). На VPS это **отдельный** сервис рядом с sing-box, не замена Reality: `bash modes/vps/enable_amneziawg.sh`. Чужой туннель AmneziaVPN по-прежнему конфликт; наш AWG идёт gVisor-endpoint внутри sing-box и свой `wireguard` NIC не поднимает.

**Что проверено**

- корпоративный VPN (VLESS+Reality через Squid) — работает
- дом, AmneziaWG — работает (Ethernet, Россия)
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

Скрипт кладёт ключи в **`creds/awg/`** репозитория: `client.json` + `pc.conf` (первый клиент). Проверка: `systemctl status ergoms-amneziawg`, `ss -lunp | grep ':51820'`. В панели хостинга открыть **UDP 51820**.

Ещё клиенты (каждый — свой `.conf`, свой IP `10.66.66.x`):

```bash
bash modes/vps/add_amneziawg_client.sh phone laptop
bash modes/vps/add_amneziawg_client.sh --count 5
python3 modes/vps/awg_clients.py list
```

Подсказки: `.\deploy.ps1` / `./deploy.sh`.

---

## Клиент (Windows / Linux)

Релиз: [GitHub Releases](https://github.com/DohaoSTR/ergoms-secure-connection/releases) — Windows `*-Setup.exe`, Linux x64 `*-linux-x64.tar.gz`.

### Linux (tar.gz)

Архив сам не запускается. Распаковать и запустить бинарник (пробелы в имени — кавычки обязательны). Папку `_internal` не удалять и не отделять от программы.

```bash
cd ~/Downloads
tar -xzf ERGOMS-SECURE-CONNECTION-*-linux-x64.tar.gz
cd "ERGOMS SECURE CONNECTION"
chmod +x "ERGOMS SECURE CONNECTION"
./"ERGOMS SECURE CONNECTION"                 # окно
# CLI (нет DISPLAY или без GUI):
./"ERGOMS SECURE CONNECTION" init
# вставить server.host + transport из bootstrap, либо:
./"ERGOMS SECURE CONNECTION" decrypt share.enc
./"ERGOMS SECURE CONNECTION" on
./"ERGOMS SECURE CONNECTION" status
./"ERGOMS SECURE CONNECTION" off
```

`on` спросит sudo (TUN / kill switch). Конфиг: `~/AppData/Local/ERGOMS SECURE CONNECTION/config.json` (своё место — `ERGOMS_SC_DATA`). Служба systemd — только из репозитория (`./ergoms-secure-connection.sh install-service`), в tar.gz её нет.

### Из репозитория

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

Окно: в трее три пункта — **Открыть**, **Подключить / Отключить** (текст и доступность меняются по статусу), **Выход**. Во вкладке «Журнал» кнопка **Копировать всё** кладёт текущий лог в буфер обмена. Служба Amnezia без поднятого туннеля AWG не ломает. Если Amnezia-туннель всё же включён — в split добавьте IP VPS `/32` (домены и exe не нужны). Kill switch Amnezia (`WinError 10013`) UDP всё равно может резать.

---

## Дом (Россия) / Windows — что реально ломалось

Проверено на домашнем Ethernet: TCP до VPS `:443` живой, SOCKS CONNECT до `1.1.1.1:443` тоже, а сайты мёртвые (`HTTPS probe timeout` / `no recent network activity`). Это **не** «клиент не стартовал».

Что помогло (без этого дома не поднимается):

1. **AmneziaWG UDP :51820**, не Reality. Домашний DPI глотает VLESS+Reality на TCP :443 (`TLS handshake timed out`, HTTP 400 с VPS при этом живой). На VPS: `ss -lunp | grep 51820` и `bash modes/vps/enable_amneziawg.sh`.
2. **Дом: AWG без чужого default `0.0.0.0/0`.** Amnezia / Tailscale через `100.x` / `10.13.13.2` metric 5 перехватывают UDP. Idle-служба Amnezia (туннель не поднят) не влияет. В `route print` у persistent-строки слово `Default` вместо метрики — клиент такие снимает. Если второй VPN нужен: split `/32` на IP VPS через Ethernet.
3. На VPS в панели хостинга открыть **UDP 51820** (не путать с TCP 443).

В журнале при норме: `дом: AmneziaWG UDP :51820`, затем `проверка выхода: OK`, затем TUN split default. Песочница (`python -m desktop sandbox`): `AWG CONNECT` + `AWG HTTPS` OK.

---

## Настройки

Всё в `config.json` (образец: `config/config.example.json`). AmneziaWG — отдельный `amneziawg.conf` рядом.

| Ключ | Назначение |
|------|------------|
| `server.host` | IP/hostname VPS |
| `transport` | VLESS: uuid, public_key, short_id, `server_name` (Reality dest). `dial`: `vless-reality` / `amneziawg` |
| `creds/awg/*.conf` | Клиентские AWG-ключи (по файлу на устройство) |
| `socks_scope` | `full` или `github` (область PAC) |
| `tun.enabled` / `tun.elevate` | TUN вместе с `on` (по умолчанию вкл.), запрос прав |
| `kill_switch` | при обрыве резать интернет (по умолчанию вкл.; нужен TUN) |
| `git_proxy` | проксировать git через VPN (в корпоративном пресете вкл., иначе выкл.) |
| `docker_proxy` | проксировать Docker Desktop / CLI через VPN (в корпоративном пресете вкл., иначе выкл.) |
| `corporate_proxy` | корпоративный Squid |
| `tun.sing_box_path` | пусто = авто `tools/sing-box` |
| `reverse_ssh.enabled` | проброс sshd клиента на `127.0.0.1:listen_port` VPS (по умолчанию выкл.) |

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

По умолчанию выключено. Если включить, пока VPN поднят, с VPS можно зайти на этот ПК. Офисный Squid рвёт прямые соединения на VPS `:22`, поэтому клиент открывает обратный туннель **через SOCKS** (AWG или Reality).

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

Linux: `./.vscode/setup.sh build` → `dist/ERGOMS SECURE CONNECTION/ERGOMS SECURE CONNECTION` (релизный `*-linux-x64.tar.gz` — см. [Linux (tar.gz)](#linux-targz)).

### GitHub Release (локально)

Сборка без GitHub Actions: Windows на хосте, Linux в WSL, файлы в `artifacts/` и на [GitHub Releases](https://github.com/DohaoSTR/ergoms-secure-connection/releases). `dist/` и `artifacts/` в git не коммитятся.

```powershell
.\.vscode\release.ps1
```

Только Windows: `-Target windows`. Только Linux (WSL Ubuntu-24.04): `-Target linux`. Только загрузка уже собранных файлов: `-Target publish`.
