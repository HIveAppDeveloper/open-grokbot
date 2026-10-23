"""Authenticated prospecting API. No mailbox reads, provider writes, or sending."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.tenancy import current_tenant_id
from app.models.prospecting import ProspectingRun
from app.services.prospecting import event, gmail_state, snapshot, TERMINAL

router = APIRouter()


def authenticated():
    tenant = current_tenant_id.get()
    if not tenant: raise HTTPException(401,'Sign in to use prospecting.')
    return tenant


class RunInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    market: str = Field(min_length=2,max_length=300)
    product: str = Field(min_length=2,max_length=2000)
    limit: int = Field(default=5,ge=1,le=20)


class DraftInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    sender_name: str = Field(default='Your team',min_length=1,max_length=100)


async def owned(db,run_id,tenant):
    row=(await db.execute(select(ProspectingRun).where(ProspectingRun.id==run_id,ProspectingRun.tenant_id==tenant))).scalar_one_or_none()
    if row is None: raise HTTPException(404,'Prospecting run not found.')
    return row


@router.post('/runs',status_code=202)
async def create_run(body:RunInput, tenant=Depends(authenticated), db:AsyncSession=Depends(get_db)):
    active=(await db.execute(select(ProspectingRun.id).where(ProspectingRun.tenant_id==tenant,ProspectingRun.status.in_(['pending','running','cancel_requested'])).limit(3))).scalars().all()
    if len(active)>=3: raise HTTPException(429,'Finish or cancel an existing run before starting another.')
    row=ProspectingRun(market=body.market,product=body.product,limit=body.limit)
    db.add(row); event(row,'queued',source='Wikipedia API'); await db.commit(); await db.refresh(row)
    return snapshot(row,await gmail_state(db))


@router.get('/runs')
async def list_runs(tenant=Depends(authenticated),db:AsyncSession=Depends(get_db)):
    rows=(await db.execute(select(ProspectingRun).where(ProspectingRun.tenant_id==tenant).order_by(ProspectingRun.created_at.desc()).limit(50))).scalars().all()
    gmail=await gmail_state(db)
    return [snapshot(row,gmail) for row in rows]


@router.get('/runs/{run_id}')
async def get_run(run_id:str,tenant=Depends(authenticated),db:AsyncSession=Depends(get_db)):
    return snapshot(await owned(db,run_id,tenant),await gmail_state(db))


@router.post('/runs/{run_id}/cancel')
async def cancel_run(run_id:str,tenant=Depends(authenticated),db:AsyncSession=Depends(get_db)):
    row=await owned(db,run_id,tenant)
    if row.status not in TERMINAL:
        row.status='cancel_requested'; event(row,'cancel_requested'); await db.commit()
    return snapshot(row,await gmail_state(db))


@router.post('/runs/{run_id}/results/{result_id}/draft')
async def prepare_draft(run_id:str,result_id:str,body:DraftInput,tenant=Depends(authenticated),db:AsyncSession=Depends(get_db)):
    row=await owned(db,run_id,tenant)
    if row.status not in TERMINAL: raise HTTPException(409,'Wait for research to finish before preparing a draft.')
    item=next((r for r in row.results if r['id']==result_id),None)
    if item is None: raise HTTPException(404,'Prospect not found.')
    draft={'subject':'Introduction: '+row.product[:100],
           'body':f"Hello {item['name']} team,\n\nYour public Wikipedia description states: \"{item['observed']['description'][:240]}\"\n\nI am reaching out to introduce {row.product}. Would a conversation about whether it could be relevant to your team be useful?\n\nBest,\n{body.sender_name}",
           'status':'local_draft','created_at':datetime.utcnow().isoformat()+'Z',
           'recipient':None,'review_required':True,'source_urls':[item['source_url']]}
    row.results=[{**r,'draft':draft} if r['id']==result_id else r for r in row.results]
    event(row,'draft_prepared',result_id=result_id,storage='local',sent=False)
    await db.commit()
    return draft


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    url: str = Field(min_length=8,max_length=2048)


@router.post('/profile')
async def company_profile(body:ProfileInput,tenant=Depends(authenticated)):
    from app.services.prospecting_profile import profile, ProfileError
    try:
        return await profile(body.url)
    except ProfileError as error:
        raise HTTPException(422,str(error)) from None
    except Exception:
        raise HTTPException(502,'The public company profile could not be read. Enter the offer manually.') from None
