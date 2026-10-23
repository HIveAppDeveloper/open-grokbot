import socket
import pytest
from app.services import prospecting_profile as service


@pytest.mark.parametrize('url', ['http://example.com', 'https://localhost', 'https://127.0.0.1', 'https://[::1]', 'https://x.internal', 'https://user:pass@example.com', 'https://example.com:8443'])
def test_rejects_nonpublic_targets(url):
    with pytest.raises(service.ProfileError): service.target(url)


def test_dns_rejects_mixed_private_answer(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(service.ProfileError): service.public_addresses('company.example')


def test_extract_is_evidence_not_generated_ideal_buyers():
    result = service.parse_copy('<title>Company</title><meta name="description" content="Scheduling software for dental clinics."><script>private prompt</script>', 'https://company.example/')
    assert result['product'] == 'Scheduling software for dental clinics.'
    assert result['market'] == 'dental clinics'
    assert result['requires_confirmation'] and result['method'] == 'public_page_extract'
    assert 'private prompt' not in result['description']


@pytest.mark.parametrize('status,robots', [(200,b'User-agent: *\nDisallow: /'),(403,b''),(302,b'')])
def test_access_restrictions_never_read_company(monkeypatch,status,robots):
    calls=[]
    monkeypatch.setattr(service,'public_addresses',lambda host:['8.8.8.8'])
    def read(host,address,path,limit):
        calls.append(path)
        return status,'text/plain',robots
    monkeypatch.setattr(service,'read_page',read)
    with pytest.raises(service.ProfileError): service._profile('https://company.example/')
    assert calls==['/robots.txt']


def test_missing_robots_allows_public_page_without_redirects(monkeypatch):
    monkeypatch.setattr(service,'public_addresses',lambda host:['8.8.8.8'])
    def read(host,address,path,limit):
        assert host=='company.example' and address=='8.8.8.8'
        return (404,'text/plain',b'') if path=='/robots.txt' else (200,'text/html',b'<title>Company</title><p>Tools for clinics.</p>')
    monkeypatch.setattr(service,'read_page',read)
    assert service._profile('https://company.example/')['market']=='clinics'


@pytest.mark.asyncio
async def test_network_diagnostics_are_not_exposed(monkeypatch):
    def fail(_): raise OSError('private socket diagnostic')
    monkeypatch.setattr(service,'_profile',fail)
    with pytest.raises(service.ProfileError) as error: await service.profile('https://company.example')
    assert 'private socket' not in str(error.value)
