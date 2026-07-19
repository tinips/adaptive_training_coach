# Adaptive Training Coach (Ironman Coach)

A Telegram bot that delivers adaptive endurance training coaching.

## Requirements

- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Windows Setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Configuration

1. Create a bot with [@BotFather](https://t.me/BotFather) on Telegram and copy the token.
2. Copy the example environment file:

```powershell
Copy-Item .env.example .env
```

3. Edit `.env` and replace `your_telegram_bot_token` with your real token.

**Never commit `.env` to Git.**

## Run

```powershell
python -m app.main
```

Press `Ctrl+C` to stop the bot.

## Current MVP Scope

- `/start` command replies with a welcome message.
- Any regular text message is echoed back to the user.
- Configuration via `.env` file.
- Long polling, local development only.

## Planned Future Milestones

- Persistent onboarding
- Athlete profile
- Weekly training plan
- Completed activity tracking
- Adaptive replanning
- Strava integration
- PostgreSQL
- LLM integration
- RAG
