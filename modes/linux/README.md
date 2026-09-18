# Linux-клиент

Одна команда:

```bash
curl -fsSL https://raw.githubusercontent.com/ergoms/ergoms-secure-connection/main/modes/linux/install.sh | sudo bash
```

Ставит программу в `/opt/ergoms-secure-connection` и команду `ergoms-sc`.

```bash
ergoms-sc                 # окно
ergoms-sc on              # подключить
ergoms-sc off             # отключить
ergoms-sc install-service # автозапуск (systemd)
```

Конфиг: `~/.local/share/ergoms-secure-connection/config.json`

Снять:

```bash
curl -fsSL https://raw.githubusercontent.com/ergoms/ergoms-secure-connection/main/modes/linux/uninstall.sh | sudo bash
```

Или: `sudo bash /opt/ergoms-secure-connection/uninstall.sh`

---

Из архива `*-linux-x64.tar.gz` (GitHub Releases):

```bash
tar -xzf ERGOMS-SECURE-CONNECTION-*-linux-x64.tar.gz
sudo bash "ERGOMS SECURE CONNECTION/install.sh"
```

Из репозитория (разработка): `./ergoms-secure-connection.sh …`
