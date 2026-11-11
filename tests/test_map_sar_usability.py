"""Offline checks for map defaults, bounded area selection and authenticated dispatch."""
import json
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import sar_workbench as workbench


class Inputs(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.inputs = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == 'input':
            row = dict(attrs)
            self.inputs[row.get('data-layer') or row.get('id')] = row


def test_map_starts_with_priority_vessels_only():
    inputs = Inputs((ROOT / 'public/index.html').read_text()).inputs
    assert 'checked' in inputs['vessel_positions']
    for name in ['neutral_tanker_context', 'showTrafficContext', 'gfw_presence',
                 'gfw_loitering', 'infrastructure_events', 'showInfrastructure', 'showTracks']:
        assert 'checked' not in inputs[name]


def test_all_presets_fit_server_limits_and_public_config_is_synchronized():
    cfg = workbench.load_config()
    public = json.loads((ROOT / 'public/assets/sar-workbench-config.json').read_text())
    assert len(cfg['areas']) >= 38
    assert len({a['id'] for a in cfg['areas']}) == len(cfg['areas'])
    assert public == {key: cfg[key] for key in public}
    for area in cfg['areas']:
        request = {'v': 1, 'id': 'da16a93d-0af3-4b5c-92ee-832503756371',
                   'created_at': workbench.iso(workbench.utc()), 'action': 'search',
                   'area_id': area['id'], 'bbox': area['bbox']}
        parsed = workbench.validate_request(request, cfg)
        assert parsed['area_km2'] <= 500
    assert cfg['retention_days'] == 14
    assert cfg['max_analyses_per_day'] == 3
    assert cfg['max_jobs_retained'] == 6


def test_browser_area_selection_and_github_client_offline():
    script = r'''
import assert from 'node:assert/strict';
import {createGitHubClient} from './public/assets/sar-github.mjs';
import {bboxAroundPoint,inspectBbox} from './public/assets/sar-workbench-core.mjs';
const cfg=JSON.parse(process.argv[1]);
for(const [lat,lon] of [[54.6,12],[78.1,10.5],[51,1.4],[59.8,28.5]]) {
 const b=bboxAroundPoint(lat,lon,cfg),size=inspectBbox(b,cfg);
 assert(size.area<401&&size.widthPx<=2500&&size.heightPx<=2500);
}
assert.throws(()=>bboxAroundPoint(90,0,cfg));
assert.throws(()=>bboxAroundPoint(54,100,cfg));
const calls=[],token='github_pat_'+'a'.repeat(40),id='da16a93d-0af3-4b5c-92ee-832503756371';
let owner='mowlsint',deny=false;
const fetcher=async(url,options)=>{
 calls.push({url,options});
 assert.equal(new URL(url).origin,'https://api.github.com');
 assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');
 assert.equal(options.headers.Authorization,'Bearer '+token);
 if(deny)return new Response('',{status:403});
 let data=url.endsWith('/user')?{login:owner}:url.endsWith('sar-workbench.yml')?{state:'active'}:
 url.endsWith('/dispatches')?null:url.includes('/artifacts?')?{artifacts:[{id:12,name:'sar-workbench-encrypted',expired:false}]}:
 {workflow_runs:[{id:10,display_title:'SAR request OTHER'},{id:11,display_title:'SAR request '+id}]};
 return new Response(data===null?null:JSON.stringify(data),{status:data===null?204:200});
};
const client=createGitHubClient(fetcher);
await assert.rejects(()=>client.dispatch('{}',id),/Connect GitHub/);
await client.connect(token);assert(client.connected);
assert.equal(calls.filter(c=>c.options.method==='POST').length,0,'Connecting must not start a job');
await client.dispatch('{"v":1,"ciphertext":"encrypted-fixture"}',id);
const post=calls.find(c=>c.options.method==='POST'),body=JSON.parse(post.options.body);
assert(post.url.endsWith('/sar-workbench.yml/dispatches'));assert.equal(body.ref,'main');
assert.equal(body.inputs.request_id,id);assert.equal(body.inputs.encrypted_request,'{"v":1,"ciphertext":"encrypted-fixture"}');
assert.equal((await client.findRun(id)).id,11,'Never associate another request with this run');
assert.equal(await client.artifactLink(11),'https://github.com/mowlsint/VoodooWhiskers/actions/runs/11/artifacts/12');
deny=true;await assert.rejects(()=>client.dispatch('{}',id),/denied access/);deny=false;
client.disconnect();assert(!client.connected);
const after=calls.length;await assert.rejects(()=>client.dispatch('{}',id),/Connect GitHub/);assert.equal(calls.length,after);
owner='someone-else';await assert.rejects(()=>client.connect(token),/repository owner/);assert(!client.connected);
// Disconnect during authentication must not resurrect an authenticated session.
let complete;const slow=createGitHubClient(()=>new Promise(resolve=>{complete=()=>resolve(new Response('{"login":"mowlsint","state":"active"}'));}));
const connecting=slow.connect(token),cancelled=assert.rejects(()=>connecting,/cancelled/);
slow.disconnect();complete();await cancelled;assert(!slow.connected);
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script, json.dumps(workbench.load_config())],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
