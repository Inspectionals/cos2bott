# COS2 Discord AI

Answers Case Opening Simulator 2 questions from the extracted catalog (733 cases + odds + battle modes).

## Railway

1. New project → Deploy from GitHub (or empty + upload these files).
2. Variables:
   - `DISCORD_TOKEN` (required)
   - `OPENAI_API_KEY` (optional, for /ask)
   - `OPENAI_BASE_URL` (optional, OpenAI-compatible)
   - `OPENAI_MODEL` (optional, default gpt-4o-mini)
3. Start command: `python bot.py`
4. In Discord Dev Portal enable Message Content Intent if you want chat questions without slash commands.

## Commands

- `/case name:` — items, prices, odds, EV
- `/rtp [limit]` — best expected value vs case cost
- `/search item:` — which cases contain an item
- `/battle mode: modifier: budget:` — suggested case lineup
- `/ask question:` — AI answer using the catalog (needs API key)
