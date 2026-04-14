import asyncio
import json
import logging

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging
from app.repositories.news_repo import NewsRepository
from app.repositories.operator_state_repo import OperatorStateRepository
from app.repositories.trade_repo import TradeRepository
from app.services.monitor import MonitorService


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    
    async with AsyncSessionLocal() as session:
        monitor = MonitorService(
            settings=settings,
            operator_state_repository=OperatorStateRepository(session),
            news_repository=NewsRepository(session),
            trade_repository=TradeRepository(session),
        )
        
        report = await monitor.run_full_verification()
        
        # Color coding for CLI
        colors = {
            "ok": "\033[92m",  # Green
            "warning": "\033[93m",  # Yellow
            "error": "\033[91m",  # Red
            "reset": "\033[0m"
        }
        
        print("\n" + "="*50)
        print(f" SYSTEM VERIFICATION REPORT: {colors[report.overall_status]}{report.overall_status.upper()}{colors['reset']} ")
        print("="*50)
        
        layers = [
            ("A. Server Is Alive", report.layer_a_server),
            ("B. Pipeline Is Alive", report.layer_b_pipeline),
            ("C. Data Is Saved", report.layer_c_data),
            ("D. Trades Automation", report.layer_d_automation),
        ]
        
        for name, layer in layers:
             print(f"{name}: {colors[layer.status]}{layer.status.upper()}{colors['reset']}")
             print(f"  Message: {layer.message}")
             if layer.details:
                 print(f"  Details: {json.dumps(layer.details, indent=4)}")
             print("-" * 30)
             
        print(f"Generated at: {report.generated_at.isoformat()}")


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
