# ERGOMS SECURE CONNECTION

Выход в интернет через **свой VPS**.

- **офис:** программа → корпоративный Squid → VPS TCP 443 (VLESS+Reality)
- **дом:** программа → VPS UDP 51820 (AmneziaWG)

Домашний провайдер часто режет Reality. Офисный Squid UDP не проводит — поэтому на одном VPS оба транспорта.

Релизы: [GitHub Releases](https://github.com/ergoms/ergoms-secure-connection/releases)

---

## 1. VPS (один раз)

С консоли хостинга, под root:

```bash
curl -fsSL https://raw.githubusercontent.com/ergoms/ergoms-secure-connection/main/modes/vps/install.sh | bash
```

Если репозиторий уже на сервере: `sudo bash modes/vps/install.sh`

В панели хостинга откройте **TCP 443** и **UDP 51820**.

Конфиг для компьютера: `/var/lib/ops-content-singbox/client.json`

Только Reality, без AmneziaWG: `sudo bash modes/vps/install.sh --no-awg`

Ещё устройства: `sudo bash modes/vps/add_amneziawg_client.sh phone`

---

## 2. Windows

Скачайте `*-windows-x64-setup.exe` из [Releases](https://github.com/ergoms/ergoms-secure-connection/releases) и установите как обычную программу.

---

## 3. Linux

```bash
curl -fsSL https://raw.githubusercontent.com/ergoms/ergoms-secure-connection/main/modes/linux/install.sh | sudo bash
```

Подробности: [`modes/linux/README.md`](modes/linux/README.md).

Или из архива `*-linux-x64.tar.gz`:

```bash
tar -xzf ERGOMS-SECURE-CONNECTION-*-linux-x64.tar.gz
sudo bash "ERGOMS SECURE CONNECTION/install.sh"
```

После этого:

```bash
ergoms-sc                 # окно
ergoms-sc on              # подключить
ergoms-sc off             # отключить
ergoms-sc install-service # автозапуск
```

Конфиг в домашнем каталоге, **sudo не нужен**:

```bash
nano ~/.local/share/ergoms-secure-connection/config.json
```

`ergoms-sc` тоже запускайте без sudo — иначе конфиг окажется в `/root/.local/share/...`.
Снять: `sudo bash /opt/ergoms-secure-connection/uninstall.sh`

---

## 4. Первый запуск

1. Скопируйте `client.json` с VPS (или зашифрованный `share.enc`).
2. В окне: **Настройки → Из файла**. Либо: `ergoms-sc decrypt share.enc`
3. **Подключить** / `ergoms-sc on` (спросит пароль sudo: TUN и kill switch).

Локально после подключения: SOCKS `:1080`, HTTP `:1088`, PAC `:1089`.

---

## Команды

Одинаковы везде: `ergoms-sc …`, `./ergoms-secure-connection.sh …`, `.\ergoms-secure-connection.ps1 …`, `python -m desktop …`.

| Команда | Смысл |
|---------|--------|
| `init` | Создать `config.json` |
| `on` / `off` | Включить / выключить |
| `status` / `probe` / `test` | Состояние и проверки |
| `sandbox` | Песочница: путь до VPS мимо TUN/Amnezia |
| `tun-on` / `tun-off` | TUN |
| `reverse-on` / `reverse-off` | SSH с VPS на этот ПК |
| `ssh-setup` | `~/.ssh` include + ключ на лабу. На новом ПК: тот же `config.json`, тот же ключ в `creds/` или `~/.ssh/server-vps`, снова `ssh-setup` |
| `encrypt` / `decrypt` | Зашифровать / расшифровать конфиг |
| `download-sing-box` | Скачать официальный sing-box 1.11 в `tools/` |
| `download-sing-box-awg` | Скачать AWG-сборку (AmneziaWG) |
| `docker-env` / `docker-test` | Прокси для контейнеров |
| `install-service` / `uninstall-service` | служба VPN (Windows / Linux) |
| `gui` | Окно |
| `help` | Справка |

Из репозитория (разработка): `./ergoms-secure-connection.sh …` / `.\ergoms-secure-connection.ps1 …`. Служба: `install-service`. VPS: `./deploy.sh`.

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
| `git_proxy` / `git_via` | git через VPN: выкл / `http` (мост :1088) / `tun`. В корпоративном пресете вкл. и `tun` |
| `docker_proxy` | проксировать Docker Desktop / CLI через VPN (в корпоративном пресете вкл., иначе выкл.) |
| `corporate_proxy` | корпоративный Squid |
| `tun.sing_box_path` | пусто = авто `tools/sing-box` |
| `reverse_ssh.enabled` | проброс sshd клиента на `127.0.0.1:listen_port` VPS (по умолчанию выкл.) |

Старый `.env` при `init` один раз мигрируется в `config.json`.

По умолчанию `tun.enabled` и `kill_switch` включены. Окно: в трее **Открыть**, **Подключить / Отключить**, **Выход**. Во вкладке «Журнал» — **Копировать всё**.

### Передача конфига (шифрование)

```powershell
.\ergoms-secure-connection.ps1 encrypt                  # → config.json.enc (спросит пароль)
.\ergoms-secure-connection.ps1 encrypt share.enc -p '…' # свой путь / пароль в аргументе
# на другом ПК:
.\ergoms-secure-connection.ps1 decrypt share.enc
```

Формат: пароль + PBKDF2-HMAC-SHA256 + HMAC-CTR + HMAC-SHA256 (без внешних зависимостей).

---

## Дом (Россия) — если сайты не открываются

Проверено на домашнем Ethernet: TCP до VPS `:443` живой, а сайты мёртвые (`HTTPS probe timeout`). Это не «клиент не стартовал».

1. Нужен **AmneziaWG UDP :51820**, не Reality. Домашний DPI глотает VLESS+Reality на TCP :443. На VPS: `ss -lunp | grep 51820`.
2. Чужой туннель AmneziaVPN / Tailscale с default `0.0.0.0/0` перехватывает UDP. Если второй VPN нужен — split `/32` на IP VPS.
3. В панели хостинга открыть **UDP 51820** (не путать с TCP 443).

В журнале при норме: `дом: AmneziaWG UDP :51820`, затем `проверка выхода: OK`. Песочница: `ergoms-sc sandbox` — `AWG CONNECT` + `AWG HTTPS` OK.

Служба Amnezia без поднятого туннеля не мешает. Kill switch чужого Amnezia (`WinError 10013`) UDP всё равно может резать.

---

## SSH на клиент без публичного IP

По умолчанию выключено. Если включить, пока VPN поднят, с VPS можно зайти на этот ПК. Офисный Squid рвёт прямые соединения на VPS `:22`, поэтому клиент открывает обратный туннель **через SOCKS**.

На клиенте нужны OpenSSH Server и ключ в `creds/` (тот же, что в `authorized_keys` на VPS). В настройках: **SSH с сервера**. После обновления один раз `off` / `on`.

```bash
# с VPS:
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
├── desktop/           CLI/GUI-клиент
├── ergoms-secure-connection.ps1/.sh
├── deploy.ps1/.sh
├── config/            образцы
├── lib/               connect.py (ProxyCommand), pac.py
├── installer/         Inno Setup (Windows)
├── modes/linux/       установщик клиента + systemd
├── modes/windows/     WinSW-служба клиента
└── modes/vps/         установщик VPS (Reality + AmneziaWG)
```

Не коммитьте `config.json`, `config.json.enc`, `creds/`, `logs/`, `var/`.

---

## Сборка

```powershell
.\.vscode\setup.ps1 -Target build
```

| Путь | Что это |
|------|---------|
| `dist/ERGOMS SECURE CONNECTION/ERGOMS SECURE CONNECTION.exe` | one-dir клиент (рядом `_internal/`) |
| `dist/ERGOMS SECURE CONNECTION-Setup.exe` | установщик Inno Setup |

Установщик кладёт программу в `Program Files\ERGOMS SECURE CONNECTION` (или per-user), ярлыки, опциональный автозапуск (`--autostart`). Перед установкой новой версии снимает предыдущую, правила firewall, задачи, автозапуск и каталоги данных (`%LOCALAPPDATA%\ERGOMS SECURE CONNECTION` и наследие `ERGOMS VPN` / `ops-content`) — конфиг нужно импортировать заново. То же при удалении, плюс `off` (kill switch / PAC / git).

Только папка без Setup: `.\.vscode\setup.ps1 -Target pyinstaller`. Только Setup (после сборки): `-Target installer`. Если Inno Setup нет — ставится через `winget` (`JRSoftware.InnoSetup`).

Linux: `./.vscode/setup.sh build` → `dist/ERGOMS SECURE CONNECTION/` (в папке `install.sh`; релизный `*-linux-x64.tar.gz`).

### GitHub Release (локально)

Сборка без GitHub Actions: Windows на хосте, Linux в WSL, файлы в `artifacts/` и на [GitHub Releases](https://github.com/ergoms/ergoms-secure-connection/releases). `dist/` и `artifacts/` в git не коммитятся.

```powershell
.\.vscode\release.ps1
```

Только Windows: `-Target windows`. Только Linux (WSL Ubuntu-24.04): `-Target linux`. Только загрузка уже собранных файлов: `-Target publish`.
