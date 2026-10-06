# Сфера — установочный комплект

Установочный комплект независимой Сферы для нового клиента на новом сервере.
Каждый экземпляр получает собственные идентификаторы, PostgreSQL, Qdrant, Neo4j,
пользователей, разрешения, тома и сеть. Исходная рабочая система и её репозитории
не являются целью установки или обновления.

**Состояние: проверенная заготовка, release candidate.** На ноутбуке проверены
инициализация пустой PostgreSQL 16.14, вход, OAuth PKCE, роли, отзыв сессий,
журналируемая запись Markdown, настройка ACL и интерфейсное чтение канона — 30 проверок.
Полная установка Docker/Caddy/AI на чистой Linux-машине ещё не подтверждена.
Подробности и границы: [VALIDATION.md](VALIDATION.md).

Для повторения функционального теста используй отдельный экземпляр с
`--client installer-test`, инициализируй его командами из раздела установки ниже и запусти:

```bash
docker compose --profile setup run --rm --no-build bootstrap python tests/integration.py
```

Этот тест создаёт тестовых пользователей и задачи и изменяет тестовый Markdown.
Он отказывается запускаться для client key, отличного от `installer-test`.
На экземпляре реального клиента его не запускают.

## Состав

- Исходный код памяти, задач, сообщений, знаний, навыков, ACL, MCP и веб-интерфейса.
- Определения схем и индексов PostgreSQL; строки исходной базы не экспортированы.
- Собственная авторизация: локальные пользователи, scrypt-хеши паролей, роли
  reader/contributor/manager/owner, отзыв пользователей и сессий.
- OAuth authorization code + PKCE S256, явное согласие, одноразовые коды,
  ротация refresh-токенов и отзыв семейства при повторном использовании.
- Нейтральные AGENTS, SOUL, HARNESS, USERPROFILE и карты проекта.
- Четыре базовых навыка и реестр их версий/применений; прежние результаты
  проверки навыков и экспертные профили не наследуются.
- PostgreSQL, Qdrant, Neo4j, TLS-прокси Caddy; опциональные Ollama и обработчики AI.

Нет исторических задач, сообщений, знаний, профилей, учётных записей, паролей,
ключей или бизнес-данных исходного клиента. TREND для входа не требуется.
Бизнес-коннекторы, экспертные профили и специализированные навыки нового клиента
подключаются отдельно. Этот комплект не содержит базы 1С или данные маркетплейсов.

## Установка на новый сервер

Цель: отдельный Ubuntu 24.04 LTS x86_64, DNS-домен, доступные порты 80/443.
Для полного базового стека планируй 8 ГБ RAM и 30 ГБ диска; AI-модели требуют
дополнительных ресурсов. До проверки Linux эти размеры являются ориентиром.

1. Установи Git, Python 3 и Docker Engine с Compose по
   [официальной инструкции Docker](https://docs.docker.com/engine/install/ubuntu/).
   Команды для чистого Ubuntu доступны в [tools/install-docker-ubuntu.sh](tools/install-docker-ubuntu.sh).
2. Скопируй отдельный репозиторий и настрой экземпляр:

```bash
git clone https://github.com/ysolodukhin1-prog/sfera-starter.git sfera-acme
cd sfera-acme
python3 tools/configure.py --client acme --domain sfera.acme.example --name 'Сфера ACME'
docker compose config --quiet
docker compose build mcp
docker compose up -d postgres qdrant neo4j
docker compose --profile setup run --rm --no-build bootstrap
docker compose --profile setup run --rm --no-build bootstrap python bootstrap.py user --username admin --role owner
docker compose up -d mcp ui caddy
```

Команда создания пользователя запрашивает новый пароль скрытым вводом.
Паролей по умолчанию нет. Генерируемые файлы `.env` и `instance/bootstrap.json`
содержат секреты только нового экземпляра: не добавляй их в Git.
Повторная настройка поверх существующего `instance/` отклоняется.
Инициализация непустой базы отклоняется.

Открой `https://sfera.acme.example/login`, затем `/galactica/`.
MCP endpoint: `https://sfera.acme.example/galactica-mcp/mcp`.
OAuth discovery опубликован в `/.well-known/oauth-authorization-server` и
`/.well-known/oauth-protected-resource`. Используй OAuth-подключение своего MCP-клиента.
Скопируй нужные каталоги из `skills/` в каталог навыков агента; настрой MCP URL
именно этого клиента. Имена исторических технических схем сохранены для совместимости.

## Пользователи и права

```bash
# Пользователь для чтения
docker compose --profile setup run --rm --no-build bootstrap python bootstrap.py user --username analyst --role reader
# Работа с задачами
docker compose --profile setup run --rm --no-build bootstrap python bootstrap.py user --username operator --role contributor
# Отключение пользователя и отзыв всех его сессий
docker compose --profile setup run --rm --no-build bootstrap python bootstrap.py disable --username operator
# Сброс пароля и отзыв сессий
docker compose --profile setup run --rm --no-build bootstrap python bootstrap.py reset-password --username analyst
```

Обычные пользователи видят только собственные задачи и архивы. Owner управляет
экземпляром; manager получает изменение материалов, contributor — создание задач,
reader — чтение разрешённых разделов. Точные ограничения по разделам и материалам
настраиваются в админке ACL. Последнего активного owner отключить нельзя.
Самостоятельная регистрация пользователей через Интернет выключена.
Первый owner создаётся только командой на сервере.

## AI-профиль

Память и задачи работают без AI. Для автоматического разбора итогов, построения
векторного индекса и смысловых связей включи отдельный профиль:

```bash
docker compose --profile ai up -d ollama
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull gemma3:4b
docker compose --profile ai up -d projector protocol links
```

Ollama может работать на CPU; GPU-настройка описана в
[официальной документации](https://docs.ollama.com/docker).
LLM_BASE_URL, EMBEDDING_MODEL, PROTOCOL_MODEL и размер вектора находятся в `.env`.
При смене embedding-модели или её весов используй новую QDRANT_COLLECTION и
отдельный rebuild: нельзя смешивать несовместимые векторы.
AI-профиль ещё не прошёл полный интеграционный прогон этого комплекта.
Codex CLI на сервер не устанавливается; будущую ночную обработку рабочим компьютером
следует настраивать отдельно, с собственной идентичностью пользователя.

## Следующий клиент

Создай новый checkout на новом сервере и повтори configure с новым client/domain.
Не копируй `.env`, `instance/`, тома, дампы или учётные записи первого экземпляра.
Новые project/account UUID и все пароли создаются заново.
Пакет не делает автоматических отправок в рабочие репозитории.

## Обновление и резервные копии

Это первоначальный установщик, не инструмент обновления существующей Сферы.
Запускай bootstrap только на пустой выделенной базе. Схема фиксирована снимком;
следующие релизы должны поставлять версионированные миграции и отдельную проверку обновления.
Для backup нового экземпляра сохраняй PostgreSQL, том knowledge, `.env`,
`instance/config.json` и `instance/bootstrap.json` в отдельное защищённое хранилище.
Копии модели и индексов можно восстановить из канона; конфигурация модели должна
сохраняться вместе с backup. В Git попадают только код и нейтральные шаблоны.

```bash
umask 077
docker compose exec -T postgres pg_dump -U sfera_bootstrap -d sfera -Fc > sfera-backup.dump
```

Дамп содержит данные нового клиента и не должен попадать в репозиторий.
Проверка восстановления полного backup ещё не проведена.
