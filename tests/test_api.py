import importlib.util
from fastapi.testclient import TestClient
import pytest

class Backend:
    def call(self, operation, payload=None):
        if operation=='inventory': return {'supported':True,'volumes':[]}
        if operation=='plan': return {'apps':payload['apps']}
        return {'status':'ok'}

@pytest.fixture
def client(tmp_path):
    assert importlib.util.find_spec('nas_setup.web'), 'Web API is not implemented'
    from nas_setup.web import create_app
    from nas_setup.auth import issue_code
    state=tmp_path/'web'; code=issue_code(state)
    with TestClient(create_app(Backend(),state,'https://nas.test:8443'),base_url='https://nas.test:8443') as c:
        yield c,code,state


def login(c,code):
    r=c.post('/api/login',json={'code':code},headers={'Origin':'https://nas.test:8443'})
    assert r.status_code==200
    return r.json()['csrf']


def test_private_routes_require_login(client):
    c,_,_=client
    assert c.get('/api/inventory').status_code==401
    assert c.post('/api/plan',json={'apps':['tailscale']}).status_code==401


def test_login_code_one_use_and_not_stored_plaintext(client):
    c,code,state=client
    assert code not in (state/'access.json').read_text()
    login(c,code)
    c.cookies.clear()
    assert c.post('/api/login',json={'code':code}).status_code==401


def test_session_and_csrf_boundaries(client):
    c,code,_=client; csrf=login(c,code)
    assert c.get('/api/inventory').json()['supported']
    assert c.post('/api/plan',json={'apps':['tailscale']}).status_code==403
    assert c.post('/api/plan',json={'apps':['tailscale']},headers={'X-CSRF-Token':csrf,'Origin':'https://evil.test'}).status_code==403
    r=c.post('/api/plan',json={'apps':['tailscale']},headers={'X-CSRF-Token':csrf,'Origin':'https://nas.test:8443'})
    assert r.status_code==200
    assert r.json()['apps']==['tailscale']


def test_rebinding_and_command_injection_refused(client):
    c,code,_=client; csrf=login(c,code)
    assert c.get('/api/inventory',headers={'Host':'evil.test'}).status_code==400
    r=c.post('/api/plan',json={'apps':['tailscale'],'command':'id'},headers={'X-CSRF-Token':csrf})
    assert r.status_code==422


def test_login_rate_limit(client):
    c,code,_=client
    for _ in range(5): assert c.post('/api/login',json={'code':'wrong'}).status_code==401
    assert c.post('/api/login',json={'code':code}).status_code==429


def test_cookie_and_no_store(client):
    c,code,_=client
    r=c.post('/api/login',json={'code':code})
    cookie=r.headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie
    assert r.headers['cache-control']=='no-store'
