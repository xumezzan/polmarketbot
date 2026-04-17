# Checklist Before Real Money

Этот файл нужен для простого ответа на вопрос:

```text
Можно ли уже идти в live trading?
```

Короткий ответ на текущем этапе:

```text
Нет, full live trading пока рано.
Paper-trading MVP уже собран.
Для small live trading нужен ещё один отдельный слой live execution и несколько защитных доработок.
```

## 1. Что уже сделано хорошо

Сейчас проект уже умеет:
- получать новости и убирать дубли
- получать structured verdict от LLM через Pydantic validation
- искать top-N market candidates
- считать `edge`
- делать deterministic `risk_engine`
- открывать и закрывать `paper trades`
- считать `PnL` и базовую статистику
- запускать весь pipeline по расписанию
- писать structured logs
- отправлять optional alerts через отдельный adapter
- прогонять unit tests для `signal_engine`, `risk_engine` и adapter mocks

Это хороший baseline для paper trading.

## 2. Что ещё НЕ готово для real money

### Live execution ещё не реализован

Пока нет:
- отдельного клиента для реального размещения ордеров в Polymarket CLOB
- подписи и безопасной работы с ключами
- логики `place order / cancel order / partial fill / retry`
- учёта реального статуса ордера после отправки
- reconciliation между локальной БД и биржей

Без этого real money запускать нельзя.

### Модель исполнения пока слишком оптимистична

Сейчас paper trading считает сделку по известной цене из сигнала.

Но в live trading появятся:
- slippage
- spread
- частичные исполнения
- проскальзывание на низкой ликвидности
- комиссии и сетевые задержки

Значит текущий `PnL` нельзя считать надёжной оценкой live `PnL`.

### Нет автоматического управления live-позицией

Пока нет:
- live monitoring открытых ордеров
- авто-выходов по времени
- авто-выходов по изменению цены
- авто-стопа при системной ошибке
- recovery после рестарта процесса

### Нет полного operational hardening

Пока не закрыты:
- production secrets management
- kill switch для немедленной остановки live order placement
- отдельный emergency mode
- audit trail для live ордеров и ошибок исполнения
- расширенные integration tests
- длительный прогон paper trading без участия человека

## 3. Что безопасно сейчас

Сейчас безопасно:
- продолжать paper trading
- гонять scheduler часами и днями
- проверять качество сигналов
- проверять блокировки `risk_engine`
- смотреть на частоту actionable signals
- собирать статистику по рынкам и новостям
- улучшать matching, risk rules и monitoring

## 4. Что пока НЕбезопасно

Пока небезопасно:
- подключать реальные деньги напрямую к текущему `paper_trader`
- автоматически отправлять реальные ордера без ручного контроля
- масштабировать лимиты выше микро-уровня
- считать paper `PnL` эквивалентом live `PnL`

## 5. Обязательный checklist перед первым live trade

Перед первым live trade должны быть готовы все пункты ниже.

### Execution layer

- отдельный `live_execution_client.py` для Polymarket CLOB
- методы:
  - `place_order`
  - `cancel_order`
  - `get_order_status`
  - `get_open_orders`
  - `get_positions`
- реальный статус исполнения сохраняется в БД
- есть idempotency или защита от повторной отправки ордера

### Risk controls

- отдельный флаг `LIVE_TRADING_ENABLED=false` по умолчанию
- kill switch из config/env
- отдельные live-лимиты, не общие с paper:
  - `LIVE_MAX_TRADE_SIZE_USD`
  - `LIVE_MAX_DAILY_EXPOSURE_USD`
  - `LIVE_MAX_OPEN_POSITIONS`
- блокировка торговли при любой ошибке синхронизации с биржей
- блокировка торговли при отсутствии свежих market data

### Monitoring

- логируется каждый live order
- логируется каждый fill / partial fill / cancel
- есть alert на:
  - live order failure
  - repeated retries
  - risk rejection spike
  - divergence between DB and exchange state
- есть heartbeat процесса

### Strategy validation

- paper trading отработал стабильный период без падений scheduler
- проверены manual review кейсы по хорошим и плохим новостям
- проверены кейсы stale news
- проверены кейсы duplicate positions
- проверены кейсы низкой ликвидности
- проверены кейсы `priced-in / convergence`

### Operations

- есть отдельный production `.env`
- API keys не лежат в коде
- есть резервный план остановки бота
- понятно, кто и как проверяет систему после рестарта

## 6. Рекомендуемый путь к small live trading

Ниже не универсальный стандарт, а практичный и осторожный rollout для этого MVP.

### Phase A — Extended paper

Оставаться в paper mode, пока не выполнено:
- минимум несколько дней непрерывной работы scheduler без ручного вмешательства
- нет необъяснимых падений pipeline
- нет silent failures
- статистика paper trades выглядит правдоподобно, а не случайно

### Phase B — Shadow live

Что делать:
- подключить live market data и live execution client
- реальные ордера НЕ отправлять
- только логировать:
  - какой live order был бы отправлен
  - по какой цене
  - какой размер

Цель:
- проверить, что execution layer правильно строит запросы
- проверить, что risk engine и execution не расходятся по данным

### Phase C — Micro live

Только после успешного shadow mode.

Рекомендуемые стартовые лимиты:
- `LIVE_MAX_TRADE_SIZE_USD = 2..5`
- `LIVE_MAX_DAILY_EXPOSURE_USD = 10..25`
- `LIVE_MAX_OPEN_POSITIONS = 1`
- только самые ликвидные рынки
- только manual supervision

Если нужна совсем прямая рекомендация:

```text
Начать с 1 открытой позиции и размера 2-5 USD на сделку.
```

### Phase D — Small live

Повышать лимиты только если одновременно выполняется всё:
- нет execution incidents
- нет неожиданных partial fill проблем
- нет проблем reconciliation
- есть положительная и стабильная статистика на микро-объёме
- все алерты и kill switch реально проверены

## 7. Стоп-условия

При любом из условий ниже live trading должен автоматически останавливаться:
- не удалось получить актуальную цену рынка
- не удалось получить статус ордера
- order placement вернул ошибку
- exchange state не совпадает с локальной БД
- превышен дневной лимит
- scheduler или execution process упал и состояние позиции не подтверждено
- отправка ордера повторилась больше одного раза без явного подтверждения

## 8. Что я бы сделал следующим перед live

Приоритет работ:

1. Сделать отдельный `live_execution_client` с dry-run mode.
2. Добавить таблицы/поля под live orders и exchange reconciliation.
3. Разделить `paper` и `live` risk limits.
4. Добавить kill switch и circuit breaker.
5. Сделать shadow live режим.
6. Только потом открыть micro live trading.

## 8.1. Как включать real OpenAI path

Для текущего состояния репо следующий стратегически правильный шаг перед новыми эвристиками в `stub`:
- переключить `LLM_MODE` с `stub` на `openai`
- оставить модель `gpt-4o-mini` для первого validation run
- держать `OPENAI_DAILY_BUDGET_USD` низким, например `1.00`

Что должно быть готово до первого OpenAI-прогона:
- реальный `OPENAI_API_KEY` должен приходить из shell или отдельного приватного `.env`
- budget cap должен быть включён
- запускать сначала на `rss + gamma + openai`, без live execution

Что смотреть после первого 1-2 дневного прогона:
- уменьшилась ли доля `general news`
- выросло ли качество `market_query`
- уменьшились ли `market_candidate_count=0`
- не выросли ли бессмысленные approvals
- сколько реально стоил день LLM-вызовов по `estimated_cost_usd`

## 9. Итоговый вывод

Текущее состояние проекта:

```text
Paper trading MVP: готов
Shadow live: почти готов концептуально, но кода ещё нет
Small live trading: пока рано
```

Практический вывод:

```text
Следующий безопасный шаг — не real money,
а live execution adapter + shadow live mode.
```
