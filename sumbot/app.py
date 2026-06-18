import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

import config
from sumbot.daily_digest import run_daily_digest_scheduler
from sumbot.logging_setup import configure_logging
from sumbot.metrics import start_metrics_server
from sumbot.services import create_services
from sumbot.telegram_handlers.registry import register_handlers
from sumbot.tracing import configure_tracing, shutdown_tracing

logger = logging.getLogger("SumBot.app")


async def run_bot() -> None:
    configure_logging()
    configure_tracing(
        enabled=config.TRACING_ENABLED,
        service_name=config.TRACING_SERVICE_NAME,
        endpoint=config.TRACING_OTLP_ENDPOINT,
        sample_ratio=config.TRACING_SAMPLE_RATIO,
    )
    if config.METRICS_ENABLED:
        start_metrics_server(config.METRICS_HOST, config.METRICS_PORT)
        logger.info("Metrics endpoint started at %s:%s", config.METRICS_HOST, config.METRICS_PORT)
    else:
        logger.info("Metrics endpoint is disabled.")
    logger.info(
        "Booting SumBot (redis_host=%s, db_enabled=%s, openrouter_models=%s, deepseek_models=%s)",
        config.REDIS_HOST,
        bool(config.db_url),
        ",".join(config.OPENROUTER_MODELS),
        ",".join(config.DEEPSEEK_MODELS),
    )
    if not config.TG_TOKEN:
        logger.error("TG_TOKEN is empty; Telegram bot cannot start without it.")

    bot = Bot(token=config.TG_TOKEN)
    dispatcher = Dispatcher()
    services = create_services()
    register_handlers(dispatcher, services)
    logger.info("Handlers registered.")

    await bot.set_my_commands(
        [
            BotCommand(command="summary", description="Пересказать чат (можно указать число сообщений)"),
            BotCommand(command="digest", description="Настроить ежедневный дайджест (для администраторов)"),
            BotCommand(command="prompt", description="Настройка стиля пересказов"),
        ]
    )
    logger.info("Bot commands configured.")

    digest_task = asyncio.create_task(run_daily_digest_scheduler(bot, services))
    logger.info("Starting polling with update types: %s", dispatcher.resolve_used_update_types())
    try:
        await dispatcher.start_polling(bot)
    except Exception:
        logger.exception("Polling stopped because of an unexpected error.")
        raise
    finally:
        logger.info("Stopping bot services.")
        digest_task.cancel()
        with suppress(asyncio.CancelledError):
            await digest_task
        await services.close()
        await bot.session.close()
        shutdown_tracing()
        logger.info("Bot session closed.")


def main() -> None:
    asyncio.run(run_bot())
