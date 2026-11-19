"""Reminder delivery worker — placeholder for next iteration."""

import asyncio
import logging

from classmate.core.logging import configure_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    configure_logging()
    logger.info("ClassMate 2 reminder worker started (idle — implementation coming next)")
    # Keep process alive
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
