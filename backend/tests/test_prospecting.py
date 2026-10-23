"""Synthetic public-source fixtures through real API, SQL and worker slices."""
import asyncio
import json
from contextlib import suppress
from types import SimpleNamespace
import httpx
import pytest
import pytest_asyncio
from app.main import app
from app.config import settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api.prospecting import router
from app.db.session import Base, get_db
from app.db.tenancy import current_tenant_id
from app.models.prospecting import ProspectingRun
from app.models.plugin import Plugin
from app.services import prospecting as service


@pytest_asyncio.fixture
async def stack(tmp_path, monkeypatch):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'prospecting.db'))
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(settings,'REQUIRE_USER_AUTH',True)
    monkeypatch.setattr(settings,'BACKEND_API_TOKEN','test-prospecting-internal-key-32-characters')
    async def dependency():
        async with sessions() as db: yield db
    app.dependency_overrides[get_db]=dependency
    real_sleep=asyncio.sleep
    async def no_delay(_): await real_sleep(0)
    monkeypatch.setattr(service.asyncio,'sleep',no_delay)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'authorization':'Bearer test-prospecting-internal-key-32-characters'}) as client:
        yield client,sessions
    app.dependency_overrides.clear()
    await engine.dispose()


A={'x-grokbot-user-id':'account-a'}
B={'x-grokbot-user-id':'account-b'}
BODY={'market':'cybersecurity','product':'cloud security','limit':3}
PAGE={'pageid':10,'title':'Fixture Security','extract':'Fixture Security is a cybersecurity company providing cloud security software.'}


async def create(client, headers=A):
    response=await client.post('/api/prospecting/runs',headers=headers,json=BODY)
    assert response.status_code==202,response.text
    return response.json()


@pytest.mark.asyncio
async def test_routes_persist_real_pipeline_account_isolation_draft_and_no_send(stack,monkeypatch):
    client,sessions=stack
    calls=[]
    async def source(params):
        calls.append(params)
        if 'list' in params: return {'query':{'search':[{'pageid':10},{'pageid':10},{'pageid':11}]}}
        if params['pageids']=='10': return {'query':{'pages':[PAGE]}}
        return {'query':{'pages':[{'pageid':11,'title':'Cybersecurity','extract':'Cybersecurity concerns protection of information.'}]}}
    monkeypatch.setattr(service,'wikipedia',source)
    assert (await client.post('/api/prospecting/runs',json=BODY)).status_code==401
    run=await create(client); rid=run['id']
    assert run['status']=='pending' and not run['results'] and run['gmail']['status']=='connection_required'
    assert (await client.get('/api/prospecting/runs/'+rid,headers=B)).status_code==404
    assert (await client.post('/api/prospecting/runs/'+rid+'/cancel',headers=B)).status_code==404
    assert (await client.get('/api/prospecting/runs',headers=B)).json()==[]
    assert (await client.post(f'/api/prospecting/runs/{rid}/results/wikipedia-10/draft',headers=A,json={})).status_code==409
    for _ in range(4): assert await service.tick(sessions)
    result=(await client.get('/api/prospecting/runs/'+rid,headers=A)).json()
    assert result['status']=='completed' and len(result['results'])==1
    item=result['results'][0]
    assert item['observed']['description']==PAGE['extract']
    assert item['source_url']=='https://en.wikipedia.org/wiki/Fixture_Security'
    assert item['contact']=={'status':'needed','email':None,'source_url':None}
    assert item['retrieved_at'] and item['inferred']['fit_score']==30
    assert sum(x['points'] for x in item['inferred']['contributions'])==item['inferred']['fit_score']
    assert {x['source_url'] for x in item['inferred']['contributions']}=={item['source_url']}
    assert result['sending']=={'enabled':False,'review_required':True}
    assert not any('intent' in k for k in item['observed'])
    assert len(calls)==4 and any(e['type']=='result_saved' for e in result['events'])
    assert (await client.post(f'/api/prospecting/runs/{rid}/results/wikipedia-10/draft',headers=B,json={})).status_code==404
    draft=(await client.post(f'/api/prospecting/runs/{rid}/results/wikipedia-10/draft',headers=A,json={'sender_name':'Pat'})).json()
    assert draft['status']=='local_draft' and draft['recipient'] is None and draft['review_required']
    assert len(calls)==4  # Local preparation makes no provider call.
    token=current_tenant_id.set('account-a')
    try:
        async with sessions() as db:
            persisted=(await db.execute(select(ProspectingRun).where(ProspectingRun.id==rid))).scalar_one()
            assert persisted.results[0]['draft']==draft
            assert persisted.events[-1]['sent'] is False
    finally: current_tenant_id.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('after_result,expected',[(False,'failed'),(True,'partial')])
async def test_source_failure_rate_limit_keeps_partial_evidence(stack,monkeypatch,after_result,expected):
    client,sessions=stack
    run=await create(client)
    calls=0
    async def source(params):
        nonlocal calls
        calls+=1
        if calls==1: return {'query':{'search':[{'pageid':10},{'pageid':11}]}}
        if after_result and calls==2: return {'query':{'pages':[PAGE]}}
        raise service.SourceError('rate_limited',60)
    monkeypatch.setattr(service,'wikipedia',source)
    for _ in range(3 if after_result else 2): await service.tick(sessions)
    result=(await client.get('/api/prospecting/runs/'+run['id'],headers=A)).json()
    assert result['status']==expected and bool(result['results'])==after_result
    assert result['events'][-1]['code']=='rate_limited' and result['events'][-1]['retry_after']==60
    assert not await service.tick(sessions)


@pytest.mark.asyncio
async def test_cancel_recovery_and_metadata_are_scoped(stack,monkeypatch):
    client,sessions=stack
    run=await create(client)
    token=current_tenant_id.set('account-b')
    try:
        async with sessions() as db:
            db.add(Plugin(name='Gmail',url='https://example.org/mcp',tools=[{'name':'browser_navigate'}])); await db.commit()
    finally: current_tenant_id.reset(token)
    assert (await client.get('/api/prospecting/runs/'+run['id'],headers=A)).json()['gmail']['status']=='connection_required'
    other=await create(client,B)
    assert other['gmail']['status']=='connection_saved' and not other['gmail']['verified']
    await client.post('/api/prospecting/runs/'+run['id']+'/cancel',headers=A)
    async def forbidden(_): raise AssertionError('Cancelled run made a network call')
    monkeypatch.setattr(service,'wikipedia',forbidden)
    await service.tick(sessions)
    assert (await client.get('/api/prospecting/runs/'+run['id'],headers=A)).json()['status']=='cancelled'
    token=current_tenant_id.set('account-b')
    try:
        async with sessions() as db:
            row=(await db.execute(select(ProspectingRun).where(ProspectingRun.id==other['id']))).scalar_one()
            row.status='running'; row.results=[{'id':'saved'}]; row.cursor=1; await db.commit()
    finally: current_tenant_id.reset(token)
    await service.recover(sessions)
    result=(await client.get('/api/prospecting/runs/'+other['id'],headers=B)).json()
    assert result['status']=='pending' and result['results']==[{'id':'saved'}]


@pytest.mark.asyncio
async def test_input_validation_and_queue_limit(stack):
    client,_=stack
    for invalid in [{'market':' ','product':'ok'}, {**BODY,'limit':21}, {**BODY,'source_url':'http://localhost'}]:
        assert (await client.post('/api/prospecting/runs',headers=A,json=invalid)).status_code==422
    for _ in range(3): await create(client)
    assert (await client.post('/api/prospecting/runs',headers=A,json=BODY)).status_code==429


@pytest.mark.asyncio
@pytest.mark.parametrize('status,code',[(429,'rate_limited'),(302,'source_http_302'),(403,'source_http_403'),(500,'source_http_500')])
async def test_public_api_rejects_redirect_errors_and_never_exposes_response(monkeypatch,status,code):
    real_client=httpx.AsyncClient
    async def public(url): assert url==service.API
    monkeypatch.setattr(service,'validate_endpoint',public)
    def respond(req):
        assert req.url.host=='en.wikipedia.org'
        return httpx.Response(status,text='private diagnostics',headers={'location':'http://127.0.0.1/','retry-after':'60'})
    monkeypatch.setattr(service.httpx,'AsyncClient',lambda **kwargs: real_client(transport=httpx.MockTransport(respond),**kwargs))
    with pytest.raises(service.SourceError) as error: await service.wikipedia({'list':'search'})
    assert error.value.code==code and 'private diagnostics' not in str(error.value)


@pytest.mark.asyncio
async def test_response_size_timeout_and_invalid_json(monkeypatch):
    real_client=httpx.AsyncClient
    async def public(_): pass
    monkeypatch.setattr(service,'validate_endpoint',public)
    for content,code in [(b'x'*(service.MAX_BYTES+1),'response_too_large'),(b'not json','source_unavailable')]:
        monkeypatch.setattr(service.httpx,'AsyncClient',lambda **kwargs: real_client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=content)),**kwargs))
        with pytest.raises(service.SourceError) as error: await service.wikipedia({})
        assert error.value.code==code
    def timeout(_): raise httpx.ReadTimeout('private diagnostic')
    monkeypatch.setattr(service.httpx,'AsyncClient',lambda **kwargs: real_client(transport=httpx.MockTransport(timeout),**kwargs))
    with pytest.raises(service.SourceError) as error: await service.wikipedia({})
    assert error.value.code=='source_timeout'
