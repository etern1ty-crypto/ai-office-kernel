# AI-Office Kernel Runbook

## 1. First install

```bash
cd /home/nekach/projects/ai-office-kernel
./bootstrap.sh
```

If Gemini CLI is already installed and authenticated:

```bash
./bootstrap.sh --skip-gemini-install --skip-gemini-auth
```

## 2. Load environment

Every new terminal needs:

```bash
cd /home/nekach/projects/ai-office-kernel
source .venv/bin/activate
set -a
source .env
set +a
```

## 3. Gemini CLI auth

Manual auth:

```bash
NO_BROWSER=1 gemini
```

Open the printed URL, finish Google login, return to the terminal.

Safe smoke test:

```bash
cd /tmp
gemini -p "Reply with exactly OK. Do not inspect files. Do not use tools." -o json --approval-mode=default --skip-trust
```

The installer writes `.gemini/settings.json` for service mode in the project root
and workspace root. It disables Gemini subagents and shell execution for bot runs,
which prevents `LocalAgentExecutor` and `run_shell_command` errors from leaking
into Telegram.

## 4. Health check

```bash
cd /home/nekach/projects/ai-office-kernel
ai-office-kernel doctor
```

Real Gemini request:

```bash
ai-office-kernel doctor --gemini-smoke
```

## 5. Local checks

```bash
ollama list
ollama pull qwen3:8b
ollama pull qwen2.5-coder:7b-instruct-q4_K_M
ai-office-kernel route "@dev Напиши парсер логов"
ai-office-kernel ask "@sec Привет, локальный агент работает?"
ai-office-kernel agent "Проверь README и скажи, что это за проект"
ai-office-kernel ask "@dev Ответь одним предложением: Gemini CLI работает?"
```

## 6. Telegram

`.env` must contain:

```bash
TELEGRAM_BOT_TOKEN=...
AI_OFFICE_ALLOWED_CHAT_IDS=-100...
```

Run:

```bash
ai-office-kernel telegram
```

Main group behavior:

```text
Пиши обычным текстом. Бот отдаст сообщение локальному секретарю.
Секретарь может сам вызвать safe/medium tools внутри workspace.
Если нужно Gemini CLI escalation или опасное действие, он попросит подтверждение.
Подтверждение: ответь "да" или используй /confirm_agent.
Отмена: ответь "нет" или используй /cancel_agent.
```

Direct role/debug commands:

```text
/sec что запланировано?
/dev напиши парсер логов
/qa проверь этот код
```

Task workflow:

```text
/task Сделай парсер nginx-логов на Python
Обычное сообщение с уточнениями
/gemini
/model auto
/confirm_model
/run
/confirm
```

Use `/local` instead of `/gemini` to run the developer role through the local
Ollama coder model. The bot always asks for confirmation before changing the
Gemini CLI model and before running an active task.

The `/task` workflow is now a fallback/manual mode. The primary mode is:

```text
Ты: Сделай простой сайт-портфолио GitHub для etern1ty-crypto
Secretary: уточняет стек/дизайн/API, читает workspace при необходимости
Secretary: просит подтверждение на Gemini CLI, если задача крупная
Ты: да
Secretary: запускает escalation и возвращает итог
```

Prompt customization:

```bash
AI_OFFICE_PROMPT_DIR=/home/nekach/projects/ai-office-kernel/prompts
```

Edit:

```text
prompts/secretary.md
prompts/developer.md
prompts/qa.md
```

The secretary prompt is for the local Ollama manager. The developer prompt is
for Gemini CLI or the local coder role, depending on the chosen backend.

Long-running jobs:

```bash
AI_OFFICE_CLI_TIMEOUT_SECONDS=1200
AI_OFFICE_PROGRESS_FIRST_SECONDS=45
AI_OFFICE_PROGRESS_INTERVAL_SECONDS=60
```

During a long `/confirm` run the bot sends heartbeat messages and `/status`
shows elapsed time, backend, and model. This is process status, not hidden model
reasoning.

Local tools from Telegram:

```text
/tools
/workspace
/pwd
/ls
/read etern1ty-portfolio/README.md
/scan etern1ty-portfolio
/cmd --cwd etern1ty-portfolio npm install
/confirm_tool
/bg --cwd etern1ty-portfolio npm run dev -- --host 0.0.0.0
/confirm_tool
/procs
/stop 1
```

Manual `/cmd` commands require `/confirm_tool`. The agentic secretary can
execute safe/medium tools automatically, but they are still scoped to
`AI_OFFICE_WORKSPACE_ROOT`. Gemini CLI escalation and dangerous commands require
`/confirm_agent` or a plain "да" reply.

## 7. HTTP API

Run:

```bash
ai-office-kernel api --host 127.0.0.1 --port 8787
```

Ask:

```bash
curl -s http://127.0.0.1:8787/chat \
  -H 'Content-Type: application/json' \
  -d '{"chat_id":1,"text":"Проверь README и кратко объясни проект"}'
```

Confirm pending action:

```bash
curl -s http://127.0.0.1:8787/confirm \
  -H 'Content-Type: application/json' \
  -d '{"chat_id":1}'
```

## 8. Backend modes

Gemini developer and QA:

```bash
AI_OFFICE_DEVELOPER_BACKEND=gemini
AI_OFFICE_QA_BACKEND=gemini
AI_OFFICE_QA_ENABLED=true
```

Local developer:

```bash
AI_OFFICE_DEVELOPER_BACKEND=local
AI_OFFICE_LOCAL_CODER_MODEL=qwen2.5-coder:7b-instruct-q4_K_M
```

Local QA:

```bash
AI_OFFICE_QA_BACKEND=local
AI_OFFICE_LOCAL_QA_MODEL=llama3.1:8b-instruct-q4_K_M
```

After editing `.env`, reload:

```bash
set -a
source .env
set +a
```
