"""Unprivileged web boundary. All machine mutations go through the worker."""
import hmac
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, Body
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from .auth import Auth, issue_code
from .manager_auth import ManagerAccount
from .models import SetupError, Selection
from .setup_models import SetupSelection
from .setup_engine import SetupStart

class Login(BaseModel):
    model_config=ConfigDict(extra='forbid')
    code:str=Field(min_length=1,max_length=256)


class ManagerLogin(BaseModel):
    model_config=ConfigDict(extra='forbid')
    username:str=Field(min_length=1,max_length=64)
    password:SecretStr=Field(min_length=1,max_length=128)


def create_app(backend,state_dir,origin,demo=False):
    parsed=urlsplit(origin)
    if parsed.scheme!='https' and not (demo and parsed.hostname in ('127.0.0.1','localhost')):
        raise ValueError('HTTPS required outside loopback demo')
    auth=Auth(state_dir)
    account=ManagerAccount(state_dir)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    static=Path(__file__).parent/'static'

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,error):
        # Pydantic errors may embed submitted passwords; never echo input values.
        return JSONResponse({'error':'request_invalid'},status_code=422)

    @app.exception_handler(SetupError)
    async def domain_error(request,error):
        status={'login_required':401,'login_invalid':401,'rate_limited':429,'csrf_invalid':403,'worker_unavailable':503}.get(error.code,409)
        return JSONResponse({'error':error.code},status_code=status)

    @app.middleware('http')
    async def boundary(request:Request,call_next):
        if request.headers.get('host')!=parsed.netloc:
            response=JSONResponse({'error':'host_invalid'},status_code=400)
        elif request.headers.get('origin') and request.headers['origin']!=origin:
            response=JSONResponse({'error':'csrf_invalid'},status_code=403)
        else:
            response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Referrer-Policy':'no-referrer','X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    def authorize(request,mutating=False):
        session=auth.session(request.cookies.get('nas_session',''))
        if mutating and not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):
            raise SetupError('csrf_invalid')
        return session

    @app.post('/api/login')
    def login(body:Login,request:Request):
        token,csrf=auth.login(body.code,request.client.host if request.client else 'local')
        response=JSONResponse({'csrf':csrf,'demo':demo})
        response.set_cookie('nas_session',token,secure=not demo,httponly=True,samesite='strict',max_age=3600)
        return response

    if demo:
        @app.post('/api/demo-login')
        def demo_login():
            # Only registered for an explicitly simulated backend on loopback HTTP.
            code=issue_code(state_dir)
            token,csrf=auth.login(code,'demo')
            response=JSONResponse({'csrf':csrf,'demo':True})
            response.set_cookie('nas_session',token,httponly=True,samesite='strict',max_age=3600)
            return response

    def signed_in(token,csrf,username=None):
        response=JSONResponse({'csrf':csrf,'demo':demo,'username':username,'manager_configured':account.exists()})
        response.set_cookie('nas_session',token,secure=not demo,httponly=True,samesite='strict',max_age=3600)
        return response

    @app.post('/api/manager-login')
    def manager_login(body:ManagerLogin,request:Request):
        token,csrf=auth.login_password(body.username,body.password.get_secret_value(),request.client.host if request.client else 'local')
        return signed_in(token,csrf,body.username)

    @app.post('/api/manager-account')
    def manager_account(body:ManagerLogin,request:Request):
        authorize(request,True)
        account.create(body.username,body.password.get_secret_value())
        token,csrf=auth.login_password(body.username,body.password.get_secret_value(),request.client.host if request.client else 'local')
        return signed_in(token,csrf,body.username)

    @app.post('/api/logout')
    def logout(request:Request):
        authorize(request,True)
        auth.logout(request.cookies.get('nas_session',''))
        response=JSONResponse({'authenticated':False})
        response.delete_cookie('nas_session',secure=not demo,httponly=True,samesite='strict')
        return response

    @app.get('/api/session')
    def session(request:Request):
        info={'demo':demo,'manager_configured':account.exists()}
        try:
            current=authorize(request)
            return {**info,'authenticated':True,'csrf':current['csrf'],'username':current.get('username')}
        except SetupError:return {**info,'authenticated':False}

    def manager_authorize(request,mutating=False):
        current=authorize(request,mutating)
        if not demo and not account.exists():raise SetupError('manager_account_required')
        return current

    @app.get('/api/manager/{operation}')
    def manager_read(operation:str,request:Request):
        current=manager_authorize(request)
        if operation not in {'catalog','overview','options','job'}:raise SetupError('operation_invalid')
        return backend.call('manager_'+operation,{'session':current['id'],'data':{}})

    @app.post('/api/manager/{operation}')
    def manager_write(operation:str,request:Request,body:dict=Body(default_factory=dict)):
        current=manager_authorize(request,True)
        if operation not in {'plan','deploy','action','logs','job','overview','retry','prepare_docker'}:raise SetupError('operation_invalid')
        import json
        if len(json.dumps(body).encode())>196608:raise SetupError('request_invalid')
        return backend.call('manager_'+operation,{'session':current['id'],'data':body})

    @app.get('/api/inventory')
    def inventory(request:Request):
        authorize(request); return backend.call('inventory',{})

    @app.get('/api/job')
    def job(request:Request):
        authorize(request); return backend.call('job',{})

    @app.get('/api/tailscale')
    def tailscale(request:Request):
        authorize(request); return backend.call('tailscale',{})

    @app.post('/api/plan')
    def plan(selection:Selection,request:Request):
        authorize(request,True); return backend.call('plan',selection.model_dump())

    @app.post('/api/install')
    def install(selection:Selection,request:Request):
        authorize(request,True); return backend.call('start',selection.model_dump())

    @app.post('/api/setup/plan')
    def setup_plan(selection:SetupSelection,request:Request):
        session=authorize(request,True)
        return backend.call('setup_plan',{'session':session['id'],'data':selection.model_dump()})

    @app.post('/api/setup/recovery')
    def setup_recovery(request:Request):
        session=authorize(request,True)
        return backend.call('setup_recovery',{'session':session['id'],'data':{}})

    @app.post('/api/setup/{operation}')
    def setup_action(operation:str,body:SetupStart,request:Request):
        session=authorize(request,True)
        if operation not in ('start','resume'):raise SetupError('operation_invalid')
        payload=body.model_dump(exclude={'passwords'})
        payload['passwords']={key:value.get_secret_value() for key,value in body.passwords.items()}
        return backend.call('setup_'+operation,{'session':session['id'],'data':payload})

    @app.post('/api/{operation}')
    def action(operation:str,request:Request):
        authorize(request,True)
        if operation not in ('pause','resume','finish'): raise SetupError('operation_invalid')
        return backend.call(operation,{})

    @app.get('/')
    def index(): return FileResponse(static/'index.html')
    app.mount('/static',StaticFiles(directory=static),name='static')
    return app
