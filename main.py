#!/usr/bin/env python3
"""PostBot - Telegram Scheduler Bot

A feature-rich bot for scheduling posts to Telegram channels and groups.

Features:
- Scheduled posts (one-time, daily, weekly, monthly)
- Post templates
- Multi-chat support
- Web panel for management
- Import/Export posts
- Participant tracking
- Error notifications

Environment variables:
- BOT_TOKEN: Telegram bot token (required)
- REDIS_URL: Redis URL for FSM storage (optional)
- WEB_PORT: Port for web panel (optional)
- WEB_HOST: Host for web panel links (default: localhost)
"""
import os
import sys
import asyncio
import logging
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


async def main():
    load_dotenv()
    
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not found in environment variables")
        sys.exit(1)
    
    from postbot.bot import SchedulerBot
    
    bot = SchedulerBot(token)
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
