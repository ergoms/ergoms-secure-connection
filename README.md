# ops-content

Сценарии для выхода в интернет и к GitHub из сети, где прямой доступ закрыт корпоративным посредником Squid (`192.0.2.10:3128`). Обход строится только через **свой виртуальный сервер**.

В этой сети обращения к `github.com` обычно отклоняются с кодом 403. Squid разрешает CONNECT на порт **443**, но блокирует SSH-порт **22**. Поэтому на сервере поднимают службу на 443-м порту.

Цепочка (SOCKS):

**программа → локальный HTTP-мост → SSH SOCKS → Squid → ваш сервер :443 → интернет**

Цепочка (singbox):

**программа → локальный HTTP/PAC → sing-box (VLESS+Reality) → Squid → ваш сервер :443 → интернет**

---

## Подготовка сервера

### SSH на :443 (`MODE=socks`)

```bash
bash modes/vps/bootstrap_sshd_443.sh
```

### sing-box VLESS+Reality на :443 (`MODE=singbox`)

На одном IP нельзя одновременно держать `sshd` и sing-box на 443.

```bash
bash modes/vps/disable_sshd_443.sh
bash modes/vps/bootstrap_singbox_443.sh
```

Скрипт напечатает блок `transport` — скопируйте его в `config.json` на клиенте.

Откат на SSH: `systemctl disable --now sing-box`, затем снова `bootstrap_sshd_443.sh`.

Подсказки по режиму: `./deploy.sh` или `.\deploy.ps1` (читают `MODE` из `.env`).

---

## Клиент (CLI)

Один Python-пакет `desktop/` на Windows и Linux:

```text
python -m desktop <команда>
./ops-content.sh <команда>      # Linux
.\ops-content.ps1 <команда>     # Windows
```

| Платформа | Возможности |
|-----------|-------------|
| Linux / Windows | `on` / `off` / `status`, TUN, Docker helpers, watchdog, `gui` |
| Linux | дополнительно systemd: `install-service` / `uninstall-service` |

### Быстрый старт

```bash
# Linux
chmod +x ops-content.sh deploy.sh modes/vps/*.sh modes/socks/*.sh
./ops-content.sh init
# правки в .env (MODE=socks|singbox, TUN=…) и config.json
./ops-content.sh probe АДРЕС_СЕРВЕРА 443
./ops-content.sh on
source ./var/cli.env
```

```powershell
# Windows
.\ops-content.ps1 init
# правки в .env и config.json (для singbox — секция transport + TUN=1)
.\ops-content.ps1 probe АДРЕС_СЕРВЕРА 443
.\ops-content.ps1 on
# или: python -m desktop on
```

Служба пользователя (Linux, MODE=socks):

```bash
./ops-content.sh install-service
systemctl --user status ops-content-socks
```

Отключение: `./ops-content.sh off` или `./ops-content.sh uninstall-service`.

---

## Файлы настроек

| Путь | Назначение |
|------|------------|
| `config.json` | Корп. прокси, SSH, исключения, `transport`, `tun` |
| `config/` | Образцы |
| `.env` | `MODE`, `SOCKS_SCOPE`, `TUN`, … |
| `creds/` | Ключи SSH, known_hosts |
| `logs/`, `var/` | Журналы и runtime |

### Пример `.env`

```env
# socks | singbox
MODE=singbox
SOCKS_SCOPE=full
TUN=1
TUN_ELEVATE=1
# HTTP_BRIDGE_PORT=1088
```

---

## TUN и Docker

- `TUN=1` в `.env` — после `on` поднимается системный TUN (sing-box); нужен для DNS из Docker Desktop.
- `python -m desktop tun-on` / `tun-off` — вручную; пишет `TUN=` в `.env`.
- `python -m desktop download-sing-box` — скачать бинарник в `tools/`.
- `python -m desktop docker-env` — `var/docker.env` + compose-сниппет.
- `python -m desktop docker-test` — curl из контейнера через HTTP-мост.

---

## Исключения из туннеля

```json
"proxy_bypass": ["*.intranet.example", "*.local"],
"proxy_bypass_via": "direct"
```

- `direct` — мимо туннеля и Squid;
- `corporate` — через корпоративный Squid, не через ваш сервер.

---

## Команды

```text
python -m desktop <команда>
./ops-content.sh <команда>          # Linux
.\ops-content.ps1 <команда>         # Windows
```

| Команда | Смысл |
|---------|--------|
| `init` | Создать `.env` / `config.json` из образцов |
| `on` / `off` | Включить / выключить по `MODE` (+ TUN если `TUN=1`) |
| `start` / `stop` | Только SSH-туннель (`MODE=socks`) |
| `status` | Состояние |
| `probe ХОСТ [ПОРТ]` | CONNECT через Squid |
| `test` | Проверка обхода |
| `tun-on` / `tun-off` | TUN поверх SOCKS / в sing-box |
| `download-sing-box` | Скачать sing-box в `tools/` |
| `docker-env` / `docker-test` | Прокси для контейнеров |
| `watch` | Следить за SOCKS и переподключать |
| `gui` | Окно настроек |
| `install-service` / `uninstall-service` | systemd --user (Linux) |
| `deploy` | Подсказки по подготовке VPS |
| `help` | Справка |

---

## Структура

```
ops-content/
├── .env / config.json    локальные настройки
├── ops-content.ps1/.sh   обёртки CLI
├── desktop/              Python-клиент (python -m desktop)
├── deploy.ps1/.sh        подсказки по MODE
├── config/               образцы
├── creds/                ключи
├── lib/                  connect_proxy, http_via_socks, probe
└── modes/
    ├── socks/            systemd + run-tunnel.sh
    └── vps/              bootstrap sshd / sing-box на :443
```

Нужны: `bash` или PowerShell, Python 3, `git`, `curl`, клиент SSH; для `MODE=singbox` / TUN — sing-box.

---

## На что обратить внимание

- Репозитории лучше по HTTPS (`https://github.com/...`), не git+ssh (порт 22 режется).
- Для закрытых репозиториев — [токен GitHub](https://github.com/settings/tokens).
- Не публикуйте `.env`, `config.json`, `creds/`, `logs/`, `var/`.
