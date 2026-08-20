# ops-content

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
.\ops-content.ps1 init
# в config.json: server.host + transport из bootstrap
.\ops-content.ps1 probe АДРЕС_СЕРВЕРА 443
.\ops-content.ps1 on
.\ops-content.ps1 status
.\ops-content.ps1 off
```

То же: `python -m desktop …` или `./ops-content.sh …`.

В `config.json`: `"tun": { "enabled": true }` поднимает TUN вместе с `on`. Для окна: `pip install -r requirements-desktop.txt`, затем `python -m desktop gui`.

Локально после `on`: SOCKS `:1080`, HTTP `:1088`, PAC `:1089`.

---

## Настройки

Всё в одном файле `config.json` (образец: `config/config.example.json`).

| Ключ | Назначение |
|------|------------|
| `server.host` | IP/hostname VPS |
| `transport` | uuid, public_key, short_id, server_name |
| `socks_scope` | `full` или `github` (область PAC) |
| `tun.enabled` / `tun.elevate` | TUN вместе с `on`, запрос прав |
| `corporate_proxy` | корпоративный Squid |
| `tun.sing_box_path` | пусто = авто `tools/sing-box` |

Старый `.env` при `init` один раз мигрируется в `config.json`.

### Передача конфига (шифрование)

```powershell
.\ops-content.ps1 encrypt                  # → config.json.enc (спросит пароль)
.\ops-content.ps1 encrypt share.enc -p '…' # свой путь / пароль в аргументе
# на другом ПК:
.\ops-content.ps1 decrypt share.enc
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
| `encrypt` / `decrypt` | Зашифровать / расшифровать конфиг |
| `download-sing-box` | Скачать бинарник в `tools/` |
| `docker-env` / `docker-test` | Прокси для контейнеров |
| `gui` | Окно |
| `deploy` | Подсказки по VPS |
| `help` | Справка |

---

## Структура

```
ops-content/
├── desktop/           CLI/GUI-клиент (VLESS+Reality)
├── ops-content.ps1/.sh
├── deploy.ps1/.sh
├── config/            образцы
├── lib/               connect_proxy, http_via_socks, probe
└── modes/vps/         bootstrap sing-box на :443
```

Не коммитьте `config.json`, `config.json.enc`, `creds/`, `logs/`, `var/`.
