/* Independent analyst seed library. Uploaded data never carries qualification. */
(function () {
  'use strict';
  const $ = s => document.querySelector(s);
  const t = key => (window.ZH_I18N[document.documentElement.lang] || window.ZH_I18N.en)[key] || window.ZH_I18N.en[key] || key;
  const state = {items:[], selected:null, revision:null, generation:0, busy:false};
  const visible = () => location.hash === '#/hunting';
  const node = (tag,text,cls) => {const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  function notice(id,text) { $(id).textContent=text || '';$(id).hidden=!text; }
  async function api(path,body) {
    const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await r.json();
    if(!r.ok) throw new Error((r.status===409?t('seedConflict')+' ':'')+(data.message || t('failedRequest')));
    return data;
  }
  function controls() {
    $('#seed-add').disabled=state.busy;$('#seed-upload').disabled=state.busy;
    $('#seed-review-fields').disabled=state.busy;$('#seed-refresh').disabled=state.busy;
    $('#seed-load-collector').disabled=state.busy;
    $('#seed-transfer').disabled=state.busy || !$('#seed-collector-list input:checked');
    document.querySelectorAll('.seed-edit').forEach(b=>{b.disabled=state.busy;});
  }
  async function action(work) {
    if(state.busy)return;
    state.busy=true;controls();notice('#seed-error','');notice('#seed-message','');
    try { await work(); } catch(e) {notice('#seed-error',e.message || t('failedRequest'));}
    finally {state.busy=false;controls();}
  }
  function history(seed) {
    const wrap=$('#seed-history-list');wrap.replaceChildren();
    (seed.history || []).forEach(r=>{
      const row=node('article',null,'seed-audit');
      row.append(node('strong',[t('seed_'+r.status),t('seed_'+r.value),r.reviewer,r.reviewed_at].filter(Boolean).join(' · ')));
      row.append(node('p',r.rationale),node('p',t('seedSource')+': '+r.source),node('p',t('seedEvidence')+': '+r.evidence));
      if(r.previous_source!==undefined || r.previous_evidence!==undefined) {
        const prev=node('details');prev.append(node('summary',t('seedPrevious')),node('p',r.previous_source || '—'),node('p',r.previous_evidence || '—'));row.append(prev);
      }
      wrap.append(row);
    });
    if(!wrap.children.length)wrap.append(node('p',t('seedNoHistory'),'muted'));
  }
  function edit(seed) {
    state.selected=seed.id;state.revision=seed.qualification.revision;
    $('#seed-review-form').hidden=false;$('#seed-review-title').textContent=seed.content+(seed.port?' · '+seed.port:'');
    $('#seed-status').value=seed.qualification.status;$('#seed-value').value=seed.qualification.value;
    $('#seed-reviewer').value=seed.qualification.reviewer || '';
    $('#seed-rationale').value=''; // Require a reason for this decision, not the previous one.
    $('#seed-review-source').value=seed.source || '';$('#seed-review-evidence').value=seed.evidence || '';
    history(seed);controls();
  }
  function render() {
    const wrap=$('#seed-list');wrap.replaceChildren();
    const filter=$('#seed-filter').value.trim().toLowerCase();
    state.items.filter(s=>[s.content,s.source,s.report_title,s.qualification.status,s.qualification.value].join(' ').toLowerCase().includes(filter)).forEach(seed=>{
      const q=seed.qualification;const row=node('article',null,'seed-record');
      row.dataset.id=seed.id;row.dataset.status=q.status;row.dataset.value=q.value;
      row.append(node('strong',seed.content+(seed.port?' · '+seed.port:'')));
      row.append(node('p',t('seed_'+q.status)+' · '+t('seed_'+q.value)+(seed.eligible?' · '+t('seedEligible'):''),'muted'));
      if(seed.source)row.append(node('p',seed.source,'muted'));
      const b=node('button',t('seedEdit'),'chip seed-edit');b.type='button';b.addEventListener('click',()=>{edit(seed);$('#seed-review-form').scrollIntoView({block:'nearest'});});row.append(b);wrap.append(row);
    });
    if(!wrap.children.length)wrap.append(node('p',t('seedEmpty'),'muted'));
    controls();
  }
  async function refresh(reopen=false) {
    const generation=++state.generation;
    const result=await api('/api/hunting/seeds');
    if(generation!==state.generation)return;
    state.items=result.items;render();
    if(reopen && state.selected){const seed=state.items.find(s=>s.id===state.selected);if(seed)edit(seed);}
    window.dispatchEvent(new Event('zh-seeds-changed'));
  }
  async function imported(body) {
    const result=await api('/api/hunting/seeds/import',body);
    await refresh();
    notice('#seed-message',t('seedImported')+' '+result.items.length+' · '+t('seedDuplicates')+' '+(result.duplicates || 0));
  }
  $('#seed-import-form').addEventListener('submit',e=>{e.preventDefault();action(async()=>{
    await imported({format:'text',text:$('#seed-text').value,source:$('#seed-source').value,evidence:$('#seed-evidence').value});$('#seed-text').value='';
  });});
  $('#seed-upload').addEventListener('change',e=>action(async()=>{
    const file=e.target.files[0];if(!file)return;
    try {
      if(file.size>2*1024*1024)throw new Error(t('seedTooLarge'));
      const text=await file.text();
      if(file.name.toLowerCase().endsWith('.json')) {
        let value;try{value=JSON.parse(text);}catch(_){throw new Error(t('seedBadFile'));}
        await imported(Array.isArray(value)?{items:value}:value);
      } else {
        await imported({format:file.name.toLowerCase().endsWith('.csv')?'csv':'text',text,source:$('#seed-source').value,evidence:$('#seed-evidence').value});
      }
    } finally {e.target.value='';}
  }));
  $('#seed-review-form').addEventListener('submit',e=>{e.preventDefault();action(async()=>{
    if(!state.selected)return;
    const seed=await api('/api/hunting/seeds/'+encodeURIComponent(state.selected)+'/review',{
      revision:state.revision,status:$('#seed-status').value,value:$('#seed-value').value,reviewer:$('#seed-reviewer').value,rationale:$('#seed-rationale').value,source:$('#seed-review-source').value,evidence:$('#seed-review-evidence').value
    });
    edit(seed);await refresh();notice('#seed-message',t('seedSaved'));
  });});
  $('#seed-close-review').addEventListener('click',()=>{state.selected=null;$('#seed-review-form').hidden=true;});
  $('#seed-reload-review').addEventListener('click',()=>action(()=>refresh(true)));
  $('#seed-refresh').addEventListener('click',()=>action(()=>refresh()));
  $('#seed-filter').addEventListener('input',render);
  $('#seed-load-collector').addEventListener('click',()=>action(async()=>{
    const result=await api('/api/hunting/collector-seeds');const list=$('#seed-collector-list');list.replaceChildren();
    result.items.forEach(seed=>{const label=node('label',null,'hunt-check');const check=node('input');check.type='checkbox';check.value=seed.id;check.addEventListener('change',controls);label.append(check,node('span',[seed.content,seed.port,seed.report_title].filter(Boolean).join(' · ')));list.append(label);});
    if(!result.items.length)list.append(node('p',t('seedCollectorEmpty'),'muted'));
  }));
  $('#seed-transfer').addEventListener('click',()=>action(async()=>{
    const seed_ids=Array.from(document.querySelectorAll('#seed-collector-list input:checked')).map(n=>n.value);
    const result=await api('/api/hunting/seeds/from-collector',{seed_ids});await refresh();
    notice('#seed-message',t('seedImported')+' '+result.items.length+' · '+t('seedDuplicates')+' '+(result.duplicates || 0));
    document.querySelectorAll('#seed-collector-list input').forEach(n=>{n.checked=false;});
  }));
  window.addEventListener('hashchange',()=>{if(visible())action(()=>refresh());});
  new MutationObserver(()=>{render();if(state.selected){const seed=state.items.find(s=>s.id===state.selected);if(seed)history(seed);}}).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  if(visible())action(()=>refresh());
})();
