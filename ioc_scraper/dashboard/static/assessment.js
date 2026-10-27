/* Observation comparisons are evidence aids, never automatic attribution. */
(function () {
  'use strict';
  const t=key=>(window.ZH_I18N[document.documentElement.lang] || window.ZH_I18N.en)[key] || window.ZH_I18N.en[key] || key;
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  function contents(assessment) {
    const details=node('div',null,'assessment-body');
    details.append(node('p',t('assessmentCaution'),'muted'));
    details.append(node('p',t('assessmentObserved')+': '+(assessment.source_observed_at || t('assessmentUnknown')),'muted'));
    const counts=assessment.counts || {};
    details.append(node('p',['match','different','missing'].map(k=>t('assessment_'+k)+': '+(counts[k] || 0)).join(' · '),'muted'));
    if(assessment.truncated)details.append(node('p',t('assessmentTruncated'),'hunt-notice'));
    if(!assessment.rows.length){details.append(node('p',t('assessmentEmpty'),'muted'));return details;}
    const scroll=node('div',null,'assessment-scroll');scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label',t('assessmentTitle'));
    const table=node('table');const head=node('thead');const titles=node('tr');
    ['assessmentSeed','assessmentField','assessmentSeedValue','assessmentCandidateValue','assessmentRelation','assessmentRetrieved'].forEach(k=>{const th=node('th',t(k));th.scope='col';titles.append(th);});head.append(titles);table.append(head);
    const body=node('tbody');
    assessment.rows.forEach(row=>{
      const tr=node('tr');tr.dataset.relation=row.relation;
      const cell=node('td');const link=node('button',row.seed_content,'assessment-seed-link');link.type='button';
      link.addEventListener('click',()=>{const el=Array.from(document.querySelectorAll('.hunt-seed-evidence')).find(n=>n.dataset.seedId===row.seed_id);if(el){el.closest('details').open=true;el.scrollIntoView({block:'center'});el.focus({preventScroll:true});}});
      cell.append(link,node('small',row.seed_asset_id || '','muted'));tr.append(cell);
      tr.append(node('td',row.field),node('td',row.seed_value==null?t('assessmentUnknown'):row.seed_value),node('td',row.candidate_value==null?t('assessmentUnknown'):row.candidate_value));
      tr.append(node('td',t('assessment_'+row.relation)+(row.generic?' · '+t('assessmentGeneric'):'')),node('td',row.retrieved_at || t('assessmentUnknown')));body.append(tr);
    });table.append(body);scroll.append(table);details.append(scroll);return details;
  }
  window.ZH_ASSESSMENT={render(candidate,taskId){
    const details=node('details',null,'hunt-assessment');
    details.append(node('summary',t('assessmentTitle')));
    let loaded=false,loading=false;
    async function load() {
      if(loaded || loading)return;
      loading=true;
      const body=node('div',t('assessmentLoading'),'assessment-body');
      const old=details.querySelector('.assessment-body');if(old)old.remove();details.append(body);
      try {
        let assessment=candidate.assessment || {rows:[],counts:{},source_observed_at:null};
        if(assessment.deferred){
          const response=await fetch('/api/hunts/'+encodeURIComponent(taskId)+'/candidates/'+encodeURIComponent(candidate.id)+'/assessment');
          const data=await response.json();
          if(!response.ok)throw new Error(data.message || t('failedRequest'));
          assessment=data;
        }
        body.replaceWith(contents(assessment));loaded=true;
      } catch(e) {
        body.replaceChildren(node('p',e.message || t('failedRequest'),'hunt-notice'));
        const retry=node('button',t('assessmentRetry'),'chip');retry.type='button';retry.addEventListener('click',load);body.append(retry);
      } finally {loading=false;}
    }
    details.addEventListener('toggle',()=>{if(details.open)load();});
    return details;
  }};
})();
