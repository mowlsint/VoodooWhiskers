/* Historical grid cells are lookup leads, never resolved vessel identities. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.GfwGrid=api;})(globalThis,()=>{
  'use strict';
  const number=v=>(typeof v==='number'||typeof v==='string'&&v.trim()!=='')&&Number.isFinite(Number(v))?Number(v):NaN;
  const utc=t=>new Date(t).toISOString().replace('T',' ').replace('.000Z',' UTC');
  function details(row){
    const lat=number(row?.latitude),lon=number(row?.longitude),start=Date.parse(row?.interval_start||''),end=Date.parse(row?.interval_end||''),resolution=number(row?.spatial_resolution_degrees??.01);
    if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180||!Number.isFinite(start)||!Number.isFinite(end)||end<=start||!Number.isFinite(resolution)||resolution<=0||resolution>.1)return null;
    const half=resolution/2,bbox=[lon-half,lat-half,lon+half,lat+half].map(n=>Number(n.toFixed(6)));
    if(bbox[0]< -180||bbox[2]>180||bbox[1]< -90||bbox[3]>90)return null;
    const flag=row.flag==null?'Not supplied':String(row.flag).slice(0,80);
    const cell={lat,lon,start,end,resolution,bbox,flag,from:utc(start),to:utc(end)};
    cell.coordinateText=`${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    cell.bboxText=bbox.map(n=>n.toFixed(5)).join(', ');
    cell.lookupText=`Source: Global Fishing Watch historical AIS presence grid\nUTC from: ${cell.from}\nUTC to: ${cell.to}\nReported grid reference (approximate latitude, longitude): ${cell.coordinateText}\nDisplayed cell bounds (west, south, east, north): ${cell.bboxText}\nReported flag group: ${flag}\nNo individual vessel identity is supplied. Search the area and full interval, not just the reference point.`;
    return cell;
  }
  function age(cell,now=Date.now()){
    if(!Number.isFinite(cell?.start)||!Number.isFinite(cell?.end)||!Number.isFinite(now))return 'Age unknown';
    if(cell.start>now)return 'Future interval — check timestamp';
    if(cell.end>now)return 'Reporting interval still open';
    const minutes=Math.floor((now-cell.end)/60000),hours=Math.floor(minutes/60),days=Math.floor(hours/24);
    if(days)return `${days} d ${hours%24} h ago`;
    if(hours)return `${hours} h ${minutes%60} min ago`;
    return minutes?`${minutes} min ago`:'Less than 1 min ago';
  }
  function popup(cell){
    const host=document.createElement('div');host.className='gfwGridPopup';host.style.maxWidth='min(380px, 65vw)';
    const add=(tag,text)=>{const e=document.createElement(tag);e.textContent=text;host.append(e);return e;};
    add('strong','GFW · historical AIS presence');
    const facts=add('dl','');facts.className='popupMeta';
    for(const [name,value]of [['UTC from',cell.from],['UTC to',cell.to],['Age · interval ended',age(cell)],['Grid reference · latitude, longitude',cell.coordinateText],['Displayed bounds · W, S, E, N',cell.bboxText],['Reported flag group',cell.flag]]){
      const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value;dd.style.margin='0';dd.style.overflowWrap='anywhere';facts.append(dt,dd);
    }
    add('p','Use these coordinates and this UTC interval to look for possible vessels in another historical AIS source. Query the full cell area: this is an approximate grid reference, not an exact vessel position.');
    const field=add('textarea','');field.value=cell.lookupText;field.readOnly=true;field.rows=4;field.setAttribute('aria-label','Coordinates and UTC interval for historical AIS research');field.style.width='100%';field.style.boxSizing='border-box';
    const copy=add('button','Copy coordinates & UTC'),box=add('button','Copy bounding box');
    for(const button of [copy,box]){button.type='button';button.className='smallButton';}
    const feedback=add('p','');feedback.setAttribute('role','status');
    async function copyText(event,text){
      event.stopPropagation();
      try{if(!globalThis.navigator?.clipboard?.writeText)throw new Error();await navigator.clipboard.writeText(text);feedback.textContent='Copied. The data remain historical and do not identify a vessel.';}
      catch{field.value=text;field.focus();field.select();feedback.textContent='Select Copy on your device or press Ctrl+C to copy the selected text.';}
    }
    copy.addEventListener('click',event=>copyText(event,cell.lookupText));box.addEventListener('click',event=>copyText(event,cell.bboxText));
    add('p','No name, MMSI or IMO is provided by this grid. Several vessels may contribute to a cell; an external match remains a research hypothesis.');
    return host;
  }
  function tooltip(cell){return `GFW historical AIS · ${cell.from} to ${cell.to} · interval ended: ${age(cell)} · click for coordinates`;}
  return {details,age,popup,tooltip};
});
