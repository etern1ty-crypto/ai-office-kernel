# AI-Office Kernel

MVP-каркас для проекта "Виртуальный ИИ-Офис": один Telegram-бот принимает сообщения, локальный секретарь на Ollama ведет диалог, вызывает backend-tools и при необходимости эскалирует тяжелую разработку в локально установленный и уже авторизованный `gemini`.

Важно: проект не обходит авторизацию и не извлекает чужие токены или веб-сессии. `GeminiCLIAdapter` только автоматизирует CLI-команду, которую пользователь сам установил и авторизовал в своей среде.

## Компоненты

- `TelegramGateway` - единая точка входа для группового чата.
- `SecretaryAgentLoop` - новый agent loop: секретарь отвечает, вызывает tools, получает результаты и продолжает диалог.
- `ToolRuntime` - backend-tools для файлов, grep, git, safe shell, фоновых процессов, URL fetch, web search, local coder и Gemini escalation.
- `TaskSession` - fallback-режим активной задачи в Telegram: обсуждение, выбор backend, подтверждение модели и запуска.
- `Router` - быстрый выбор роли по триггерам `@dev`, `@qa`, `@sec` и простым эвристикам.
- `AgentOrchestrator` - собирает контекст, запускает локальные Ollama-роли или Gemini CLI и выгружает Ollama-модель перед тяжелым CLI-процессом.
- `BaseCLIAdapter` / `GeminiCLIAdapter` - изолированный слой для запуска `gemini --prompt ... --output-format json`.
- `OllamaClient` - локальная генерация и принудительная выгрузка модели через `keep_alive=0`.
- `WorkspaceTools` / `WorkspaceToolRunner` - безопасное чтение файлов и запуск команд внутри заданного workspace.
- `setup` - интерактивный установщик зависимостей, моделей и Gemini CLI auth.
- `doctor` - проверка готовности окружения перед запуском.

## Кастомные промпты

Роли читают системные промпты из файлов:

- `prompts/secretary.md` - локальный секретарь/менеджер на Ollama.
- `prompts/developer.md` - сильный разработчик для Gemini CLI или локального coder backend.
- `prompts/qa.md` - контролер/ревьюер.

Путь задается через:

```bash
AI_OFFICE_PROMPT_DIR=/home/nekach/projects/ai-office-kernel/prompts
```

Если файла нет, используется встроенный дефолт из `roles.py`.

## Агентный секретарь

Основной режим теперь такой:

```text
Ты пишешь обычное сообщение
↓
локальный секретарь понимает задачу
↓
если надо, вызывает backend-tools
↓
safe/medium tools выполняются автоматически внутри workspace
↓
Gemini CLI escalation и опасные действия ждут подтверждения
↓
секретарь возвращает нормальный человеческий ответ
```

Это решает проблему, когда секретарь раньше говорил "сервер запущен", хотя ничего не запускал. Теперь он должен либо получить реальный tool-result, либо честно попросить подтверждение/данные.

CLI-проверка без Telegram:

```bash
ai-office-kernel agent "Проверь, какие файлы есть в текущем workspace"
```

Минимальный HTTP API:

```bash
ai-office-kernel api --host 127.0.0.1 --port 8787
curl -s http://127.0.0.1:8787/chat \
  -H 'Content-Type: application/json' \
  -d '{"chat_id":1,"text":"Проверь README и кратко скажи, что это за проект"}'
```

Подтверждение опасного действия через API:

```bash
curl -s http://127.0.0.1:8787/confirm \
  -H 'Content-Type: application/json' \
  -d '{"chat_id":1}'
```

## Локальные агенты

Локальная часть реализована через Ollama:

- обычный Telegram-текст идет в agentic secretary на `AI_OFFICE_ROUTER_MODEL`.
- `@sec` / `/sec` остаются fallback-прямым вызовом секретаря.
- `@dev` может идти в Gemini CLI или локальную coder-модель через `AI_OFFICE_DEVELOPER_BACKEND=gemini|local`.
- `@qa` может идти в Gemini CLI или локальную QA-модель через `AI_OFFICE_QA_BACKEND=gemini|local`.

Рекомендуемый старт под агентного секретаря:

```bash
AI_OFFICE_ROUTER_MODEL=qwen3:8b
AI_OFFICE_LOCAL_QA_MODEL=qwen3:8b
```

Fallback, если `qwen3:8b` не установлен:

```bash
AI_OFFICE_ROUTER_MODEL=llama3.1:8b-instruct-q4_K_M
```

Минимальный локальный coder-режим:

```bash
AI_OFFICE_DEVELOPER_BACKEND=local
AI_OFFICE_LOCAL_CODER_MODEL=qwen2.5-coder:7b-instruct-q4_K_M
```

Долгие Gemini CLI задачи рассчитаны на ожидание до 20 минут по умолчанию:

```bash
AI_OFFICE_CLI_TIMEOUT_SECONDS=1200
AI_OFFICE_PROGRESS_FIRST_SECONDS=45
AI_OFFICE_PROGRESS_INTERVAL_SECONDS=60
```

Во время выполнения `/status` показывает heartbeat: сколько идет задача, backend и модель. Это не скрытые рассуждения модели, а контроль живости процесса.

Ручные local tools в Telegram остаются как debug/fallback:

```text
/tools
/workspace
/ls
/read <file>
/scan [path]
/cmd --cwd <dir> npm install
/confirm_tool
/bg --cwd <dir> npm run dev -- --host 0.0.0.0
/confirm_tool
/procs
/stop <id>
```

Ручной `/cmd` требует `/confirm_tool`. Агентный секретарь может сам вызывать safe/medium tools внутри `AI_OFFICE_WORKSPACE_ROOT`; Gemini CLI escalation и опасные shell-действия требуют `/confirm_agent` или обычного ответа `да`.

## Gemini CLI как сервисный backend

Адаптер использует официальный headless-режим Gemini CLI:

```bash
gemini --prompt "..." --output-format json --approval-mode auto_edit --skip-trust
```

Поддерживаемые настройки:

- `AI_OFFICE_GEMINI_OUTPUT_FORMAT=json|stream-json|text`
- `AI_OFFICE_GEMINI_APPROVAL_MODE=default|auto_edit|yolo|plan`
- `AI_OFFICE_GEMINI_MODEL=auto|gemini-*`
- `AI_OFFICE_GEMINI_RESUME=latest` или конкретный session id
- `AI_OFFICE_GEMINI_INCLUDE_DIRECTORIES=src,tests`
- `AI_OFFICE_GEMINI_ALL_FILES=true`
- `AI_OFFICE_GEMINI_SANDBOX=true`

`json` возвращает итоговый `response`, `session_id` и `stats`. `stream-json` возвращает JSONL-события `init`, `message`, `tool_use`, `tool_result`, `error`, `result`; адаптер собирает из них текст, события инструментов и usage.

Смотри подробности: [docs/gemini-cli-integration.md](docs/gemini-cli-integration.md).
Полный runbook запуска: [docs/runbook.md](docs/runbook.md).

`setup` также пишет `.gemini/settings.json` в корень проекта и workspace-root: subagents и shell tool отключены для сервисных запусков, чтобы Gemini CLI не отдавал в Telegram ошибки вида `LocalAgentExecutor` и `run_shell_command`.

## Быстрый старт

```bash
cd /home/nekach/projects/ai-office-kernel
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Полностью автоматизированный bootstrap из чистой папки проекта:

```bash
cd /home/nekach/projects/ai-office-kernel
./bootstrap.sh
```

Если нужно сразу запустить Gemini OAuth и получить ссылку в Telegram:

```bash
./bootstrap.sh \
  --gemini-auth \
  --telegram-bot-token "123:abc" \
  --telegram-chat-id "123456789"
```

OAuth Google нельзя завершить без действия пользователя. По умолчанию setup только показывает ручную команду. Если передан `--gemini-auth`, скрипт запускает `gemini`, вытаскивает ссылку, отправляет ее в Telegram и ждет Enter после входа в браузере.

Интерактивная настройка:

```bash
ai-office-kernel setup
```

Установщик умеет:

- ставить Python-зависимости через `pip install -e .`;
- ставить Gemini CLI через `npm install -g @google/gemini-cli`;
- проверять или ставить Ollama;
- делать `ollama pull <model>`;
- принимать прямую Hugging Face GGUF-ссылку, скачивать файл и создавать Ollama-модель через `ollama create`;
- запускать Gemini CLI OAuth, печатать auth URL и отправлять его в Telegram-чат, если указан `TELEGRAM_BOT_TOKEN` и chat id;
- писать готовый `.env`.

Сейчас дефолтные модели:

```text
secretary/router: qwen3:8b
local coder: qwen2.5-coder:7b-instruct-q4_K_M
local QA: qwen3:8b
```

Та же настройка без установленного entrypoint:

```bash
PYTHONPATH=src python3 -m ai_office_kernel setup --auto --skip-gemini-auth
```

Ручная авторизация Gemini CLI:

```bash
NO_BROWSER=1 gemini
```

После входа проверь:

```bash
cd /tmp
gemini --prompt "Reply with exactly OK. Do not inspect files. Do not use tools." --output-format json --approval-mode default --skip-trust
```

Запуск Telegram-шлюза:

```bash
export TELEGRAM_BOT_TOKEN=...
ai-office-kernel telegram
```

Основной Telegram workflow:

```text
Просто пишешь обычное сообщение.
Секретарь уточняет, проверяет файлы/tools, собирает ТЗ и сам решает маршрут.
Если он хочет вызвать Gemini CLI или опасное действие, бот попросит подтверждение.
```

Fallback workflow с ручной активной задачей:

```text
/task Сделай парсер nginx-логов на Python
Любое сообщение с уточнениями задачи
/gemini
/model auto
/confirm_model
/run
/confirm
```

Для локального выполнения вместо `/gemini` используй `/local`. Если активной задачи нет, обычный текст идет агентному локальному секретарю.

Локальная проверка маршрутизации без запуска агентов:

```bash
ai-office-kernel doctor
ai-office-kernel route "@dev Напиши парсер логов на Python"
```

Разовый прогон через оркестратор:

```bash
ai-office-kernel ask "@dev Напиши парсер логов на Python"
```

## Telegram privacy mode

Если privacy mode у бота включен, Telegram обычно доставляет боту команды вида `/dev`, `/qa`, `/sec`, но не каждое обычное сообщение группы. Поэтому gateway поддерживает оба формата:

- `/dev Напиши парсер логов`
- `/qa Проверь этот фрагмент`
- `/sec Что дальше по плану?`
- `/task Сделай задачу`
- `@dev ...`, `@qa ...`, `@sec ...` при отключенном privacy mode или в личном чате

## Тесты

Тесты ядра не требуют Telegram, Ollama или Gemini CLI:

```bash
python3 -m unittest discover -s tests
```
