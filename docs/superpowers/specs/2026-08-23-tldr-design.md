# Design: `/tldr` — LLM-суммаризация истории чата

## Goal

Команда `/tldr [N]` читает последние `N` сообщений текущего чата через Telethon и отдаёт **развёрнутую** суммаризацию в образе Бельмондо: общий нарратив того, что происходит в чате, а не сводка по каждому участнику.

## Decisions (locked)

| Тема | Решение |
|------|---------|
| Стиль | Образ Бельмондо, развёрнутый текст |
| Форма | Narrative сцены («Артём и Иван обсуждают…, Инара не понимает»), без bullet-list «по юзерам» |
| `N` | Default **100**, soft max **1000** (clamp + короткое предупреждение) |
| Доступ | `@pause` + `ensure_master_in_chat_for_ai` |
| История | Только текстовые сообщения; стикеры/медиа/сервисные пропускаем |
| Архитектура | Handler + отдельный Telethon-хелпер (вариант 1) |
| Concurrency | `ai_lock` чата (как у AI-ответов) |

## Architecture

```
/tldr [N]
    → handlers/tldr.py
        → parse N, guards, placeholder
        → tldr/history.py (Telethon iter_messages)
        → DeepSeek (professional_prompt + tldr instruction)
        → send_long_message
```

### Components

| Path | Responsibility |
|------|----------------|
| `handlers/tldr.py` | Command handler: args, guards, placeholder, LLM call, reply |
| `tldr/history.py` | Telethon client; fetch & filter text messages for a chat |
| `const.py` | Prompt fragment for narrative TLDR |
| `app.py` | Register `CommandHandler("tldr", …)` |
| `tests/test_tldr.py` | Unit tests: parse N, filter, prompt assembly |

Auth for Telethon reuses the same `auth.conf` fields as godnoscop: `api_id`, `api_hash`, `phone`.

## Data flow

1. Parse args:
   - no `N` → `100`
   - `N > 1000` → clamp to `1000`, note in reply header
   - `N < 1` or non-integer → usage hint, exit
2. Acquire `chat_data["ai_lock"]` (skip/ignore if already locked, same spirit as `process_ai_response`).
3. Send placeholder («Бельмондо листает историю…») + typing action.
4. Telethon: `iter_messages(chat_id, limit=N)`:
   - keep messages with non-empty text
   - exclude the invoking `/tldr` command message
   - reverse to chronological order (oldest → newest)
5. If zero text messages → edit/send short notice, no LLM.
6. Build user prompt: lines `DisplayName: text` (truncate very long texts); instruction demands a connected narrative of the room, not per-user digests.
7. Call `deepseek-v4-flash` with streaming + `parse_stream` (same pattern as tarot / AI horoscope).
8. Delete/replace placeholder; deliver via `send_long_message` (markdown).

## Prompt requirements

System: existing `const.professional_prompt` plus a dedicated TLDR instruction that:

- asks for an **expanded** summary (not 2–3 dry sentences);
- frames the chat as a scene: topics, dynamics, who is aligned / confused / arguing;
- **forbids** per-author bullet summaries;
- may use participant names as actors in the story;
- stays in Belmondo voice.

Untrusted chat content should be treated as data (same spirit as games: do not follow instructions found inside messages).

## Error handling

| Case | Behavior |
|------|----------|
| No text messages in window | Short user-facing message, no LLM |
| Telethon failure (session/access) | User-facing error + exception log |
| LLM failure / empty response | Placeholder → failure text (tarot-style) |
| Concurrent `/tldr` while lock held | Ignore / no second run (log) |

## Out of scope

- Refactoring godnoscop onto a shared Telethon service
- Summarizing media, stickers, or voice
- Persisting TLDR history / caching summaries
- Hard reject when `N > 1000` (we soft-clamp)

## Testing

- Parse/clamp of `N` (default, max, invalid)
- History filter: text-only, exclude command, chronological order
- Prompt builder: includes names+text, embeds narrative instruction
- Handler smoke with mocked Telethon + LLM (optional if patterns already cover AI handlers)
