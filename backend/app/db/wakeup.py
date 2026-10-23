"""Wake the persistent worker on committed work, without polling an idle DB."""
import asyncio
from contextlib import suppress
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from app.config import settings

CHANNEL = 'grokbot_work_' + settings.DATABASE_SCHEMA


@event.listens_for(Session, 'after_flush')
def notify_work(session, context):
    if session.get_bind().dialect.name != 'postgresql':
        return
    from app.models.task import Task
    from app.models.prospecting import ProspectingRun
    from app.models.automation import Goal
    from app.models.plugin_sync import PluginSync
    from app.models.runtime import RuntimeCheck
    from app.models.workspace import Routine,ComputerCommand
    for item in list(session.new) + list(session.dirty):
        if ((isinstance(item, (Task, PluginSync, RuntimeCheck, ComputerCommand, ProspectingRun)) and item.status == 'pending') or
                (isinstance(item, Goal) and item.status == 'active') or (isinstance(item,Routine) and item.enabled)):
            # PostgreSQL delivers this only after a successful commit. No user
            # data, IDs or credentials enter the notification payload.
            session.connection().execute(text('SELECT pg_notify(:channel, :payload)'), {'channel':CHANNEL,'payload':''})
            break


async def wait_for_work(wake, stop, timeout):
    tasks = [asyncio.create_task(stop.wait())]
    if wake is not None:
        tasks.append(asyncio.create_task(wake.wait()))
    try:
        await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        signaled = wake is not None and wake.is_set()
        if wake is not None:wake.clear()
        return signaled
    finally:
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):await task
