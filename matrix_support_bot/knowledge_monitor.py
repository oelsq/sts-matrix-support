"""Periodic offline integrity/review-age inventory; never downloads or activates law."""
import asyncio
import logging
from evaluation_core import private_json
from knowledge_updates import inventory

async def monitor(root,output,*,interval=21600):
    while True:
        try:
            report=await asyncio.to_thread(inventory,root)
            await asyncio.to_thread(private_json,output,report)
            logging.getLogger('support_bot.knowledge').info('Knowledge inventory: %s',report['counts'])
        except Exception as exc:
            logging.getLogger('support_bot.knowledge').error('Knowledge inventory failed (%s)',type(exc).__name__)
        await asyncio.sleep(interval)
