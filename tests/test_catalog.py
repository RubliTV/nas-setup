from nas_setup.catalog import APPS, render_catalog


def test_catalog_pins_images_and_maps_single_media_root(tmp_path):
    for app in APPS:
        assert app['image'].startswith('lscr.io/linuxserver/')
        assert ':latest' not in app['image']
        cfg=render_catalog(app['id'],tmp_path/'config',tmp_path/'media',1000,100,app['port'])
        service=cfg['services'][app['id']]
        assert service['environment']['PUID']=='1000'
        assert service['environment']['PGID']=='100'
        if app['id']!='prowlarr':
            assert service['volumes'][1]['target']=='/data'
            assert service['volumes'][1]['source']==str(tmp_path/'media')
        assert service['restart']=='unless-stopped'


def test_prowlarr_needs_only_config_no_media(tmp_path):
    cfg=render_catalog('prowlarr',tmp_path/'config',None,1000,100,9696)
    assert len(cfg['services']['prowlarr']['volumes'])==1
