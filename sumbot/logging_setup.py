import logging
import os


def configure_logging() -> logging.Logger:
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = logging.getLevelName(log_level_name)
    invalid_log_level = not isinstance(log_level, int)
    if invalid_log_level:
        log_level = logging.INFO

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    logger = logging.getLogger("SumBot")
    logger.setLevel(log_level)

    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    if invalid_log_level:
        logger.warning("Unknown LOG_LEVEL=%s, using INFO.", log_level_name)

    logger.info("Logging configured (level=%s)", logging.getLevelName(logger.level))
    return logger
