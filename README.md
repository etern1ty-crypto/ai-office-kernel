<img src="https://capsule-render.vercel.app/api?type=waving&color=0:1a1b27,50:9B59B6,100:1a1b27&height=200&section=header&text=AI-Office%20Kernel&fontSize=45&fontColor=FFFFFF&fontAlignY=35&desc=Telegram-Driven%20Multi-Agent%20AI%20Office%20Framework&descSize=16&descColor=E8D5F5&descAlignY=55&animation=fadeIn" width="100%"/>

<div align="center">

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-3670A0?style=flat-square&logo=python&logoColor=ffdd54)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Ollama](https://img.shields.io/badge/ollama-local_LLM-000000?style=flat-square&logo=ollama&logoColor=white)](https://ollama.com)
[![Gemini CLI](https://img.shields.io/badge/gemini-CLI_escalation-4285F4?style=flat-square&logo=google&logoColor=white)]()
[![Telegram](https://img.shields.io/badge/telegram-bot_gateway-2CA5E0?style=flat-square&logo=telegram&logoColor=white)]()

**🇷🇺 [Русский](#-описание) · 🇬🇧 [English](#-overview)**

</div>

---

## 🇬🇧 Overview

**AI-Office Kernel** is an MVP framework for a "Virtual AI Office": a single Telegram bot acts as the gateway, a local secretary agent on Ollama handles conversations, invokes backend tools, and escalates heavy development tasks to locally-installed Gemini CLI.

> The project does not bypass auth or extract tokens. `GeminiCLIAdapter` only automates the CLI command that the user has already installed and authorized.

### How It Works

```
You send a message
        ↓
Local secretary understands the task
        ↓
Invokes backend-tools if needed
        ↓
Safe/medium tools execute automatically
        ↓
Gemini CLI escalation waits for confirmation
        ↓
Secretary returns a human-readable answer
```

### Components

| Component | Role |
|:---|:---|
| `TelegramGateway` | Single entry point for group/private chats |
| `SecretaryAgentLoop` | Agentic loop: answer → call tools → get results → continue |
| `ToolRuntime` | Backend tools: files, grep, git, shell, URL fetch, web search, local coder, Gemini |
| `Router` | Fast role selection via `@dev`, `@qa`, `@sec` triggers |
| `AgentOrchestrator` | Context assembly, Ollama/Gemini routing, model lifecycle |
| `GeminiCLIAdapter` | Isolated layer for `gemini --prompt ... --output-format json` |
| `OllamaClient` | Local generation + forced unload via `keep_alive=0` |

### Quick Start

```bash
# Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env

# Or use the bootstrap script
./bootstrap.sh

# Interactive setup (installs deps, Ollama models, Gemini CLI)
ai-office-kernel setup

# Health check
ai-office-kernel doctor

# Run Telegram gateway
export TELEGRAM_BOT_TOKEN=...
ai-office-kernel telegram

# CLI test without Telegram
ai-office-kernel agent "Check what files are in the current workspace"

# HTTP API
ai-office-kernel api --host 127.0.0.1 --port 8787
```

### Default Models

```yaml
secretary/router: qwen3:8b
local coder: qwen2.5-coder:7b-instruct-q4_K_M
local QA: qwen3:8b
fallback: llama3.1:8b-instruct-q4_K_M
```

### Testing

```bash
# Core tests (no Telegram/Ollama/Gemini required)
python3 -m unittest discover -s tests
```

### Tech Stack

![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)
![Ollama](https://img.shields.io/badge/ollama-000000?style=for-the-badge&logo=ollama&logoColor=white)
![Telegram](https://img.shields.io/badge/telegram-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)

---

## 🇷🇺 Описание

**AI-Office Kernel** — MVP-каркас «Виртуального ИИ-Офиса»: один Telegram-бот принимает сообщения, локальный секретарь на Ollama ведёт диалог, вызывает backend-tools и эскалирует тяжёлую разработку в локально установленный Gemini CLI.

### Как Это Работает

```
Обычное сообщение в чат
        ↓
Локальный секретарь понимает задачу
        ↓
Вызывает backend-tools при необходимости
        ↓
Safe/medium tools выполняются автоматически
        ↓
Gemini CLI escalation ждёт подтверждения
        ↓
Секретарь возвращает человеческий ответ
```

### Компоненты

| Компонент | Роль |
|:---|:---|
| `TelegramGateway` | Единая точка входа для группового/личного чата |
| `SecretaryAgentLoop` | Агентный цикл: ответ → вызов tools → результат → продолжение |
| `ToolRuntime` | Файлы, grep, git, shell, URL fetch, web search, local coder, Gemini |
| `Router` | Быстрый выбор роли: `@dev`, `@qa`, `@sec` |
| `AgentOrchestrator` | Сборка контекста, маршрутизация Ollama/Gemini |
| `GeminiCLIAdapter` | `gemini --prompt ... --output-format json` |
| `OllamaClient` | Локальная генерация + выгрузка через `keep_alive=0` |

### Быстрый Старт

```bash
# Установка
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env

# Или автоматический bootstrap
./bootstrap.sh

# Интерактивная настройка
ai-office-kernel setup

# Проверка окружения
ai-office-kernel doctor

# Запуск Telegram
export TELEGRAM_BOT_TOKEN=...
ai-office-kernel telegram

# CLI тест без Telegram
ai-office-kernel agent "Проверь файлы в workspace"

# HTTP API
ai-office-kernel api --host 127.0.0.1 --port 8787
```

### Telegram Команды

```
/dev  — задача на разработку
/qa   — ревью и проверка
/sec  — секретарь
/task — начать активную задачу
/tools /workspace /ls /read /scan — debug-инструменты
/status — heartbeat текущей задачи
```

### Тесты

```bash
# Тесты ядра (без Telegram/Ollama/Gemini)
python3 -m unittest discover -s tests
```

### Документация

- [Интеграция Gemini CLI](docs/gemini-cli-integration.md)
- [Runbook запуска](docs/runbook.md)

---

<div align="center">

### License

MIT — see [LICENSE](LICENSE) for details.

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:1a1b27,50:9B59B6,100:1a1b27&height=80&section=footer" width="100%"/>

</div>
