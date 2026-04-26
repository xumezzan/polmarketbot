import asyncio

from app.services.backtest_runner import _main


if __name__ == "__main__":
    asyncio.run(_main())
