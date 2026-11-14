# Постмортем — недоступность OpenCloud снаружи (2026-11-13/14)

- **Статус:** устранён
- **Критичность:** высокая (публичные точки входа недоступны; сам сервис жив)
- **Длительность:** ~17 ч 15 мин публичных 504 (примерно 2026-11-13 06:52 → 2026-11-14 00:07 MSK)
- **Затронуто:** `cloud.metoda.cc` (OpenCloud), `auth.metoda.cc` (Keycloak),
  `hermes.metoda.cc`, `gitlab.metoda.cc`, GitLab SSH через adminvps
- **Не затронуто:** workload OpenCloud, PVC, секреты, доступ из LAN через
  split-horizon DNS, SSH до adminvps
- **Ответственный:** infra (tower_cloud)
- **Источники:** сессии opencode `ses_e2998875…`, `ses_e293d1a2…`,
  `ses_e29306a1…`, `ses_e2907f7f…`, `ses_e29025da…`, `ses_e28fac34…`

## Кратко

OpenCloud всё это время был здоров: pod `1/1 Running`, `status.php` 200
(7.2.4), PVC Bound, ExternalSecret синхронизирован, ingress и сертификат
готовы. Проблема была в **публичном пути входа**: Traefik на `adminvps`
отдавал **504** на все публичные vhost'ы, потому что не мог добраться до
кластера через туннель WireGuard.

Сложились две независимые причины:

1. Основной WAN (ZenCom, `eth0.2`, `100.64.48.134/20`) потерял upstream-сессию.
   mwan3 переключился на 4G-резерв (DomOnline, `wan2`). Установленный
   UDP-поток WireGuard переключение не пережил.
2. Даже после восстановления основного WAN провайдер **дропал transport-пакеты
   WireGuard (тип 4), пропуская handshake (типы 1/2)** — DPI на уровне
   протокола. Перезапуск туннеля это не лечит.

Публичный доступ восстановили, заменив WireGuard на TCP-путь
**Xray VLESS Reality + SSH reverse tunnel**, позже добавили
**fallback через wstunnel (WSS)**. WireGuard выведен из эксплуатации.

## Влияние

| Область | Влияние |
|---|---|
| Публичный web (`cloud`, `auth`, `hermes`, `gitlab`) | 504 от Traefik ~17 ч |
| GitLab SSH (`adminvps:30222`) | недоступен |
| Данные/доступность OpenCloud | нет — pod, PVC, секреты, доступ из LAN в порядке |
| Клиенты в LAN | работали через split-horizon, но браузер ругался: ingress отдаёт сертификат внутреннего `tower-cloud-ca` |
| Telegram-egress Hermes | позже отвалился при удалении WG; исправлен в ту же ночь (переведён на Xray SOCKS) |
| Алертинг | отсутствовал; об аварии узнали от пользователя |

## Таймлайн (MSK)

| Время | Событие |
|---|---|
| ~06:42 | Upstream основного WAN (ZenCom) падает; mwan3 переключается на 4G `wan2`. Последний WG-handshake ~06:52. |
| 22:34 | Пользователь спрашивает «что с opencloud?» — начинается разбор. |
| 22:42 | OpenCloud признан здоровым; публичные vhost'ы отдают 504; WG-handshake протух 15 ч 42 мин назад. |
| 22:49 | Найдена причина №1: failover mwan3; старый conntrack/NAT-мэппинг WG больше не доходит до adminvps. |
| 22:59 | Спроектирована профилактика: flush conntrack, WG-watchdog, внешний мониторинг, IaC. |
| 23:13 | Правки IaC закоммичены локально (не применялись): flush mwan3 в `router_openwrt`, watchdog `keenetic_wireguard`, `public-health.sh`, роль мониторинга, документация. |
| 23:16 | Согласовано и применено на `router` + `keenetic`. |
| 23:35 | `ifdown/ifup wan` возвращает основной WAN (залипшая DHCP/upstream-сессия, не авария провайдера). WG-handshake проходит, но transport по-прежнему не идёт: на роутере 0 входящих data-пакетов, хотя VPS их отправляет. Подозрение на DPI. |
| 23:47 | Решающий тест: двусторонний UDP-echo с archasus через тот же WAN → 10/10 запросов и 10/10 ответов. Transport WireGuard при этом не проходит → подтверждён DPI именно по WireGuard, а не CGNAT/общий блок UDP. |
| 23:51 | Решение: строить туннель на Xray. |
| 2026-11-14 00:07 | Xray VLESS Reality + SSH reverse tunnel поднят; публичные хосты 200/302, `public-health.sh` зелёный. Политика mwan3 изменена `balanced` → `wan_wanb`. |
| 00:13 | Коммит `18df23f` — публичный ingress через Xray reverse tunnel. WG-watchdog удалён. |
| 00:14–00:26 | Интерфейсы WAN подписаны ZenCom/DomOnline (комментарий в `/etc/config/network`); позже коммит `d5a3a0e`. |
| 00:28–01:10 | WireGuard подтверждён бесполезным (100% потерь на data) и удалён с обеих сторон и из IaC; ADR-0006. Egress Hermes переведён на Xray SOCKS; fingerprint `chrome` → `firefox`, Reality вынесен на `:443` через SNI-passthrough (коммиты `53fe462`, `ebb8025`). |
| 01:12–01:14 | Объяснено «подключение не защищено» на телефоне: split-horizon + внутренний `tower-cloud-ca`; это не авария. |
| 01:18–01:23 | `flush_conntrack` в mwan3 дополнен событиями `connected`/`disconnected` для tracking-only failover; применено. |
| 01:26–01:56 | Fallback на wstunnel (WSS) + автоматическое переключение Traefik (`relay.metoda.cc`, таймер 30 с); проверено остановкой Xray-туннеля; ADR-0007. |
| 09:42 | Запушены `04ef364` и `30c5c19`; рабочее дерево чистое. |

## Корневая причина

### 1. Failover WAN убил долгоживущий поток WireGuard

Линк основного WAN оставался up, DHCP-аренда живой, поэтому netifd не делал
`ifdown/ifup`; mwan3 зафиксировал только **tracking**-сбой. Старый поток
`192.168.1.216:50827 → 78.17.99.16:51820` сохранял протухший conntrack
mark/NAT-мэппинг и отправлял handshake в никуда. adminvps также продолжал
долбить устаревший endpoint (`89.188.180.2:50827`).

4G-резерв тоже был нездоров: mwan3track писал повторяющиеся 100% loss до
`1.0.0.1`, при этом интерфейс оставался `online`, то есть failover ненадёжен.

### 2. DPI провайдера дропает transport WireGuard (решающая причина)

После возврата основного WAN control plane работал, а данные — нет:

- handshake WireGuard (типы 1/2) проходил в обе стороны;
- transport WireGuard (тип 4) от adminvps до роутера не доходил вообще
  (tcpdump: 0 входящих; счётчик `received` на Keenetic не растёт);
- произвольный двусторонний UDP по тому же пути работал 10/10.

Вывод: провайдер (или CGNAT/DPI на нём) распознаёт WireGuard и режет data.
Это совпадает с прежним поведением и находится вне контроля домашних хостов.
Смена порта, flush conntrack и watchdog'и это не лечат.

## Устранение

1. **Временно:** `ifdown/ifup wan` вернул основной WAN (залипшая
   upstream-сессия). Туннелю это не помогло из-за DPI.
2. **Постоянный публичный путь (ADR-0006):**
   `интернет → adminvps Traefik → 127.0.0.1:10443 (SSH reverse) →
   Xray VLESS Reality → k8s-node-1 → 127.0.0.1:443 (ingress)`.
   GitLab SSH: `adminvps:30222 → Traefik → 127.0.0.1:12222 → k8s-node-1:30222`.
   VLESS Reality теперь также слушает публичный `:443` через SNI-passthrough
   Traefik (`www.oracle.com`), прямой `:36243` оставлен фолбэком.
3. **WireGuard выведен из эксплуатации** на Keenetic и adminvps, включая
   firewall-правила, разрешения danted, подсеть `10.200.0.0/24` и watchdog.
   ADR-0003 помечен как superseded.
4. **Egress Hermes** переведён с мёртвого `socks5://10.200.0.1:1080` на
   `socks5://10.10.120.101:1081` (Xray SOCKS).
5. **Fallback-транспорт (ADR-0007):** `wstunnel` через
   `wss://relay.metoda.cc`, таймер на adminvps атомарно переключает Traefik
   между `10443` (primary) и `10444` (fallback) за ~30 с.
6. **Гигиена роутера:** `flush_conntrack` =
   `ifup ifdown connected disconnected` для `wan`/`wan2`; политика mwan3
   `balanced` → `wan_wanb` (4G «зависает» на TCP после handshake, поэтому
   только failover).

## Что получилось хорошо

- Сам сервис не был под угрозой; диагностика отделила здоровье workload от
  пути входа.
- Решающий UDP-echo тест позволил отличить DPI от общей проблемы UDP/CGNAT
  до выбора решения.
- Публичный путь восстановлен в ту же ночь, фикс закодирован в IaC
  (роли `xray_tunnel`, `wstunnel_fallback`, обновлённые
  `adminvps_traefik`, `router_openwrt`).
- Переключение на fallback проверено реальной остановкой основного туннеля.

## Что получилось плохо

- **Не было мониторинга/алертинга:** авария длилась ~17 ч, о ней узнали от
  пользователя. Playbook мониторинга всё ещё требует sudo на archasus.
- Долгоживущий UDP-туннель на multi-WAN роутере оказался единой точкой отказа;
  семантика failover под него не проектировалась.
- 4G-резерв ненадёжен для TCP после handshake, но mwan3 считает его `online`.
- Поведение DPI провайдера недокументировано и не видно с хостов — понадобился
  контролируемый эксперимент.

## Задачи

| # | Задача | Статус |
|---|---|---|
| 1 | Заменить WG для публичного ingress на Xray VLESS Reality + SSH reverse tunnel | сделано (`18df23f`, ADR-0006) |
| 2 | Вывести WireGuard с хостов и из IaC/документации | сделано (`53fe462`) |
| 3 | Починить Telegram-egress Hermes через Xray SOCKS | сделано (`53fe462`, `ebb8025`) |
| 4 | Стабилизировать Xray под DPI (firefox fingerprint, Reality на `:443`) | сделано (`ebb8025`) |
| 5 | Добавить fallback wstunnel (WSS) с автопереключением Traefik | сделано (`30c5c19`, ADR-0007) |
| 6 | Чистить conntrack при tracking-only failover mwan3 | сделано (`04ef364`) |
| 7 | Установить таймер внешнего мониторинга (`public-health.sh`, опционально healthchecks.io) | **открыто — нужен `-K`/sudo на archasus** |
| 8 | Разобраться с ненадёжным 4G `wan2` для TCP-failover | **открыто** |
| 9 | Второй relay (Cloudflare Tunnel/Tailscale/второй VPS) на случай блокировки IP adminvps или падения VPS | **открыто (варианты в ADR-0007)** |
| 10 | Повысить надёжность основного WAN у провайдера (ZenCom upstream) | **открыто** |
| 11 | Мобильные клиенты: установить `tower-cloud-ca` или убрать split-horizon для `cloud.metoda.cc` | **открыто (нужно решение)** |

## Выводы

- Нельзя делать multi-WAN роутер носителем единственного долгоживущего
  UDP-туннеля для публичного доступа; считайте, что каждый flap WAN убивает
  поток.
- `online` в mwan3 не доказывает рабочую связность — проверяйте end-to-end.
- Если туннель «делает handshake, но не передаёт данные», проверяйте
  произвольные UDP/TCP по тому же пути, чтобы отделить DPI от CGNAT/NAT.
- Мониторинг не должен зависеть от того пути, который он мониторит: нужен
  внешний prober и независимый канал алертов.

## Ссылки

- `docs/network/CHANGELOG.md` — записи 2026-11-13/14
- `docs/adr/0006-public-ingress-via-xray.md`
- `docs/adr/0007-wstunnel-fallback-transport.md`
- `ansible/roles/xray_tunnel`, `ansible/roles/wstunnel_fallback`,
  `ansible/roles/router_openwrt`, `ansible/roles/adminvps_traefik`
- `scripts/public-health.sh`, `ansible/playbooks/monitoring.yml`
- Коммиты: `18df23f`, `53fe462`, `ebb8025`, `d5a3a0e`, `04ef364`, `30c5c19`
