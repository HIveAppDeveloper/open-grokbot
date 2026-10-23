from fastapi import APIRouter
from .bots import router as bots_router
from .conversations import router as conversations_router
from .messages import router as messages_router

from .plugins import router as plugins_router

from .teams import router as teams_router

from .runtime import router as runtime_router

from .automation import router as automation_router

from .context import router as context_router

from .device import router as device_router

from .accounts import router as accounts_router
from .auth import router as auth_router

router = APIRouter()
from .atlas_engine import router as atlas_engine_router
router.include_router(atlas_engine_router, prefix="/integrations/atlas/v1", tags=["atlas-engine"])
router.include_router(accounts_router,prefix="/accounts",tags=["accounts"])
router.include_router(auth_router,prefix="/auth",tags=["auth"])
router.include_router(device_router,prefix="/device",tags=["device"])
router.include_router(context_router,prefix="/conversations",tags=["context"])
router.include_router(automation_router,tags=["automation"])
router.include_router(runtime_router, prefix="/runtime", tags=["runtime"])
router.include_router(teams_router, prefix="/teams", tags=["teams"])
from .plugin_catalog import router as plugin_catalog_router
router.include_router(plugin_catalog_router,prefix='/plugins',tags=['plugins'])
router.include_router(plugins_router, prefix="/plugins", tags=["plugins"])
router.include_router(bots_router, prefix="/bots", tags=["bots"])
router.include_router(conversations_router, prefix="/conversations", tags=["conversations"])
router.include_router(messages_router, prefix="/conversations", tags=["messages"])
from .workspace import router as workspace_router
router.include_router(workspace_router,prefix='/workspace',tags=['workspace'])

from .computer import router as computer_router
router.include_router(computer_router,prefix='/computer',tags=['computer'])

from .local_device import router as local_device_router
router.include_router(local_device_router,prefix='/local-device',tags=['local-device'])

from .prospecting import router as prospecting_router
router.include_router(prospecting_router, prefix='/prospecting', tags=['prospecting'])
