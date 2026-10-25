from fastapi.testclient import TestClient
from nas_setup.web import create_app
from nas_setup.auth import issue_code


class Backend:
    def __init__(self):self.calls=[]
    def call(self,operation,payload=None):
        self.calls.append((operation,payload))
        return {'apps':[]} if operation=='manager_catalog' else {'status':'ok'}


def test_manager_enrollment_login_and_session_bound_mutations(tmp_path):
    backend=Backend();code=issue_code(tmp_path)
    client=TestClient(create_app(backend,tmp_path,'https://nas.test:8443'),base_url='https://nas.test:8443')
    assert client.get('/api/manager/catalog').status_code==401
    assert client.get('/api/session').json()['manager_configured'] is False
    assert client.post('/api/manager-account',json={'username':'admin','password':'GoodPassword-2026!'}).status_code==401
    csrf=client.post('/api/login',json={'code':code}).json()['csrf']
    headers={'X-CSRF-Token':csrf}
    assert client.get('/api/manager/catalog').status_code==409
    assert client.post('/api/manager-account',json={'username':'admin','password':'GoodPassword-2026!'}).status_code==403
    r=client.post('/api/manager-account',json={'username':'admin','password':'GoodPassword-2026!'},headers=headers)
    assert r.status_code==200,r.text
    csrf=r.json()['csrf'];headers={'X-CSRF-Token':csrf}
    assert client.get('/api/session').json()['username']=='admin'
    assert client.get('/api/manager/catalog').json()=={'apps':[]}
    assert client.post('/api/manager/deploy',json={'plan_id':'test'}).status_code==403
    assert client.post('/api/manager/deploy',json={'plan_id':'test'},headers=headers).status_code==200
    operation,payload=backend.calls[-1]
    assert operation=='manager_deploy' and payload['session'] and payload['data']=={'plan_id':'test'}
    assert client.post('/api/manager-account',json={'username':'other','password':'OtherPassword-2026!'},headers=headers).status_code==409
    assert client.post('/api/logout',headers=headers).status_code==200
    assert client.get('/api/manager/catalog').status_code==401
    assert client.post('/api/manager-login',json={'username':'admin','password':'IncorrectPassword!'}).status_code==401
    response=client.post('/api/manager-login',json={'username':'admin','password':'GoodPassword-2026!'})
    assert response.status_code==200
    assert 'secure' in response.headers['set-cookie'].lower()
    assert 'GoodPassword' not in response.text
    assert client.get('/api/manager/catalog').status_code==200


def test_manager_errors_do_not_echo_secrets_or_allow_unknown_routes(tmp_path):
    c=TestClient(create_app(Backend(),tmp_path,'http://localhost:8765',demo=True),base_url='http://localhost:8765')
    csrf=c.post('/api/demo-login').json()['csrf'];headers={'X-CSRF-Token':csrf}
    r=c.post('/api/manager-account',json={'username':'INVALID!','password':'DO_NOT_ECHO'},headers=headers)
    assert r.status_code in (409,422) and 'DO_NOT_ECHO' not in r.text
    assert c.post('/api/manager/shell',json={'command':'id'},headers=headers).status_code==409
    assert c.get('/api/manager/deploy').status_code==409
    assert c.post('/api/manager/prepare_docker',json={}).status_code==403
    assert c.post('/api/manager/prepare_docker',json={},headers=headers).status_code==200
