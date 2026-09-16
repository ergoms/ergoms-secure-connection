# Manual checklist after refactor

- [ ] Office: VLESS+Reality via Squid — `on` / `status` / `off`
- [ ] Home AmneziaWG — connect, exit probe OK, then TUN/kill switch
- [ ] `disable` clears PAC / git / Docker / kill switch
- [ ] Watchdog reconnect after SOCKS drop
- [ ] GUI: load settings, save settings, import/export config
- [x] VPN on: `ssh -G <алиас>` читает `~/.ssh/config` (HostName не равен самому алиасу, если в конфиге задан другой)
- [x] VPN on: `ssh <алиас>` на хост из конфига (офис/лаба/VPS) — сессия живая, не NXDOMAIN и не мгновенный `CreateProcessW` / `posix_spawnp`
- [ ] VPN on: Cursor Remote-SSH на тот же хост (не reverse-ssh)
- [ ] Новый ПК: `ssh-setup` пишет include, `ssh vps-server` и lab без правки ручных путей
