(function () {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const state = { seeds: [], selected: new Set(), config: {}, tasks: [], task: null, id: null, timer: null, loading: false, generation: 0, refreshGeneration: 0, detailGeneration: 0, candidatePage: 0, detailText: '' };
  const activeStates = new Set(['queued', 'running', 'stopping']);
  const visible = () => location.hash === '#/hunting';
  const lang = () => document.documentElement.lang === 'zh-CN' ? 'zh-CN' : 'en';
  const t = (key) => (window.ZH_I18N[lang()] || {})[key] || window.ZH_I18N.en[key] || key;
  const str = (v) => typeof v === 'string' ? v : JSON.stringify(v, null, 2);
  function node(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
  function button(text, callback, cls) { const b = node('button', text, cls || 'chip'); b.type = 'button'; b.addEventListener('click', callback); return b; }
  function message(text) { $('#hunt-error').textContent = text || ''; $('#hunt-error').hidden = !text; }
  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || str(data.detail) || t('failedRequest'));
    return data;
  }
  async function action(work, trigger) {
    if (trigger) trigger.disabled = true;
    message('');
    try { await work(); } catch (e) { message(e.message || t('failedRequest')); }
    finally { if (trigger) trigger.disabled = false; updateControls(); }
  }
  function taskState(task) { return t('hunt_' + task.state); }
  function updateControls() {
    $('#hunt-selected').textContent = state.selected.size + ' ' + t('huntSelected');
    const busy = state.tasks.some(task => activeStates.has(task.state)) || (state.task && activeStates.has(state.task.state));
    $('#hunt-start').disabled = !state.config.fofa_configured || !state.selected.size || busy || state.loading;
    $('#hunt-demo').disabled = busy || state.loading;
    $('#hunt-import').disabled = busy || state.loading;
    $('#hunt-llm').disabled = !state.config.deepseek_configured;
    if (!state.config.deepseek_configured) $('#hunt-llm').checked = false;
    $('#hunt-config').textContent = 'FOFA: ' + t(state.config.fofa_configured ? 'huntReady' : 'huntMissing') + ' · DeepSeek: ' + t(state.config.deepseek_configured ? 'huntReady' : 'huntMissing') + (state.config.deepseek_model ? ' (' + state.config.deepseek_model + ')' : '');
  }
  function filteredSeeds() {
    const term = $('#hunt-filter').value.trim().toLowerCase();
    return state.seeds.filter(seed => [seed.content,seed.report_title,seed.threat_actor,seed.indicator_role,seed.published_at].join(' ').toLowerCase().includes(term));
  }
  function renderSeeds() {
    const wrap = $('#hunt-seeds'); wrap.replaceChildren();
    const seeds = filteredSeeds();
    if (!seeds.length) wrap.append(node('p', t('huntNoSeeds'), 'hunt-empty'));
    seeds.forEach(seed => {
      const row = node('div', null, 'hunt-seed');
      const label = node('label', null, 'hunt-check'); const check = node('input'); check.type = 'checkbox'; check.checked = state.selected.has(seed.id);
      check.addEventListener('change', () => {
        if (check.checked && state.selected.size >= 20) { check.checked = false; message(t('huntSeedLimit')); return; }
        if (check.checked) state.selected.add(seed.id); else state.selected.delete(seed.id);
        updateControls();
      });
      label.append(check, node('strong', seed.content));
      row.append(label, node('small', [seed.port ? 'port '+seed.port : '', seed.report_title, seed.threat_actor, seed.indicator_role, seed.published_at].filter(Boolean).join(' · ')));
      wrap.append(row);
    });
    updateControls();
  }
  function renderHistory() {
    const wrap = $('#hunt-history'); wrap.replaceChildren();
    if (!state.tasks.length) wrap.append(node('p',t('huntNoTasks'),'hunt-empty'));
    state.tasks.forEach(task => {
      const b = button('', () => selectTask(task.id), 'hunt-task' + (task.id === state.id ? ' active' : ''));
      b.append(node('strong', task.name || task.id), node('span',taskState(task) + ' · ' + (task.mode === 'fofa' ? t('huntLive') : t('huntOffline'))), node('span',task.created_at));
      wrap.append(b);
    });
    updateControls();
  }
  function disclosure(label, content) {
    const d = node('details'); d.append(node('summary',label),node('pre',str(content))); return d;
  }
  function decisionLabel(value) { return t({related:'huntRelated',unrelated:'huntUnrelated',uncertain:'huntUncertain'}[value] || 'huntPending'); }
  function renderCandidate(candidate, task) {
    const card = node('article',null,'hunt-candidate'); card.dataset.decision = candidate.decision || 'pending';
    const asset = candidate.asset || {};
    card.append(node('h3',[asset.host || asset.domain || asset.ip || candidate.id, asset.port].filter(Boolean).join(' · ')));
    card.append(node('p', t('huntDecision') + ': ' + decisionLabel(candidate.decision), 'muted'));
    card.append(disclosure(t('huntReasons'),candidate.reasons || []),disclosure(t('huntAsset'),asset));
    card.append(window.ZH_ASSESSMENT.render(candidate,task.id));
    const form = node('div',null,'hunt-feedback'); const choices = node('div',null,'hunt-actions');
    let choice = candidate.decision || '';
    ['related','unrelated','uncertain'].forEach(value => {
      const b = button(decisionLabel(value), () => { choice = value; choices.querySelectorAll('button').forEach(btn => { btn.classList.toggle('active',btn.dataset.feedback === choice); btn.setAttribute('aria-pressed',String(btn.dataset.feedback === choice)); }); });
      b.dataset.feedback = value; b.classList.toggle('active', choice === value); b.setAttribute('aria-pressed',String(choice === value)); choices.append(b);
    });
    const actorLabel = node('label', t('huntAttribution')); const actor = node('input',null,'hunt-attribution'); actor.value = candidate.attribution || ''; actor.maxLength = 500; actorLabel.append(actor);
    const noteLabel = node('label',t('huntNote')); const note = node('textarea',null,'hunt-note'); note.value = candidate.note || ''; note.maxLength = 4000; note.rows = 2; noteLabel.append(note);
    const save = button(t('huntSave'), () => action(async () => {
      if (!choice) throw new Error(t('huntChooseDecision'));
      await api('/api/hunts/' + encodeURIComponent(task.id) + '/feedback', {candidate_id:candidate.id,decision:choice,attribution:actor.value,note:note.value});
      await loadDetail(task.id, true);
    },save),'primary hunt-save');
    save.disabled = activeStates.has(task.state);
    form.append(choices,actorLabel,noteLabel,save); card.append(form); return card;
  }
  function renderDetail(force) {
    const task = state.task; const wrap = $('#hunt-detail');
    const serialized = JSON.stringify(task) + lang();
    if (serialized === state.detailText) return;
    state.detailText = serialized; wrap.replaceChildren();
    if (!task) { wrap.append(node('p',t('huntSelectTask'),'hunt-empty')); return; }
    wrap.dataset.taskId = task.id; wrap.dataset.state = task.state; wrap.dataset.mode = task.mode;
    const head = node('div',null,'panel-head'); const actions = node('div',null,'hunt-actions'); head.append(node('h2',task.name || task.id));
    const stop = button(t('huntStop'),() => action(async () => { await api('/api/hunts/' + encodeURIComponent(task.id) + '/stop',{}); await loadDetail(task.id,true); },stop),'danger'); stop.id = 'hunt-stop'; stop.disabled = !activeStates.has(task.state);
    const replay = button(t('huntReplay'),() => start('/api/hunts/' + encodeURIComponent(task.id) + '/replay',{},replay)); replay.id = 'hunt-replay'; replay.disabled = activeStates.has(task.state);
    const evaluate = button(t('huntEvaluate'),() => start('/api/hunts/' + encodeURIComponent(task.id) + '/evaluate',{},evaluate)); evaluate.id='hunt-evaluate'; evaluate.disabled=activeStates.has(task.state);
    const exportButton = button(t('huntExport'),() => action(async () => {
      const snapshot = await api('/api/hunts/' + encodeURIComponent(task.id) + '/export');
      const url = URL.createObjectURL(new Blob([JSON.stringify(snapshot,null,2)],{type:'application/json'}));
      const a = node('a'); a.href=url; a.download='hunt-' + task.id + '.json'; document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
    },exportButton)); exportButton.id = 'hunt-export'; exportButton.disabled = activeStates.has(task.state);
    actions.append(stop,replay,evaluate,exportButton); head.append(actions); wrap.append(head);
    if (task.synthetic) wrap.append(node('p',t('huntSynthetic'),'hunt-notice'));
    wrap.append(node('p',t('huntRankingNote'),'muted'));
    const summary = node('dl',null,'hunt-summary'); const metrics = task.metrics || {};
    [[t('state'),taskState(task)],[t('huntCalls'),metrics.api_calls || 0],[t('huntCache'),metrics.cache_hits || 0],[t('huntCandidateCount'),(task.candidates || []).length]].forEach(([key,value]) => {const d=node('div');d.append(node('dt',key),node('dd',value));summary.append(d);});
    wrap.append(summary);
    if (task.error) wrap.append(node('p',str(task.error),'hunt-notice'));
    const warningKeys = {
      'Query budget exhausted; evidence and coverage are partial': 'huntBudgetSpent',
      'DeepSeek unavailable or invalid evidence; deterministic fallback used': 'huntLLMFallback',
      'Replayed all recorded queries at the original partial execution boundary': 'huntPartialReplay'
    };
    (task.warnings || []).filter(w => !(metrics.budget_exhausted && warningKeys[w] === 'huntBudgetSpent')).forEach(w => wrap.append(node('p',warningKeys[w] ? t(warningKeys[w]) : str(w),'hunt-notice')));
    if (metrics.budget_exhausted) wrap.append(node('p',t('huntBudgetSpent'),'hunt-notice'));
    if(task.evaluation)wrap.append(node('p',t('huntEvaluationNote'),'hunt-notice'));
    if(task.policy){
      const p=task.policy;
      const summary=node('p',t('huntPolicyCounts')+': '+(p.skipped || []).length+' / '+(p.rejected || []).length+' / '+(p.offline_missing || []).length,'hunt-notice');summary.id='hunt-policy-summary';wrap.append(summary);
      if(p.stop_reason)wrap.append(node('p',t('policy_'+p.stop_reason),'hunt-notice'));
      if((p.offline_missing || []).length)wrap.append(node('p',t('huntOfflineMissing'),'hunt-notice'));
      const detail=node('details',null,'hunt-policy');detail.append(node('summary',t('huntPolicyAudit')));
      detail.addEventListener('toggle',()=>{if(!detail.open || detail.dataset.loaded)return;detail.dataset.loaded='1';
        [...(p.rejected || []),...(p.skipped || []),...(p.offline_missing || [])].forEach(r=>{const row=node('div',null,'hunt-rule');row.append(node('code',r.query),node('p',t('policy_'+r.reason)+(r.total!=null?' · '+t('huntTotal')+': '+r.total:'')));detail.append(row);});
      });wrap.append(detail);
    }
    const seeds = node('details',null,'hunt-evidence'); seeds.append(node('summary',t('huntSeeds')));
    (task.seeds || []).forEach(seed => {
      const entry=node('article',null,'hunt-seed-evidence');entry.dataset.seedId=seed.id;entry.tabIndex=-1;
      entry.append(node('p',[seed.content,seed.port,seed.report_title,seed.threat_actor,seed.published_at].filter(Boolean).join(' · ')));
      entry.append(node('p',seed.source || ''),node('pre',seed.evidence || ''));
      if(seed.qualification){const q=seed.qualification;entry.append(node('p',[t('seed_'+q.status),t('seed_'+q.value),q.reviewer,q.reviewed_at].filter(Boolean).join(' · ')),node('p',q.rationale || ''));}
      try { const url = new URL(seed.report_url); if (['http:','https:'].includes(url.protocol)) { const a=node('a',t('openOriginal')); a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';entry.append(a); } } catch (_) {}
      seeds.append(entry);
    }); wrap.append(seeds);
    const rules = node('details'); rules.append(node('summary',t('huntRules') + ' · ' + (task.rules || []).length));
    (task.rules || []).forEach(rule => {
      const row=node('div',null,'hunt-rule'); row.append(node('code',rule.query));
      const coverage=typeof rule.coverage === 'number' ? Math.round(rule.coverage*100)+'%' : str(rule.coverage);
      row.append(node('p',t('huntCoverage')+': '+coverage+' · '+t('huntNovel')+': '+rule.new_count+' · '+t('huntTotal')+': '+rule.total+' · '+t('huntScore')+': '+rule.score+(rule.partial ? ' · '+t('huntPartialCoverage') : ''))); rules.append(row);
      if((task.policy?.rejected || []).some(r=>r.query===rule.query))row.append(node('p',t('policy_broad_result'),'hunt-notice'));
    });
    wrap.append(rules,disclosure(t('huntFeatures'),task.features || []),disclosure(t('huntQueries'),task.queries || []),disclosure(t('huntFeedback'),task.feedback || []));
    wrap.append(node('h2',t('huntCandidates')));
    if (!(task.candidates || []).length) wrap.append(node('p',t('huntNoCandidates'),'hunt-empty'));
    const items=task.candidates || [];const pageSize=25;
    state.candidatePage=Math.min(state.candidatePage,Math.max(0,Math.ceil(items.length/pageSize)-1));
    const pager=node('div',null,'hunt-actions');const candidates=node('div',null,'hunt-candidates');
    const previous=button(t('assessmentPrev'),()=>{state.candidatePage--;renderPage();});previous.id='hunt-candidates-prev';
    const next=button(t('assessmentNext'),()=>{state.candidatePage++;renderPage();});next.id='hunt-candidates-next';
    const count=node('span',null,'muted');count.id='hunt-candidate-page';
    pager.append(previous,count,next);if(items.length)wrap.append(pager);wrap.append(candidates);
    function renderPage(){
      const start=state.candidatePage*pageSize;
      candidates.replaceChildren();items.slice(start,start+pageSize).forEach(c=>candidates.append(renderCandidate(c,task)));
      previous.disabled=state.candidatePage===0;next.disabled=start+pageSize>=items.length;
      count.textContent=(items.length?start+1:0)+'–'+Math.min(start+pageSize,items.length)+' / '+items.length;
    }
    renderPage();
  }
  async function loadDetail(id, force) {
    const generation = state.generation;
    const detailGeneration = ++state.detailGeneration;
    const task = await api('/api/hunts/' + encodeURIComponent(id));
    if (state.id !== id || generation !== state.generation || detailGeneration !== state.detailGeneration) return;
    state.task = task;
    state.tasks = state.tasks.map(summary => summary.id === task.id ? {...summary, ...task} : summary);
    renderDetail(force); renderHistory(); schedule();
  }
  async function selectTask(id) {
    state.id=id; state.generation++; state.candidatePage=0; state.task=null; state.detailText=''; renderDetail(); renderHistory();
    await action(() => loadDetail(id,true));
  }
  async function refresh() {
    const generation=++state.refreshGeneration;
    await action(async () => {
      const [config,seeds,tasks] = await Promise.all([api('/api/hunting/config'),api('/api/hunting/seeds'),api('/api/hunts')]);
      if(generation!==state.refreshGeneration)return;
      state.config=config;state.seeds=(seeds.items || []).filter(s=>s.eligible);state.tasks=tasks.items || [];
      const available=new Set(state.seeds.map(s => s.id));state.selected.forEach(id => {if(!available.has(id))state.selected.delete(id);});
      renderSeeds();renderHistory();
      if (!state.id && state.tasks.length) await selectTask(state.tasks[0].id);
      else if (state.id) await loadDetail(state.id,false);
      else renderDetail();
    }); schedule();
  }
  function schedule() {
    clearTimeout(state.timer);
    if (visible() && ((state.task && activeStates.has(state.task.state)) || state.tasks.some(task => activeStates.has(task.state)))) {
      state.timer = setTimeout(async () => {
        await action(async () => { const tasks=await api('/api/hunts');state.tasks=tasks.items || [];renderHistory();if(state.id)await loadDetail(state.id,false); });
        schedule();
      },1200);
    }
  }
  async function start(path,body,trigger) {
    state.loading=true;updateControls();
    await action(async () => {const result=await api(path,body);state.id=result.id;state.generation++;state.candidatePage=0;state.task=null;state.detailText='';await refresh();},trigger);
    state.loading=false;updateControls();schedule();
  }
  $('#hunt-demo').addEventListener('click', () => start('/api/hunts/demo',{},$('#hunt-demo')));
  $('#hunt-form').addEventListener('submit', e => {
    e.preventDefault(); if (!state.config.fofa_configured) {message(t('huntNeedsKeys'));return;}
    start('/api/hunts',{name:$('#hunt-name').value,context:$('#hunt-context').value,mode:'fofa',seed_ids:Array.from(state.selected),use_llm:$('#hunt-llm').checked,query_budget:Number($('#hunt-budget').value),max_results:Number($('#hunt-max-results').value)},$('#hunt-start'));
  });
  $('#hunt-filter').addEventListener('input',renderSeeds);
  $('#hunt-select-visible').addEventListener('click',() => {filteredSeeds().slice(0,20).forEach(s => {if(state.selected.size<20)state.selected.add(s.id);});renderSeeds();});
  $('#hunt-clear').addEventListener('click',() => {state.selected.clear();renderSeeds();});
  $('#hunt-refresh').addEventListener('click',refresh);
  window.addEventListener('zh-seeds-changed',()=>{if(visible())refresh();});
  $('#hunt-import').addEventListener('change',async e => {
    const file=e.target.files[0]; if(!file)return;
    let snapshot;
    try { snapshot=JSON.parse(await file.text()); } catch (_) { message(t('huntBadFile'));e.target.value='';return; }
    await start('/api/hunts',{name:file.name.replace(/\.json$/i,''),mode:'replay',seed_ids:[],snapshot,use_llm:false},null); e.target.value='';
  });
  window.addEventListener('hashchange',() => {if(visible())refresh();else clearTimeout(state.timer);});
  new MutationObserver(() => {renderSeeds();renderHistory();renderDetail(true);}).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  if (visible()) refresh();
})();
