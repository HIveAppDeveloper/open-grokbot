"""Single-worker persisted job runner for the persistent backend host."""
import asyncio
import logging
import os
import time
import uuid
from contextlib import suppress
from datetime import datetime
from sqlalchemy import select, update, text
from app.db.session import AsyncSessionLocal, engine
from app.db.tenancy import current_tenant_id
from app.models.task import Task
from app.models.runtime import RuntimeCheck
from app.models.execution import TaskExecution
from app.services.execution import retry_or_interrupt, ExecutionSuperseded
from app.services.orchestrator import AgentOrchestrator
logger = logging.getLogger(__name__)
POLL_SECONDS = 2
PROCESS_BOOT_ID = uuid.uuid4().hex
PROCESS_STARTED_AT = datetime.utcnow().isoformat()+'Z'

async def run_worker(stop_event=None):
    stop_event = stop_event or asyncio.Event()
    # Hold a dedicated Postgres session lock across the loop. During rolling
    # deployments the next worker waits until the old one exits; it cannot
    # claim the same pending task or interrupt another live worker's work.
    if engine.dialect.name == "postgresql":
        async with engine.connect() as lock_connection:
            # PostgreSQL waits for the active owner to release the lock. The
            # standby no longer sends a query every two seconds, all day.
            acquired = asyncio.create_task(lock_connection.scalar(text('SELECT pg_advisory_lock(1763000274)')))
            stopped = asyncio.create_task(stop_event.wait())
            try:
                await asyncio.wait({acquired,stopped},return_when=asyncio.FIRST_COMPLETED)
                if stop_event.is_set():
                    acquired.cancel()
                    with suppress(asyncio.CancelledError):await acquired
                    return
                await acquired
                await lock_connection.commit()
            finally:
                stopped.cancel()
                with suppress(asyncio.CancelledError):await stopped
            owner_pid = await lock_connection.scalar(text("SELECT pg_backend_pid()"))
            await lock_connection.commit()
            from app.db.wakeup import CHANNEL
            wake = asyncio.Event()
            raw = await lock_connection.get_raw_connection()
            driver = raw.driver_connection
            def notify(*args):wake.set()
            await driver.add_listener(CHANNEL,notify)
            try:
                await _run_with_catalog(lock_connection, owner_pid, stop_event, wake)
            finally:
                with suppress(Exception):
                    await driver.remove_listener(CHANNEL,notify)
                    await lock_connection.execute(text("SELECT pg_advisory_unlock(1763000274)"))
                    await lock_connection.commit()
    else:
        await _run_with_catalog(stop_event=stop_event)

async def _run_with_catalog(lock_connection=None, owner_pid=None, stop_event=None, wake=None):
    from app.services.plugin_sync import run
    catalog_task=asyncio.create_task(run(stop_event, AsyncSessionLocal))
    try:
        await _run_worker_loop(lock_connection, owner_pid, stop_event, wake)
    finally:
        catalog_task.cancel()
        with suppress(asyncio.CancelledError):await catalog_task

async def heartbeat():
    from app.models.runtime import RuntimeHeartbeat
    from app.services.verification import record
    from app.services.runtime_identity import runtime_identity
    async with AsyncSessionLocal() as db:
        if db.bind.dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert
            now = datetime.utcnow()
            values = [{'name':'worker','updated_at':now}]
            statement = insert(RuntimeHeartbeat).values(values)
            await db.execute(statement.on_conflict_do_update(index_elements=['name'],set_={'updated_at':statement.excluded.updated_at}))
            statement = insert(RuntimeCheck).values(name='worker_release',status='running',detail={**runtime_identity(),'boot_id':PROCESS_BOOT_ID,'boot_started_at':PROCESS_STARTED_AT},updated_at=now)
            await db.execute(statement.on_conflict_do_update(index_elements=['name'],set_={'detail':statement.excluded.detail,'updated_at':now}))
            await db.commit()
            return
        item = await db.get(RuntimeHeartbeat, "worker")
        if item is None:
            item = RuntimeHeartbeat(name="worker", updated_at=datetime.utcnow())
            db.add(item)
        else: item.updated_at = datetime.utcnow()
        release = await db.get(RuntimeCheck, 'worker_release')
        if release is None:
            db.add(RuntimeCheck(name='worker_release', status='running', detail={**runtime_identity(), 'boot_id':PROCESS_BOOT_ID, 'boot_started_at':PROCESS_STARTED_AT}, updated_at=datetime.utcnow()))
        else:
            release.detail = {**runtime_identity(), 'boot_id':PROCESS_BOOT_ID, 'boot_started_at':PROCESS_STARTED_AT}
            release.updated_at = datetime.utcnow()
        await db.commit()

async def _run_worker_loop(lock_connection=None, owner_pid=None, stop_event=None, wake=None):
    stop_event = stop_event or asyncio.Event()
    if stop_event.is_set(): return
    last_heartbeat = 0
    async def pulse():
        nonlocal last_heartbeat
        if time.monotonic() - last_heartbeat < (30 if lock_connection is not None else 5): return
        if lock_connection is not None:
            pid = await lock_connection.scalar(text("SELECT pg_backend_pid()"))
            await lock_connection.commit()
            if pid != owner_pid: raise ExecutionSuperseded("Worker lost its PostgreSQL session lock")
        await heartbeat()
        last_heartbeat = time.monotonic()
    # Only the lock owner recovers work. Completed steps are replayed from SQL;
    # ambiguous external writes still require review.
    async with AsyncSessionLocal() as db:
        from app.models.bot import Bot
        await db.execute(update(Bot).where(Bot.status=='working').values(status='idle'))
        abandoned = (await db.execute(select(Task).where(Task.status == 'running'))).scalars().all()
        for task in abandoned:
            owner=current_tenant_id.set(task.tenant_id)
            try:
                await retry_or_interrupt(db, task, immediate=True)
                if task.status in {'failed','interrupted'}:
                    from app.services.workspace import notify_finished
                    await notify_finished(db,task)
                await db.flush()
            finally:current_tenant_id.reset(owner)
        await db.execute(update(Task).where(Task.status == "cancel_requested").values(status="cancelled", completed_at=datetime.utcnow(), result={"note":"Worker restarted after cancellation. Review any in-flight external actions."}))
        await db.commit()
    async def run_checks(force=False):
        from app.services.verification import verify_cloud
        check = asyncio.create_task(verify_cloud(force=force))
        try:
            while not check.done():
                await pulse()
                await asyncio.wait({check}, timeout=5)
            await check
        finally:
            if not check.done():
                check.cancel()
                with suppress(asyncio.CancelledError): await check
    async with AsyncSessionLocal() as db:
        previous = await db.get(RuntimeCheck, 'check_request')
        if previous and previous.status == 'running':
            previous.status = 'pending'
            await db.commit()
    from app.services.model_connections import recover_checks
    await recover_checks(AsyncSessionLocal)
    from app.services.prospecting import recover as recover_prospecting
    await recover_prospecting(AsyncSessionLocal)
    if os.getenv("GROKBOT_VERIFY_CLOUD") == "true":
        await run_checks()
    from app.db.wakeup import wait_for_work
    from app.services.computer import tick_commands
    from app.models.workspace import ComputerCommand
    async with AsyncSessionLocal() as db:
        await db.execute(update(ComputerCommand).where(ComputerCommand.status=='running').values(status='interrupted',payload='',result=None))
        await db.commit()
    last_maintenance = 0
    while not stop_event.is_set():
        await pulse()
        await tick_commands(AsyncSessionLocal)
        from app.services.prospecting import tick as tick_prospecting
        prospecting_work = await tick_prospecting(AsyncSessionLocal)
        maintenance_due = lock_connection is None or time.monotonic()-last_maintenance >= 60 or (wake is not None and wake.is_set())
        requested = False
        if maintenance_due:
          last_maintenance = time.monotonic()
          async with AsyncSessionLocal() as commands:
              request_check = await commands.get(RuntimeCheck, 'check_request')
              requested = request_check is not None and request_check.status == 'pending'
              if requested:
                  request_check.status = 'running'
                  await commands.commit()
          from app.services.model_connections import run_requested_check
          if not requested and await run_requested_check(AsyncSessionLocal, pulse):
              last_maintenance = 0
              continue
        if requested:
            from app.services.verification import save_check, safe_error
            try:
                await run_checks(force=True)
                await save_check('check_request','completed',{})
            except Exception as error:
                await save_check('check_request','failed',safe_error(error))
            continue
        from app.services.automation import tick_goals,tick_deliveries
        if maintenance_due:
            await tick_goals(AsyncSessionLocal)
            await tick_deliveries(AsyncSessionLocal)
            from app.services.workspace import tick_routines
            await tick_routines(AsyncSessionLocal)
        if stop_event.is_set(): break
        async with AsyncSessionLocal() as db:
            task = (await db.execute(select(Task).outerjoin(TaskExecution, TaskExecution.task_id == Task.id).where(
                Task.status == 'pending',
                (TaskExecution.next_attempt_at.is_(None)) | (TaskExecution.next_attempt_at <= datetime.utcnow()),
            ).order_by(Task.created_at).limit(1).with_for_update(of=Task, skip_locked=True))).scalar_one_or_none()
            if stop_event.is_set():
                await db.rollback()
                break
            if task is None:
                await db.commit()
                signaled = await wait_for_work(wake,stop_event,0 if prospecting_work else (30 if lock_connection is not None else POLL_SECONDS))
                if signaled:last_maintenance = 0
                continue
            tenant_token = current_tenant_id.set(getattr(task, "tenant_id", "legacy-owner"))
            try:
                state = await db.get(TaskExecution, task.id)
                if state is None:
                    state = TaskExecution(task_id=task.id, attempts=0, recoveries=0)
                    db.add(state)
                state.attempts += 1
                claimed_attempt = state.attempts
                state.next_attempt_at = None
                state.updated_at = datetime.utcnow()
                task.status = 'running'
                task.started_at = task.started_at or datetime.utcnow()
                await db.commit()
                request = (task.result or {}).get("request", {})
                execution = asyncio.create_task(AgentOrchestrator().process_message(task.conversation_id, task.description, attachments=request.get("attachments", []), options=request.get("options", {}), goal_id=request.get("goal_id"), task_id=task.id, incoming_message_id=request.get("incoming_message_id"), execution_attempt=state.attempts))
                try:
                    cancelled = False
                    while not execution.done():
                        await asyncio.wait({execution}, timeout=2)
                        if execution.done(): break
                        await pulse()
                        await tick_commands(AsyncSessionLocal)
                        async with AsyncSessionLocal() as monitor:
                            current = await monitor.get(Task, task.id)
                            if current and current.status == "cancel_requested":
                                execution.cancel()
                                with suppress(asyncio.CancelledError): await execution
                                cancelled = True
                                break
                    if cancelled:
                        task.status = "cancelled"
                        task.result = {**(task.result or {}), "note": "Stopped future steps. An in-flight external action may already have completed."}
                    else:
                        responses = await execution
                        task.status = response_status(responses)
                        task.result = {**(task.result or {}), "responses": responses}
                finally:
                    if not execution.done():
                        execution.cancel()
                        with suppress(asyncio.CancelledError): await execution
            except asyncio.CancelledError:
                raise
            except ExecutionSuperseded:
                raise
            except Exception:
                logger.exception("Job failed: %s", task.id)
                await db.rollback()
                await db.refresh(task)
                await retry_or_interrupt(db, task)
                if task.status in {'failed','interrupted'}:
                    from app.services.workspace import notify_finished
                    await notify_finished(db,task)
                await db.commit()
                current_tenant_id.reset(tenant_token)
                continue
            with db.no_autoflush:
                owner=(await db.execute(select(TaskExecution.attempts).where(TaskExecution.task_id==task.id).with_for_update())).scalar_one()
                if owner!=claimed_attempt:raise ExecutionSuperseded('A replacement owns the task result.')
            task.completed_at = datetime.utcnow()
            if request.get('goal_id'):
                from app.models.automation import Goal
                goal=await db.get(Goal,request['goal_id'])
                if goal and not goal.completion_requested:
                    public=[r for r in (task.result or {}).get('responses',[]) if not (r.get('metadata') or {}).get('internal') and r.get('sender_type')=='bot']
                    if public:goal.last_summary=public[-1].get('content','')[:2000]
            from app.services.automation import enqueue_delivery
            await enqueue_delivery(task.id, request.get("channel_id"),AsyncSessionLocal,db)
            from app.services.workspace import notify_finished
            await notify_finished(db,task)
            await db.commit()
            current_tenant_id.reset(tenant_token)


def response_status(responses):
    public = [r for r in responses if not (r.get('metadata') or {}).get('internal')]
    if not public or any(r.get('message_type') == 'status_update' for r in public):
        return 'failed'
    return 'completed'
