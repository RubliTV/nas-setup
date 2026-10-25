"""Unprivileged web boundary. All machine mutations go through the worker."""
import hmac
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from .auth import Auth, issue_code
from .models import SetupError, Selection
from .setup_models import SetupSelection
from .setup_engine import SetupStart

class Login(BaseModel):
    model_config=ConfigDict(extra='forbid')
    code:str=Field(min_length=1,max_length=256)


def create_app(backend,state_dir,origin,demo=False):
    parsed=urlsplit(origin)
    if parsed.scheme!='https' and not (demo and parsed.hostname in ('127.0.0.1','localhost')):
        raise ValueError('HTTPS required outside loopback demo')
    auth=Auth(state_dir)
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

    @app.get('/api/session')
    def session(request:Request):
        try:return {'authenticated':True,'csrf':authorize(request)['csrf'],'demo':demo}
        except SetupError:return {'authenticated':False,'demo':demo}

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
