# Gemini CLI integration notes

Документ фиксирует, как проект использует `google-gemini/gemini-cli` как backend для стороннего сервиса.

## Вывод из исходника Gemini CLI

Проверенные точки интеграции:

- `packages/cli/src/config/config.ts` определяет реальные флаги: `--prompt`, `--model`, `--approval-mode`, `--skip-trust`, `--sandbox`, `--allowed-tools`, `--include-directories`, `--resume`, `--output-format`.
- В том же файле `--prompt` принудительно включает non-interactive/headless режим.
- `--approval-mode` принимает `default`, `auto_edit`, `yolo`, `plan`. Старый `--yolo` есть, но лучше использовать `--approval-mode=yolo`.
- Если папка не trusted, Gemini CLI сбрасывает approval mode в `default`; для сервисного запуска нужен `--skip-trust` или заранее доверенная рабочая папка.
- В non-interactive режиме Gemini CLI блокирует `ask_user`, потому что в headless-процессе нет человека, которому можно показать prompt.
- `packages/cli/src/nonInteractiveCli.ts` пишет `init`, `message`, `tool_use`, `tool_result`, `error`, `result` при `--output-format stream-json`.
- `packages/core/src/output/types.ts` описывает JSON/JSONL-схему: `response`, `stats`, `session_id`, token stats и stream events.
- `packages/core/src/output/stream-json-formatter.ts` агрегирует токены в поля `total_tokens`, `input_tokens`, `output_tokens`, `cached`, `tool_calls`, `models`.

## Рабочая модель для Telegram/HTTP сервиса

Не надо держать TTY и отвечать `y` на вопросы. Правильный сервисный контур:

```bash
gemini \
  --prompt "task text" \
  --output-format json \
  --approval-mode auto_edit \
  --skip-trust \
  --model auto
```

Для полного авто-принятия инструментов:

```bash
gemini \
  --prompt "task text" \
  --output-format stream-json \
  --approval-mode yolo \
  --skip-trust
```

`auto_edit` безопаснее для повседневного режима: Gemini сам принимает edit/write tools, но опасные действия не должны молча проходить. `yolo` стоит включать только в одноразовом sandbox/worktree.

Для проверки авторизации не запускай smoke-test из рабочего репозитория с агентными правами. Запускай из `/tmp` и явно запрети tools:

```bash
cd /tmp
gemini --prompt "Reply with exactly OK. Do not inspect files. Do not use tools." --output-format json --approval-mode default --skip-trust
```

## Bootstrap авторизации

`ai-office-kernel setup` запускает интерактивный `gemini` с `NO_BROWSER=1`, выбирает Login with Google при обнаружении auth prompt, парсит URL из вывода и может отправить его в Telegram через Bot API. После входа в браузере установщик ждет Enter и закрывает Gemini CLI через `/quit`.

Это не переносит, не читает и не сохраняет OAuth-токены. Кеш credentials остается во внутренних файлах Gemini CLI, как при ручном запуске `gemini`.

## Токены и usage

Для `json` адаптер читает:

```json
{
  "session_id": "...",
  "response": "...",
  "stats": {
    "models": {
      "gemini-...": {
        "tokens": {
          "prompt": 0,
          "candidates": 0,
          "total": 0,
          "cached": 0
        }
      }
    },
    "tools": {
      "totalCalls": 0
    }
  }
}
```

Для `stream-json` итоговый event содержит:

```json
{
  "type": "result",
  "status": "success",
  "stats": {
    "total_tokens": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "cached": 0,
    "tool_calls": 0,
    "models": {}
  }
}
```

`CLIResult.usage_summary()` нормализует оба формата в короткую строку вида:

```text
tokens=1234 in=900 out=334 cached=100 tools=2
```

## Сессии

Gemini CLI не является постоянным stdin-сервером в обычном headless-режиме. Практичная схема для стороннего сервиса:

1. На каждую задачу запускать отдельный процесс `gemini --prompt ...`.
2. Сохранять `session_id` из JSON/stream init.
3. Для продолжения диалога запускать новый процесс с `--resume <session-id>` или `--resume latest`.

В проекте это управляется переменной:

```bash
AI_OFFICE_GEMINI_RESUME=latest
```

## ACP

У Gemini CLI есть `--acp`, но для этого MVP он не используется. Причина прагматичная: обычный headless `json`/`stream-json` уже дает стабильный интерфейс для Telegram-сервиса, а ACP в текущей экосистеме еще экспериментален и требует отдельного JSON-RPC транспорта.
