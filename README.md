# ERGOMS SECURE CONNECTION

Клиент и серверные скрипты для выхода в интернет через корпоративный Squid (`192.0.2.10:3128`) на **свой VPS** по **VLESS+Reality** (sing-box на порту 443).

Цепочка:

**программа → локальный sing-box (SOCKS/HTTP/TUN) → Squid CONNECT → VPS :443 (VLESS+Reality) → интернет**

---

## Подготовка VPS (один раз)

С консоли хостинга:

```bash
bash modes/vps/disable_sshd_443.sh
bash modes/vps/bootstrap_singbox_443.sh
```

Скрипт напечатает блок `server` + `transport` для `config.json`.

Проверка: `systemctl status sing-box`, `ss -lntp | grep ':443'`.

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

В `config.json` по умолчанию `tun.enabled` и `kill_switch` включены: TUN поднимается вместе с `on`, при обрыве интернет блокируется. Для окна: `poetry install --extras gui`, затем `python -m desktop gui` (или `poetry run python -m desktop gui`).

Локально после `on`: SOCKS `:1080`, HTTP `:1088`, PAC `:1089`.

---

## Настройки

Всё в одном файле `config.json` (образец: `config/config.example.json`).

| Ключ | Назначение |
|------|------------|
| `server.host` | IP/hostname VPS |
| `transport` | uuid, public_key, short_id, server_name |
| `socks_scope` | `full` или `github` (область PAC) |
| `tun.enabled` / `tun.elevate` | TUN вместе с `on` (по умолчанию вкл.), запрос прав |
| `kill_switch` | при обрыве резать интернет (по умолчанию вкл.; нужен TUN) |
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
| `tun-on` / `tun-off` | TUN |
| `reverse-on` / `reverse-off` | SSH с VPS на этот ПК |
| `encrypt` / `decrypt` | Зашифровать / расшифровать конфиг |
| `download-sing-box` | Скачать бинарник в `tools/` |
| `docker-env` / `docker-test` | Прокси для контейнеров |
| `install-service` / `uninstall-service` | служба VPN (Windows / Linux) |
| `gui` | Окно |
| `deploy` | Подсказки по VPS |
| `help` | Справка |

---

## SSH на клиент без публичного IP

Офисный Squid рвёт прямые соединения на VPS `:22`. Клиент сам открывает обратный туннель **через уже поднятый SOCKS/VLESS**.

После обновления клиента один раз `off` / `on` — в sing-box добавлен маршрут «VPS :22 через VLESS» (иначе офис снова даст `connection reset`).

На клиенте (OpenSSH Server + ключ в `creds/`, тот же что в `authorized_keys` на VPS):

```powershell
.\ergoms-secure-connection.ps1 on
.\ergoms-secure-connection.ps1 reverse-on
.\ergoms-secure-connection.ps1 status
```

Или в `config.json`: `"reverse_ssh": { "enabled": true }` — тогда `on` поднимает проброс сам.

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
├── lib/               connect_socks, http_via_socks (PAC)
├── modes/linux/       systemd-служба клиента
├── modes/windows/     WinSW-служба клиента
└── modes/vps/         bootstrap sing-box на :443
```

Не коммитьте `config.json`, `config.json.enc`, `creds/`, `logs/`, `var/`.
