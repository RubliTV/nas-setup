"""Small, pinned catalog. Upstream tags verified against LinuxServer releases.

Recipe tests validate our bindings; physical-NAS/app startup validation remains
separate. Updating a tested tag is an explicit release change, never `latest`.
"""
from .models import SetupError
from .system import bind

APPS = [
    {'id':'radarr','name':'Radarr','image':'lscr.io/linuxserver/radarr:6.4.4.10685-ls323','port':7878,
     'description':{'en':'Organize and manage movies.','pt':'Organize e gerencie filmes.'},
     'first_run':{'en':'Create an app login, then connect your download client and indexers. Media is available at /data.','pt':'Crie o login do aplicativo e conecte seu cliente de downloads e indexadores. Mídia disponível em /data.'}},
    {'id':'sonarr','name':'Sonarr','image':'lscr.io/linuxserver/sonarr:4.0.20.3014-ls1','port':8989,
     'description':{'en':'Organize and manage TV series.','pt':'Organize e gerencie séries.'},
     'first_run':{'en':'Create an app login, then connect your download client and indexers. Media is available at /data.','pt':'Crie o login do aplicativo e conecte seu cliente de downloads e indexadores. Mídia disponível em /data.'}},
    {'id':'prowlarr','name':'Prowlarr','image':'lscr.io/linuxserver/prowlarr:2.6.5.5623-ls165','port':9696,
     'description':{'en':'Manage indexers for your media apps.','pt':'Gerencie indexadores dos aplicativos de mídia.'},
     'first_run':{'en':'Create an app login, add indexers, and connect Radarr or Sonarr.','pt':'Crie o login, adicione indexadores e conecte Radarr ou Sonarr.'}},
]


def render_catalog(app, config, media, uid, gid, port):
    recipe=next((a for a in APPS if a['id']==app),None)
    if recipe is None:raise SetupError('app_invalid')
    return {'services':{app:{'image':recipe['image'],'restart':'unless-stopped',
        'environment':{'PUID':str(uid),'PGID':str(gid),'TZ':'America/Sao_Paulo'},
        'ports':[f'{port}:{recipe["port"]}'],
        'volumes':[bind(config,'/config')]+([bind(media,'/data')] if app!='prowlarr' else [])}}}
