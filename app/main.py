from __future__ import annotations

import asyncio
import logging
import ssl
import sys
from pathlib import Path

import aiohttp
from maxapi import Bot, Dispatcher
from maxapi.client.default import DefaultConnectionProperties
from maxapi.exceptions.max import MaxConnection

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.bot import build_commands, create_router
from app.config import load_settings
from app.google_clients import GoogleRepository
from app.reminders import ReminderService
from app.storage import Storage
from app.webhook_server import serve_webhook


logger = logging.getLogger(__name__)
STARTUP_RETRY_DELAY_SECONDS = 5


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    connector_ssl: bool | ssl.SSLContext = True
    if not settings.max_ssl_verify:
        connector_ssl = False
    elif settings.max_ca_bundle is not None:
        connector_ssl = ssl.create_default_context(cafile=str(settings.max_ca_bundle))

    connector = aiohttp.TCPConnector(ssl=connector_ssl)
    default_connection = DefaultConnectionProperties(connector=connector)

    bot = Bot(settings.bot_token, default_connection=default_connection)
    # Different maxapi releases expose the API host differently.
    if hasattr(bot, "set_api_url"):
        bot.set_api_url("https://platform-api.max.ru")
    elif hasattr(bot, "api_url"):
        bot.api_url = "https://platform-api.max.ru"
    else:
        bot.API_URL = "https://platform-api.max.ru"

    # Older library releases still send access_token via query params.
    if hasattr(bot, "params"):
        bot.params = {}
    # Newer releases already keep Authorization header on bot.headers.
    if hasattr(bot, "headers"):
        bot.headers = {"Authorization": settings.bot_token}

    dispatcher = Dispatcher()

    storage = Storage(settings.db_path)
    repository = GoogleRepository(settings)
    reminders = ReminderService(storage, repository)

    dispatcher.include_routers(create_router(repository, storage, settings, reminders))
    await reminders.start(bot)

    while True:
        try:
            try:
                await bot.set_my_commands(*build_commands())
            except Exception:
                logger.exception("Failed to update MAX bot commands. Continuing without command sync.")

            if settings.delivery_mode == "webhook":
                if settings.webhook_public_url:
                    await bot.delete_webhook()
                    await bot.subscribe_webhook(
                        url=settings.webhook_public_url,
                        secret=settings.webhook_secret,
                    )
                    logger.info("MAX webhook subscribed: %s", settings.webhook_public_url)
                else:
                    logger.warning(
                        "MAX_WEBHOOK_PUBLIC_URL is empty. The bot server will start, "
                        "but MAX will not deliver webhook events until the subscription is created."
                    )

                await serve_webhook(
                    dispatcher=dispatcher,
                    bot=bot,
                    host=settings.webhook_host,
                    port=settings.webhook_port,
                    log_level=settings.log_level,
                    secret=settings.webhook_secret,
                    path=settings.webhook_path,
                )
                return

            try:
                await bot.delete_webhook()
            except Exception:
                logger.exception("Failed to remove MAX webhook before polling start")

            await dispatcher.start_polling(bot)
            return
        except MaxConnection:
            logger.exception(
                "MAX API is temporarily unavailable during startup. Retrying in %s seconds.",
                STARTUP_RETRY_DELAY_SECONDS,
            )
            await bot.close_session()
            await asyncio.sleep(STARTUP_RETRY_DELAY_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
