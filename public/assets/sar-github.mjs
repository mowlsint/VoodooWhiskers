// Optional direct dispatch. The access token exists only in this closure until lock/reload.
const API='https://api.github.com',REPO='/repos/mowlsint/VoodooWhiskers';
const WORKFLOW=REPO+'/actions/workflows/sar-workbench.yml';
export const WORKFLOW_URL='https://github.com/mowlsint/VoodooWhiskers/actions/workflows/sar-workbench.yml';
export function createGitHubClient(fetcher=(...args)=>fetch(...args)){
  let token='',generation=0,controller=new AbortController();
  async function api(path,{method='GET',body,credential=token}={}){
    if(!credential)throw new Error('Connect GitHub for this tab, or use the manual steps.');
    let response;
    try{response=await fetcher(API+path,{method,headers:{Accept:'application/vnd.github+json',Authorization:`Bearer ${credential}`,'X-GitHub-Api-Version':'2022-11-28',...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{}),credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer',signal:AbortSignal.any([controller.signal,AbortSignal.timeout(30000)])});}
    catch{throw new Error(method==='POST'?'GitHub did not confirm the start. Check workflow runs before trying again to avoid a duplicate request.':'GitHub could not be reached. Check the connection or use the manual steps.');}
    if(response.status===401)throw new Error('GitHub rejected the token. Check its value and expiry.');
    if(response.status===403)throw new Error('GitHub denied access. Use a token for VoodooWhiskers with Actions: Read and write; check any rate limit on GitHub.');
    if(response.status===404)throw new Error('The workflow or repository is not accessible with this token.');
    if(response.status===422)throw new Error('GitHub rejected the request. Update the workbench and workflow together on main, then create a fresh request.');
    if(!response.ok)throw new Error(`GitHub returned HTTP ${response.status}. Check the workflow before trying again.`);
    return response.status===204?null:response.json();
  }
  return {
    get connected(){return Boolean(token);},
    disconnect(){token='';generation++;controller.abort();controller=new AbortController();},
    async connect(value){
      token='';const version=++generation,candidate=String(value||'').trim();
      if(!/^github_pat_[A-Za-z0-9_]{20,255}$/.test(candidate))throw new Error('Use a fine-grained GitHub personal access token, starting with github_pat_.');
      const user=await api('/user',{credential:candidate});
      if(version!==generation)throw new Error('GitHub connection cancelled.');
      if(user.login?.toLowerCase()!=='mowlsint')throw new Error('This workflow is restricted to the repository owner account: mowlsint.');
      const workflow=await api(WORKFLOW,{credential:candidate});
      if(workflow.state!=='active')throw new Error('The SAR workflow is disabled. Open GitHub to review its status.');
      if(version!==generation)throw new Error('GitHub connection cancelled.');
      token=candidate;
    },
    async dispatch(envelope,id){
      if(!/^[a-f0-9-]{36}$/.test(id)||typeof envelope!=='string'||envelope.length>32000)throw new Error('Create a fresh encrypted request.');
      await api(WORKFLOW+'/dispatches',{method:'POST',body:{ref:'main',inputs:{encrypted_request:envelope,request_id:id}}});
    },
    async findRun(id){const data=await api(WORKFLOW+'/runs?event=workflow_dispatch&branch=main&per_page=30');return (data.workflow_runs||[]).find(run=>run.display_title===`SAR request ${id}`)||null;},
    async latestRun(){const data=await api(WORKFLOW+'/runs?event=workflow_dispatch&branch=main&status=completed&per_page=10');return data.workflow_runs?.[0]||null;},
    async run(id){if(!Number.isSafeInteger(id)||id<1)throw new Error('Invalid run.');return api(REPO+`/actions/runs/${id}`);},
    async artifactLink(runId){
      if(!Number.isSafeInteger(runId)||runId<1)throw new Error('Invalid run.');
      const data=await api(REPO+`/actions/runs/${runId}/artifacts?per_page=100`);
      const artifact=(data.artifacts||[]).find(a=>a.name==='sar-workbench-encrypted'&&!a.expired&&Number.isSafeInteger(a.id)&&a.id>0);
      return artifact?`https://github.com/mowlsint/VoodooWhiskers/actions/runs/${runId}/artifacts/${artifact.id}`:null;
    }
  };
}
