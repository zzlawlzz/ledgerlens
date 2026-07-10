# EDGAR egress-прокси через VPS-FI

**Зачем:** SEC троттлит полосу IP после burst-загрузок (наблюдалось 2026-07-10:
~15 КБ за 30 с с домашнего IP при полной скорости из Финляндии). Митигация из
ARCHITECTURE §5.6 / T-008: `EDGAR_PROXY_URL` — применяется клиентом **только**
к `*.sec.gov`, остальной трафик идёт напрямую.

## Устройство

- **VPS-FI (31.58.137.203):** `tinyproxy` на `127.0.0.1:8888` (наружу не
  открыт; установлен и включён 2026-07-10, systemd-юнит `tinyproxy`).
- **Рабочая машина:** SSH-туннель до прокси + переменная в `.env`:

```powershell
ssh -i $env:USERPROFILE\.ssh\id_ed25519_remote_server -N `
    -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes `
    -L 18888:127.0.0.1:8888 root@31.58.137.203
```

```
EDGAR_PROXY_URL=http://127.0.0.1:18888
```

## Проверка

```powershell
curl.exe -s -o NUL -w "HTTP %{http_code}, %{time_total}s" `
    -x http://127.0.0.1:18888 -A "ledgerlens you@example.com" `
    https://www.sec.gov/files/company_tickers.json
```

Ожидание: HTTP 200 за ~1–2 с. Без туннеля клиент EDGAR упадёт с внятной
ошибкой (`refusing to fall back to a direct connection`) — тихого обхода
прокси нет by design.

## Отключение

Убрать/закомментировать `EDGAR_PROXY_URL` в `.env` — клиент вернётся к прямым
запросам. Остановить прокси на VPS: `systemctl stop tinyproxy`.
