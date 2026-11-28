#!/usr/bin/env python3
"""Bounded SAR workbench: catalogue → one chip → CFAR → historical AIS → encrypted review.

Inputs and outputs are password encrypted. Existing single-scene calibration tools
stay intact; their reusable raster/CFAR/matching functions run with dynamic scenes.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
import math
import os
import re
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import copernicus_sar_catalog as catalog
import copernicus_sar_chip as chip
import copernicus_sar_cfar as cfar
import copernicus_historical_context as historical
import sar_crypto

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'config/sar_workbench.json'
WORKSPACE=ROOT/'public/data/sar/workbench.enc.json'
PUBLIC_CONFIG=ROOT/'public/assets/sar-workbench-config.json'
CATALOG_ITEMS='https://stac.dataspace.copernicus.eu/v1/collections/sentinel-1-grd/items'
LIMIT='Unreviewed radar candidates. These do not establish vessel identity, deliberate AIS disablement or hostile activity.'


class WorkbenchError(RuntimeError):pass


def utc():return datetime.now(timezone.utc)
def iso(value):return value.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
def load_config():return json.loads(CONFIG.read_text(encoding='utf-8'))


def validate_request(raw,cfg,now=None):
    now=now or utc()
    if not isinstance(raw,dict) or raw.get('v')!=1:raise WorkbenchError('REQUEST_VERSION')
    if set(raw)-{'v','id','created_at','action','area_id','bbox','days','scene_id','gfw_context','danish_ais','range_from','range_to','event_id'}:raise WorkbenchError('REQUEST_FIELDS')
    action=raw.get('action')
    if action not in {'search','analyse','refresh_context'}:raise WorkbenchError('REQUEST_ACTION')
    identifier=str(raw.get('id',''))
    if not re.fullmatch(r'[a-f0-9-]{32,36}',identifier):raise WorkbenchError('REQUEST_ID')
    try:created=catalog.parse_utc(raw.get('created_at'),'request time')
    except Exception as exc:raise WorkbenchError('REQUEST_TIME') from exc
    if not -300<=(now-created).total_seconds()<=cfg['max_request_age_hours']*3600:raise WorkbenchError('REQUEST_EXPIRED')
    days=raw.get('days',14)
    if isinstance(days,bool) or not isinstance(days,int) or not 1<=days<=30:raise WorkbenchError('REQUEST_DAYS')
    areas={a['id']:a for a in cfg['areas']}
    area_id=raw.get('area_id','custom')
    if area_id in areas:
        bbox=areas[area_id]['bbox'];name=areas[area_id]['name']
        if 'bbox' in raw and raw['bbox']!=bbox:raise WorkbenchError('PRESET_BBOX_MISMATCH')
    elif area_id=='custom':bbox=raw.get('bbox');name='Custom area'
    else:raise WorkbenchError('UNKNOWN_AREA')
    if not isinstance(bbox,list) or len(bbox)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in bbox):raise WorkbenchError('BBOX_INVALID')
    west,south,east,north=bbox
    # Initial operational scope follows the existing North Sea/Baltic project.
    if not(-25<=west<east<=35 and 48<=south<north<=80):raise WorkbenchError('BBOX_OUTSIDE_SCOPE')
    width_m=(east-west)*111320*math.cos(math.radians((south+north)/2))
    height_m=(north-south)*111320
    area=width_m*height_m/1e6
    width=max(64,math.ceil(width_m/cfg['pixel_spacing_m']))
    height=max(64,math.ceil(height_m/cfg['pixel_spacing_m']))
    if area>cfg['max_area_km2'] or max(width,height)>cfg['max_dimension_px'] or min(width_m,height_m)<1000:raise WorkbenchError('BBOX_TOO_LARGE_OR_SMALL')
    scene_id=raw.get('scene_id','latest')
    if scene_id!='latest' and not re.fullmatch(r'S1[A-D]_IW_GRDH_1SDV_[A-Za-z0-9_]{35,120}',str(scene_id)):raise WorkbenchError('SCENE_ID_INVALID')
    for key in ('gfw_context','danish_ais'):
        if key in raw and not isinstance(raw[key],bool):raise WorkbenchError('REQUEST_BOOLEAN')
    if raw.get('danish_ais') and not (3<=west<east<=17 and 53<=south<north<=60):raise WorkbenchError('DANISH_AIS_OUTSIDE_REGION')
    if any(key in raw for key in ('range_from','range_to','event_id')):
        try:
            begin=catalog.parse_utc(raw['range_from'],'period start');end=catalog.parse_utc(raw['range_to'],'period end')
        except Exception as exc:raise WorkbenchError('EVENT_PERIOD_INVALID') from exc
        if not now-timedelta(days=14,minutes=5)<=begin<=end<=now+timedelta(minutes=5):raise WorkbenchError('EVENT_PERIOD_OUTSIDE_RETENTION')
        if not isinstance(raw.get('event_id'),str) or not 1<=len(raw['event_id'])<=160:raise WorkbenchError('EVENT_ID_INVALID')
    return {**raw,'action':action,'area_id':area_id,'area_name':name,'bbox':bbox,'days':days,'scene_id':scene_id,
            'width_px':width,'height_px':height,'area_km2':round(area,2),'approx_width_m':width_m,'approx_height_m':height_m}


def point_in_ring(point,ring):
    x,y=point;inside=False
    for (x1,y1,*_),(x2,y2,*_) in zip(ring,ring[1:]):
        if (y1>y)!=(y2>y) and x<(x2-x1)*(y-y1)/(y2-y1)+x1:inside=not inside
    return inside


def geometry_contains(geometry,point):
    polygons=[geometry.get('coordinates',[])] if geometry.get('type')=='Polygon' else geometry.get('coordinates',[]) if geometry.get('type')=='MultiPolygon' else []
    return any(p and point_in_ring(point,p[0]) and not any(point_in_ring(point,h) for h in p[1:]) for p in polygons)


def normalize_scene(feature,bbox):
    props=feature.get('properties') or {}
    item_id=str(feature.get('id',''))
    if not re.fullmatch(r'S1[A-D]_IW_GRDH_1SDV_[A-Za-z0-9_]{35,120}',item_id):return None
    if props.get('sar:instrument_mode')!='IW' or not {'VV','VH'}<=set(props.get('sar:polarizations') or []):return None
    acquired=catalog.parse_utc(props.get('start_datetime') or props.get('datetime'),'scene time')
    normalized=catalog.normalize_scene_id(item_id)
    stamp=re.search(r'_(\d{8}T\d{6})_',normalized)
    if not stamp or stamp[1]!=acquired.strftime('%Y%m%dT%H%M%S'):return None
    west,south,east,north=bbox
    covers=all(geometry_contains(feature.get('geometry') or {},p) for p in [(west,south),(west,north),(east,south),(east,north),((west+east)/2,(south+north)/2)])
    orbit=props.get('sat:orbit_state')
    return {'item_id':item_id,'scene_id':normalized,'datetime':iso(acquired),
            'end_datetime':props.get('end_datetime') or iso(acquired+timedelta(seconds=30)),
            'platform':props.get('platform') or normalized[:3],'orbit_direction':orbit,
            'bbox':feature.get('bbox'),'footprint':feature.get('geometry'),
            'covers_aoi':covers,'processable':covers and orbit in {'ascending','descending'},
            'gfw_delay_elapsed':(utc()-acquired).total_seconds()>=5*86400,
            'source_url':f'https://stac.dataspace.copernicus.eu/v1/collections/sentinel-1-grd/items/{item_id}'}


def read_response(response,maximum):
    if not 200<=response.status_code<300:raise WorkbenchError(f'PROVIDER_HTTP_{response.status_code}')
    chunks=[];size=0
    for block in response.iter_content(65536):
        size+=len(block)
        if size>maximum:raise WorkbenchError('PROVIDER_RESPONSE_TOO_LARGE')
        chunks.append(block)
    return b''.join(chunks)


def search_scenes(request,cfg,session=None,now=None):
    now=now or utc();session=session or requests.Session()
    begin=catalog.parse_utc(request['range_from'],'period start') if request.get('range_from') else now-timedelta(days=request['days'])
    end=catalog.parse_utc(request['range_to'],'period end') if request.get('range_to') else now
    params={'bbox':','.join(map(str,request['bbox'])),'datetime':f'{iso(begin)}/{iso(end)}','limit':cfg['max_catalog_scenes']}
    with session.get(CATALOG_ITEMS,params=params,timeout=(10,45),stream=True) as response:
        payload=json.loads(read_response(response,3*1024*1024))
    if not isinstance(payload.get('features'),list):raise WorkbenchError('CATALOG_SCHEMA')
    scenes=[]
    for f in payload['features']:
        try:s=normalize_scene(f,request['bbox'])
        except (ValueError,TypeError,KeyError,catalog.PilotError):continue
        if s and begin<=catalog.parse_utc(s['datetime'],'scene time')<=end:scenes.append(s)
    scenes=list({s['scene_id']:s for s in scenes}.values())
    scenes.sort(key=lambda s:s['datetime'],reverse=True)
    return {'scenes':scenes,'truncated':any(l.get('rel')=='next' for l in payload.get('links',[]) if isinstance(l,dict)),
            'queried_at':iso(now),'provider':'Copernicus Data Space STAC','raw_raster_downloaded':False}


def read_history():
    path=ROOT/'data/history/maritime_source_history_21d.jsonl'
    rows=[]
    if path.exists():
        if path.stat().st_size>24*1024*1024:raise WorkbenchError('AIS_HISTORY_SIZE')
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                try:r=json.loads(line)
                except ValueError:continue
                if isinstance(r,dict):rows.append(r)
    return rows


def land_mask(valid,bbox,cfg,session=None,archive_bytes=None,pixel_m=10):
    import numpy as np
    import shapefile
    from PIL import Image,ImageDraw
    from scipy import ndimage
    if archive_bytes is None:
        with (session or requests.Session()).get(cfg['land_archive_url'],timeout=(10,60),stream=True) as response:
            archive_bytes=read_response(response,8*1024*1024)
    if hashlib.sha256(archive_bytes).hexdigest()!=cfg['land_archive_sha256']:raise WorkbenchError('LAND_MASK_CHECKSUM')
    height,width=valid.shape;west,south,east,north=bbox
    radius=max(1,math.ceil(1000/pixel_m));pad=radius+1
    dx=(east-west)/width;dy=(north-south)/height
    # Include nearby land outside the requested chip before applying the coast buffer.
    mask_bbox=[west-pad*dx,south-pad*dy,east+pad*dx,north+pad*dy]
    image=Image.new('1',(width+2*pad,height+2*pad),0);draw=ImageDraw.Draw(image)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as z:
        names={Path(n).suffix:n for n in z.namelist() if n.endswith(('.shp','.shx','.dbf'))}
        if not {'.shp','.shx','.dbf'}<=names.keys() or any(z.getinfo(n).file_size>64*1024*1024 for n in names.values()):raise WorkbenchError('LAND_MASK_ARCHIVE')
        reader=shapefile.Reader(shp=io.BytesIO(z.read(names['.shp'])),shx=io.BytesIO(z.read(names['.shx'])),dbf=io.BytesIO(z.read(names['.dbf'])))
        for shape in reader.iterShapes(bbox=mask_bbox):
            geom=shape.__geo_interface__
            polygons=[geom['coordinates']] if geom['type']=='Polygon' else geom['coordinates']
            for polygon in polygons:
                for index,ring in enumerate(polygon):
                    points=[(round((x-west)/dx)+pad,round((north-y)/dy)+pad) for x,y,*_ in ring]
                    if len(points)>2:draw.polygon(points,fill=1 if index==0 else 0)
    land=np.asarray(image,dtype=bool)
    buffered=ndimage.distance_transform_edt(~land)<=radius if land.any() else land
    land=land[pad:pad+height,pad:pad+width];buffered=buffered[pad:pad+height,pad:pad+width]
    mask=np.asarray(valid,dtype=bool)&~buffered
    if not mask.any():raise WorkbenchError('NO_OPEN_WATER_PIXELS')
    return mask,{'source':'Natural Earth 1:10m land v5.1.1','source_sha256':cfg['land_archive_sha256'],
        'land_pixels':int(land.sum()),'land_and_buffer_pixels':int(buffered.sum()),'coast_buffer_m':1000,
        'cartographic_generalization_review_required':True,'applied':True}


def derived_images(vv,valid,features):
    import numpy as np
    from PIL import Image
    good=np.asarray(valid)&np.isfinite(vv)&(vv>0)
    db=np.zeros(vv.shape,dtype='float32');db[good]=10*np.log10(vv[good])
    low,high=np.percentile(db[good],[1,99.8])
    if high<=low:raise WorkbenchError('QUICKLOOK_STRETCH')
    pixels=np.zeros(vv.shape,dtype='uint8');pixels[good]=np.rint(np.clip((db[good]-low)/(high-low),0,1)*255)
    image=Image.fromarray(pixels)
    def png(img,max_side):
        img=img.copy();img.thumbnail((max_side,max_side));out=io.BytesIO();img.save(out,format='PNG',optimize=True)
        if out.tell()>2*1024*1024:raise WorkbenchError('QUICKLOOK_SIZE')
        return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode('ascii')
    quicklook=png(image,1200)
    chips={}
    for feature in features[:60]:
        c=feature['properties']['centroid_pixel'];x,y=int(c['column']),int(c['row'])
        chips[feature['properties']['detection_id']]=png(image.crop((max(0,x-64),max(0,y-64),min(image.width,x+65),min(image.height,y+65))),260)
    return {'quicklook':quicklook,'candidate_chips':chips,'stretch_db':[float(low),float(high)],
            'preview_is_derived':True,'chip_side_approx_m':1290}


def dynamic_configs(request,scene):
    phase=json.loads((ROOT/'config/sar_copernicus_cfar.json').read_text())
    chip_cfg=json.loads((ROOT/'config/sar_copernicus_chip.json').read_text())
    acquired=catalog.parse_utc(scene['datetime'],'scene time')
    pilot={'pilot_id':'workbench','collection':'sentinel-1-grd','expected_sentinel_hub_product_id':scene['scene_id'],
        'time_range':{'from':iso(acquired-timedelta(seconds=1)),'to':iso(acquired+timedelta(seconds=1))},
        'aoi':{'bbox_wgs84':request['bbox'],'approx_width_m':request['approx_width_m'],'approx_height_m':request['approx_height_m']}}
    chip_cfg['request']['width_px']=request['width_px'];chip_cfg['request']['height_px']=request['height_px']
    chip_cfg['request']['data_filter']['orbit_direction']=scene['orbit_direction'].upper()
    phase['spatial_context']['land_buffer_radius_px']=100
    return phase,chip_cfg,pilot


def acquire_and_detect(request,scene,cfg,client_id,client_secret,binary_session=None,land_archive=None):
    phase,chip_cfg,pilot=dynamic_configs(request,scene)
    http=binary_session or chip.BinarySession()
    token,_=catalog.request_access_token(http,client_id,client_secret)
    search=catalog.safe_post(http,catalog.SENTINEL_HUB_CATALOG_URL,'scene confirmation',
        headers={'Authorization':f'Bearer {token}'},json=chip.build_catalog_search(pilot))
    selected,confirmation=cfar._scene_selection(search,pilot)
    selected.update({'platform':scene['platform']})
    # A second scene in the tiny time window is rejected by _scene_selection before processing.
    raw,headers=chip.request_process_chip(http,token,chip.build_process_request(chip_cfg,pilot),int(chip_cfg['request']['maximum_response_bytes']))
    with tempfile.TemporaryDirectory(prefix='mowlsint-sar-') as td:
        path=Path(td)/'temporary.tiff';path.write_bytes(raw);del raw
        array,validation=cfar._load_normalized_raster(path,chip_cfg)
        scale=validation['geotiff'].get('model_pixel_scale')
        tie=validation['geotiff'].get('model_tiepoint')
        west,south,east,north=request['bbox']
        if not scale or not tie or len(scale)<2 or len(tie)<6:raise WorkbenchError('RASTER_GEOREFERENCE_UNSUPPORTED')
        expected=[(east-west)/request['width_px'],(north-south)/request['height_px'],west,north]
        actual=[scale[0],scale[1],tie[3]-tie[0]*scale[0],tie[4]+tie[1]*scale[1]]
        if any(not math.isclose(a,b,rel_tol=1e-5,abs_tol=1e-7) for a,b in zip(actual,expected)):raise WorkbenchError('RASTER_GEOREFERENCE_MISMATCH')
        valid,land=land_mask(array[:,:,2]>=.5,request['bbox'],cfg,archive_bytes=land_archive)
        masks,excess,vh_hits,vh_excess,profiles,vh_summary=cfar.compare_profiles(array[:,:,0],array[:,:,1],valid,phase)
        features=cfar.group_candidates(array[:,:,0],array[:,:,1],masks,excess,vh_hits,vh_excess,phase,pilot,selected,cfar.load_infrastructure_context())
        for f in features:
            f['properties']['platform']=scene['platform']
            f['properties']['spatial_quality']['open_water_aoi_reviewed']=False
            f['properties']['human_visual_review_required']=True
        images=derived_images(array[:,:,0],valid,features)
    payload={'type':'FeatureCollection','metadata':{'scene':{'normalized_scene_id':scene['scene_id'],'datetime':scene['datetime'],'bbox':request['bbox']},
        'acquisition_footprint':scene['footprint'],'aoi_bbox_wgs84':request['bbox'],'raw_raster_retained':False,
        'land_mask':land,'profile_results':profiles,'raster_validation':validation,'scene_confirmation':confirmation,
        'processing_units_estimate':chip.processing_unit_estimate(chip_cfg),'process_api_reported_processing_units':headers.get('x-processingunits-spent'),
        'assessment_limit':LIMIT,'public_layer':False},'features':features}
    return payload,images


def fetch_context(request,scene):
    if not request.get('gfw_context'):return {},{},'not_requested'
    acquired=catalog.parse_utc(scene['datetime'],'scene time')
    if utc()-acquired<timedelta(days=5):return {},{},'pending_provider_delay'
    import fetch_gfw_context as gfw
    token=os.environ.get('GFW_TOKEN','')
    if not token:return {},{},'token_missing'
    west,south,east,north=request['bbox']
    region={'id':'sar_aoi','geometry':{'type':'Polygon','coordinates':[[[west,south],[east,south],[east,north],[west,north],[west,south]]]}}
    session=requests.Session();session.headers.update({'Authorization':f'Bearer {token}','User-Agent':'MOwlSINT-SAR-Workbench/1.0'})
    grid,events,queries=gfw.fetch_region(session,region,acquired.date(),include_events=True,max_pages=1)
    return {'records':grid,'observation_day':acquired.date().isoformat(),'queries':queries}, {'events':events},'available' if all(q['ok'] and not q.get('cap_applied') for q in queries) else 'partial_or_failed'


def match_with_context(candidates,request,scene,history,session=None):
    grid,events,gfw_state=fetch_context(request,scene)
    if not request.get('danish_ais'):
        result,queue=historical.assess(candidates,history,grid,events)
    else:
        # Download one official day only when explicitly selected. Never retain the ZIP.
        day=catalog.parse_utc(scene['datetime'],'scene time').date().isoformat()
        url=f'https://aisdata.ais.dk/aisdk-{day}.zip'
        with tempfile.TemporaryDirectory(prefix='mowlsint-danish-sar-') as td:
            archive=Path(td)/'temporary.zip';size=0
            with (session or requests.Session()).get(url,timeout=(10,120),stream=True) as response:
                if response.status_code!=200:raise WorkbenchError('DANISH_ARCHIVE_UNAVAILABLE')
                with archive.open('wb') as out:
                    for block in response.iter_content(1024*1024):
                        size+=len(block)
                        if size>900*1024*1024:raise WorkbenchError('DANISH_ARCHIVE_SIZE')
                        out.write(block)
            result,queue=historical.assess(candidates,history,grid,events,archive,request['bbox'])
    result['metadata']['gfw_context_state']=gfw_state
    result['metadata']['workbench_review_only']=True
    return result,queue


def context_or_partial(candidates,request,scene,history,matcher=None):
    try:
        return (matcher or match_with_context)(candidates,request,scene,history)
    except Exception as exc:
        # Preserve the costly SAR result when an optional historical source fails.
        result,queue=historical.assess(candidates,history)
        code=str(exc) if isinstance(exc,WorkbenchError) else type(exc).__name__
        result['metadata']['optional_context_error']=code
        result['metadata']['gfw_context_state']='partial_or_failed'
        return result,queue


def run_task(request,cfg,workspace,search=None,detector=None,matcher=None,history=None):
    now=utc();ledger=workspace.setdefault('analysis_ledger',{})
    jobs=workspace.setdefault('jobs',[])
    if any(j.get('request_id')==request['id'] for j in jobs):raise WorkbenchError('REQUEST_ALREADY_COMPLETED')
    job={'request_id':request['id'],'created_at':iso(now),'action':request['action'],'request':request,
         'state':'error','assessment_limit':LIMIT,'analyst_review':{},'reviewed':False}
    if request['action']=='refresh_context':
        previous=next((j for j in jobs if j.get('scene',{}).get('scene_id')==request['scene_id'] and j.get('request',{}).get('bbox')==request['bbox'] and j.get('candidates')),None)
        if previous is None:raise WorkbenchError('NO_STORED_SCENE_FOR_CONTEXT')
        scene=previous['scene'];candidates=copy.deepcopy(previous['candidates'])
        result,queue=context_or_partial(candidates,request,scene,history if history is not None else read_history(),matcher)
        return {**job,'state':'ready','scene':scene,'candidates':candidates,'context':result,'queue':queue,'images':previous.get('images',{}),'processing_performed':False}
    found=(search or search_scenes)(request,cfg)
    job['catalog']=found
    if request['action']=='search':return {**job,'state':'catalog_ready','processing_performed':False}
    scene=next((s for s in found['scenes'] if s['processable'] and (request['scene_id']=='latest' or request['scene_id'] in {s['scene_id'],s['item_id']})),None)
    if scene is None:raise WorkbenchError('NO_SUITABLE_SCENE')
    job['scene']=scene
    date_key=now.date().isoformat()
    if ledger.get(date_key,0)>=cfg['max_analyses_per_day']:raise WorkbenchError('DAILY_ANALYSIS_LIMIT')
    if detector is None and not (os.environ.get('CDSE_SH_CLIENT_ID') and os.environ.get('CDSE_SH_CLIENT_SECRET')):raise WorkbenchError('COPERNICUS_CREDENTIALS_MISSING')
    ledger[date_key]=ledger.get(date_key,0)+1
    candidates,images=(detector or acquire_and_detect)(request,scene,cfg,os.environ.get('CDSE_SH_CLIENT_ID',''),os.environ.get('CDSE_SH_CLIENT_SECRET',''))
    result,queue=context_or_partial(candidates,request,scene,history if history is not None else read_history(),matcher)
    return {**job,'state':'ready','candidates':candidates,'context':result,'queue':queue,'images':images,'processing_performed':True}


def save_workspace(workspace,job,password,cfg,path=WORKSPACE):
    cutoff=utc()-timedelta(days=cfg['retention_days'])
    jobs=[job]+[j for j in workspace.get('jobs',[]) if j.get('request_id')!=job['request_id'] and catalog.parse_utc(j['created_at'],'job time')>=cutoff]
    workspace.update({'v':1,'updated_at':iso(utc()),'jobs':jobs[:cfg['max_jobs_retained']]})
    workspace['analysis_ledger']={k:v for k,v in workspace.get('analysis_ledger',{}).items() if k>=cutoff.date().isoformat()}
    while len(json.dumps(workspace,ensure_ascii=False).encode())>cfg['max_workspace_bytes'] and len(workspace['jobs'])>1:workspace['jobs'].pop()
    envelope=sar_crypto.encrypt(workspace,password,'workspace')
    catalog.atomic_json(path,envelope)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-config',action='store_true')
    args=parser.parse_args();cfg=load_config()
    if args.export_config:
        catalog.atomic_json(PUBLIC_CONFIG,{k:cfg[k] for k in ('schema_version','areas','max_area_km2','max_dimension_px','pixel_spacing_m','max_analyses_per_day')})
        return 0
    password=os.environ.get('SAR_WORKBENCH_PASSWORD','')
    try:
        sar_crypto.validate_password(password)
        encrypted=os.environ.get('SAR_ENCRYPTED_REQUEST','')
        if not encrypted or len(encrypted)>32000:raise WorkbenchError('ENCRYPTED_REQUEST_MISSING_OR_TOO_LARGE')
        request=validate_request(sar_crypto.decrypt(json.loads(encrypted),password,'request'),cfg)
        workspace=sar_crypto.decrypt(json.loads(WORKSPACE.read_text()),password,'workspace') if WORKSPACE.exists() else {'v':1,'jobs':[],'analysis_ledger':{}}
    except Exception:
        print('Invalid SAR password or encrypted request. No provider analysis was started.')
        return 1
    try:
        job=run_task(request,cfg,workspace)
        ok=True
    except Exception as exc:
        code=str(exc) if isinstance(exc,WorkbenchError) and re.fullmatch(r'[A-Z0-9_]+',str(exc)) else type(exc).__name__
        job={'request_id':request['id'],'created_at':iso(utc()),'action':request['action'],'request':request,'state':'error','error_code':code,'assessment_limit':LIMIT}
        ok=False
    save_workspace(workspace,job,password,cfg)
    print('SAR request complete; result stored encrypted.' if ok else 'SAR request failed; encrypted error details are available in the workbench.')
    return 0 if ok else 1


if __name__=='__main__':raise SystemExit(main())
