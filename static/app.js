'use strict';
const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time = m => `${String(Math.floor(m / 60)).padStart(2,'0')}:${String(m % 60).padStart(2,'0')}`;
const minutes = value => { const [h,m] = value.split(':').map(Number); return h*60+m; };
const codes = value => value.split(/[,;\n]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
const colors = [ ['#e3edda','#466a3a'], ['#e9e4f2','#79628b'], ['#f4e7d5','#9a7241'], ['#dae9ec','#477b85'], ['#f1dfe1','#9d6570'], ['#e5e7cc','#6e733f'] ];
let controller = null, result = null, familyIndex = 0, variantIndex = 0, trace = [];

function error(message) { $('form-error').textContent = message; $('form-error').hidden = !message; }
function detailMessage(body) { return typeof body.detail === 'string' ? body.detail : (body.detail || []).map(e => `${e.field || ''}: ${e.message || e.msg}`).join('; '); }
function addCourse(course = {code:'',grade:'P',status:'completed'}) {
  const row = document.createElement('div'); row.className = 'transcript-row';
  row.innerHTML = `<input aria-label="Completed course code" placeholder="CS 1110" value="${escape(course.code)}"><select aria-label="Course grade">${['A+','A','A-','B+','B','B-','C+','C','C-','D+','D','D-','F','P','S','U','W','I','UNKNOWN'].map(g=>`<option${g===(course.grade || 'UNKNOWN')?' selected':''}>${g}</option>`).join('')}</select><select aria-label="Completion status">${[['completed','Done'],['in_progress','In progress'],['unknown','Unknown']].map(([v,n])=>`<option value="${v}"${v===course.status?' selected':''}>${n}</option>`).join('')}</select><button type="button" class="remove" aria-label="Remove course">×</button>`;
  row.querySelector('button').addEventListener('click',()=>{row.remove();updateTotal();});
  row.addEventListener('change',updateTotal); $('transcript-rows').append(row); updateTotal();
}
function getCourses() { return [...$('transcript-rows').children].map(row=>({code:row.children[0].value.trim().toUpperCase(),grade:row.children[1].value,status:row.children[2].value})).filter(c=>c.code); }
function updateTotal() { const n = getCourses().filter(c=>c.status==='completed').length; $('course-total').textContent=`${n} completed course${n===1?'':'s'}`; }
function addBlock() {
  const row=document.createElement('div');row.className='blocked-row';
  row.innerHTML=`<select aria-label="Unavailable day">${[['M','Mon'],['T','Tue'],['W','Wed'],['R','Thu'],['F','Fri'],['S','Sat'],['U','Sun']].map(([v,n])=>`<option value="${v}">${n}</option>`).join('')}</select><input type="time" value="12:00" aria-label="Unavailable start"><input type="time" value="13:00" aria-label="Unavailable end"><button type="button" class="remove" aria-label="Remove time">×</button>`;
  row.querySelector('button').addEventListener('click',()=>row.remove());$('blocked-rows').append(row);
}
function profile(demo) {
  return {api_key: demo?'':$('api-key').value.trim(),model:$('model').value,goal:$('goal').value,
    roster:$('roster').value,career:$('career').value,campus:$('campus').value,completed:getCourses(),
    min_credits:Number($('min-credits').value),max_credits:Number($('max-credits').value),course_count:Number($('course-count').value),
    earliest:minutes($('earliest').value),latest:minutes($('latest').value),
    blocked:[...$('blocked-rows').children].map(row=>({day:row.children[0].value,start:minutes(row.children[1].value),end:minutes(row.children[2].value)})),
    required:codes($('required').value),confirmed_permissions:codes($('permissions').value),subject_hints:codes($('subjects').value),demo};
}
function busy(value) {
  document.querySelectorAll('#planner-form input,#planner-form select,#planner-form textarea,#planner-form button,#demo-top').forEach(el=>el.disabled=value);
  $('generate').innerHTML=value?'Finding a semester that fits…':'Find my course plans <span>→</span>';
  $('cancel').hidden=!value;
}
function appendEvent(event) {
  trace.push(event); const li=document.createElement('li');li.textContent=event.message;$('events').append(li);li.scrollIntoView({block:'nearest'});
}
async function run(demo=false) {
  if (controller) return;
  error('');
  if (!$('planner-form').reportValidity()) return;
  if (!$('reviewed').checked) return error('Review the extracted course codes, grades and completion statuses, then check the confirmation box.');
  if (!demo && !$('api-key').value.trim()) { $('api-section').open=true;error('Enter your OpenAI API key, or try the offline demo.');$('api-key').focus();return; }
  controller = new AbortController(); const signal=controller.signal; busy(true);
  $('empty-state').hidden=true;$('results').hidden=true;$('activity').hidden=false;$('events').replaceChildren();trace=[];
  $('demo-banner').hidden=!demo;$('run-state').textContent='AGENT AT WORK';$('results-title').textContent='Finding your possibilities…';
  try {
    const response=await fetch('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(profile(demo)),signal});
    if(!response.ok) throw new Error(detailMessage(await response.json()) || `Request failed (${response.status}).`);
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',received=false;
    const handle=line=>{if(!line.trim())return;const item=JSON.parse(line);if(item.type==='event')appendEvent(item);if(item.type==='error')throw new Error(item.message);if(item.type==='result'){result=item.data;received=true;renderResult();}};
    while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let end;while((end=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,end);buffer=buffer.slice(end+1);handle(line);}if(done){if(buffer.trim())handle(buffer);break;}}
    if(!received)throw new Error('The connection ended before a result arrived. Please retry.');
  } catch(e) {
    error(e.name==='AbortError'?'Run cancelled. You can adjust your inputs and start again.':e.message);
    $('run-state').textContent=e.name==='AbortError'?'CANCELLED':'NEEDS ATTENTION';
    $('results-title').textContent='Let’s adjust the starting point.';
  } finally {controller=null;busy(false);}
}
function loadDemo() {
  if(controller)return;
  $('goal').value='I want to become a product or data analyst. Help me build skills in data science, programming, statistics, and human-centered digital products.';
  if(![...$('roster').options].some(o=>o.value==='SP27'))$('roster').add(new Option('Spring 2027','SP27'));
  $('roster').value='SP27';$('career').value='UG';$('campus').value='MAIN';$('min-credits').value=12;$('max-credits').value=16;$('course-count').value=4;
  $('earliest').value='09:00';$('latest').value='18:00';$('required').value='';$('subjects').value='';$('permissions').value='';$('blocked-rows').replaceChildren();
  $('transcript-rows').replaceChildren();[{code:'CS 1110',grade:'A',status:'completed'},{code:'MATH 1110',grade:'B+',status:'completed'},{code:'MATH 1710',grade:'A-',status:'completed'},{code:'ECON 1110',grade:'B',status:'completed'}].forEach(addCourse);
  $('reviewed').checked=true;$('transcript-notes').textContent='Sample transcript for the demo profile. No personal student data.';run(true);
}
function renderResult() {
  familyIndex=0;variantIndex=0;$('results').hidden=false;$('run-state').textContent=result.families.length?'PLANS READY':'NO PLAN FOUND';
  $('results-title').textContent=result.families.length?`${result.families.length} direction${result.families.length===1?'':'s'} to explore.`:'A constraint needs another look.';
  $('coverage').textContent=`${result.raw_count} offerings retrieved · ${result.retrieved_subjects.join(', ')} · ${result.assessed_count} assessed · ${result.eligible_count} in the solver pool`;
  $('family-list').innerHTML=result.families.map((family,i)=>{const v=family.variants[0];return `<button class="family-card${i===0?' selected':''}" data-family="${i}" aria-pressed="${i===0}"><div class="family-top"><span class="family-number">0${i+1}</span><span class="pill">${v.match} MATCH</span></div><h3>${escape(family.name)}</h3><p class="course-codes">${family.anchor_courses.map(escape).join(' · ')}</p><div class="family-meta">${v.credits} credits · ${v.days} campus days<br>${v.courses.length} courses · ${v.status==='OPTIMAL'?'Optimal for objective':'Feasible result'}</div><span class="variant-link">${family.variants.length} variant${family.variants.length===1?'':'s'} to explore ↗</span></button>`;}).join('');
  document.querySelector('.plan-detail').hidden=!result.families.length;
  $('audit-content').innerHTML=`<p><strong>${escape(result.data_mode)}</strong><br>Data timestamp: ${escape(result.retrieved_at)}<br>Solver status: ${escape(result.status)}. ${escape(result.search_note || 'No feasible schedule was returned by the bounded search.')}</p><p>${result.usage.calls} OpenAI calls · ${result.usage.input_tokens.toLocaleString()} input tokens · ${result.usage.output_tokens.toLocaleString()} output tokens.</p><ul>${result.warnings.map(w=>`<li>${escape(w)}</li>`).join('')}</ul><h4>${result.excluded.length} exclusions and review items</h4>${result.excluded.map(c=>`<div class="exclusion"><a href="${escape(c.source)}" target="_blank" rel="noopener noreferrer">${escape(c.code)}</a> — ${escape(c.reason)}</div>`).join('')}`;
  if(result.families.length)renderFamily();else document.querySelector('.audit').open=true;
}
function renderFamily() {
  const family=result.families[familyIndex];$('family-title').textContent=family.name;$('family-core').textContent=`Shared core: ${family.core.join(' · ')}. Variants change sections or at most one elective.`;
  document.querySelectorAll('.family-card').forEach((el,i)=>{el.classList.toggle('selected',i===familyIndex);el.setAttribute('aria-pressed',String(i===familyIndex));});
  $('variant-list').innerHTML=family.variants.map((v,i)=>`<button class="variant${i===variantIndex?' selected':''}" data-variant="${i}" aria-pressed="${i===variantIndex}"><strong>${escape(v.name)}</strong><small>${v.name==='One elective swap'?'Same core, different elective':i===0?'Family anchor':v.objective==='days'?'Minimize campus days':'Minimize early classes'}<br>${v.days} days · starts ${time(v.earliest)}</small></button>`).join('');
  renderPlan();
}
function currentPlan(){return result.families[familyIndex].variants[variantIndex];}
function renderPlan(){
  const plan=currentPlan(),anchor=result.families[familyIndex].variants[0];
  const before=anchor.courses.map(c=>c.code),after=plan.courses.map(c=>c.code);
  const removed=before.filter(c=>!after.includes(c)),added=after.filter(c=>!before.includes(c));
  $('variant-comparison').textContent=`${plan.credits} credits · ${plan.match}/100 career match · ${plan.days} campus days. `+(removed.length?`Compared with anchor: ${removed.join(', ')} → ${added.join(', ')}.`:variantIndex?'Same courses, different sections.':'This is the family anchor.');
  $('course-list').innerHTML=plan.courses.map((c,i)=>`<article class="course-item"><div class="course-top"><div><p class="course-code" style="color:${colors[i%6][1]}">${escape(c.code)} · ${c.bundle.credits} CREDITS · ${c.assessment.score}/100 FIT</p><h5>${escape(c.title)}</h5></div><a class="source-link" href="${escape(c.source)}" target="_blank" rel="noopener noreferrer">Cornell ↗</a></div><p>${escape(c.assessment.reason)}</p><p class="evidence">“${escape(c.assessment.evidence)}”</p><div class="tags">${c.assessment.skills.map(s=>`<span class="tag">${escape(s)}</span>`).join('')}</div><details><summary>Requirements & section details ↓</summary><p><strong>Prerequisites:</strong> ${escape(c.prereq_text || 'No prerequisite stated in this roster field; this is not proof of unrestricted enrollment.')}<br><strong>Corequisites:</strong> ${escape(c.coreq_text || 'None stated.')}<br><strong>Restrictions:</strong> ${escape(c.restriction_text || 'None stated in this roster field.')}</p><p>${c.bundle.sections.map(s=>`${escape(s.component)} ${escape(s.label)} · Class #${s.number}<br>${s.meetings.map(m=>`${escape(m.days)} ${time(m.start)}–${time(m.end)} · ${m.date_start} to ${m.date_end}`).join('<br>')}`).join('<br>')}</p>${c.warnings.length?`<p>Catalog notes: ${c.warnings.map(escape).join(' ')}</p>`:''}<p>${escape(c.notes)}</p></details></article>`).join('');
  $('calendar-legend').innerHTML=plan.courses.map((c,i)=>`<span><i style="background:${colors[i%6][1]}"></i>${escape(c.code)}</span>`).join('');
  const meetings=plan.courses.flatMap(c=>c.bundle.sections.flatMap(s=>s.meetings));
  const min=meetings.map(m=>m.date_start).sort()[0],max=meetings.map(m=>m.date_end).sort().at(-1);
  const start=new Date(min+'T12:00:00Z');start.setUTCDate(start.getUTCDate()-((start.getUTCDay()+6)%7));
  const prior=$('week-select').value;$('week-select').replaceChildren();
  while(start.toISOString().slice(0,10)<=max){const iso=start.toISOString().slice(0,10);$('week-select').add(new Option('Week of '+iso,iso));start.setUTCDate(start.getUTCDate()+7);}
  if([...$('week-select').options].some(o=>o.value===prior))$('week-select').value=prior;
  renderCalendar();
}
function renderCalendar(){
  const plan=currentPlan(),week=$('week-select').value;
  const events=plan.courses.flatMap((c,i)=>c.bundle.sections.flatMap(s=>s.meetings.map(m=>({...m,code:c.code,component:s.component,color:colors[i%6]}))));
  const dayCodes=events.some(e=>/[SU]/.test(e.days))?'MTWRFSU':'MTWRF',names=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const begin=Math.min(8*60,...events.map(e=>Math.floor(e.start/60)*60)),end=Math.max(18*60,...events.map(e=>Math.ceil(e.end/60)*60));
  const height=(end-begin)*.8,columns=`36px repeat(${dayCodes.length},minmax(0,1fr))`;
  let html=`<div class="cal-head" style="grid-template-columns:${columns}"><div></div>${[...dayCodes].map((d,i)=>`<div>${names[i]}</div>`).join('')}</div><div class="cal-body" style="grid-template-columns:${columns};height:${height}px"><div class="time-axis">`;
  for(let t=begin+60;t<end;t+=60)html+=`<span class="time-label" style="top:${(t-begin)*.8}px">${time(t)}</span>`;
  html+='</div>';
  [...dayCodes].forEach((day,i)=>{const d=new Date(week+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+i);const date=d.toISOString().slice(0,10);html+='<div class="day-col">';
    events.filter(e=>e.days.includes(day)&&e.date_start<=date&&date<=e.date_end).forEach(e=>{html+=`<div class="cal-event" style="top:${(e.start-begin)*.8}px;height:${(e.end-e.start)*.8}px;background:${e.color[0]};color:${e.color[1]};border-color:${e.color[1]}" title="${escape(e.code)} ${escape(e.component)} ${time(e.start)}–${time(e.end)}"><b>${escape(e.code)}</b>${escape(e.component)}<br>${time(e.start)}–${time(e.end)}</div>`;});html+='</div>';});
  $('calendar').innerHTML=html+'</div>';
}
async function uploadTranscript(){
  const file=$('transcript-file').files[0];if(!file)return;error('');
  if(!$('api-key').value.trim()){$('api-section').open=true;error('Transcript extraction needs your OpenAI API key. You can also add courses manually.');$('transcript-file').value='';return;}
  if(file.size>5*1024*1024){error('Use a file smaller than 5 MB.');return;}
  const data=new FormData();data.append('file',file);data.append('api_key',$('api-key').value.trim());data.append('model',$('model').value);
  busy(true);$('cancel').hidden=true;$('transcript-notes').textContent='Extracting course codes and grades…';
  try{const response=await fetch('/api/transcript',{method:'POST',body:data});const body=await response.json();if(!response.ok)throw new Error(detailMessage(body));$('transcript-rows').replaceChildren();body.courses.forEach(addCourse);$('reviewed').checked=false;$('transcript-notes').textContent=['Review the extracted results before continuing.',...body.notes].join(' ');}catch(e){error(e.message);$('transcript-notes').textContent='Extraction did not finish. Your existing course entries have been kept.';}finally{busy(false);$('transcript-file').value='';}
}
$('planner-form').addEventListener('submit',e=>{e.preventDefault();run(false);});
$('demo-button').addEventListener('click',loadDemo);$('demo-top').addEventListener('click',loadDemo);
$('cancel').addEventListener('click',()=>controller?.abort());$('add-course').addEventListener('click',()=>addCourse());$('add-block').addEventListener('click',addBlock);
$('transcript-file').addEventListener('change',uploadTranscript);
$('family-list').addEventListener('click',e=>{const card=e.target.closest('[data-family]');if(card){familyIndex=Number(card.dataset.family);variantIndex=0;renderFamily();}});
$('variant-list').addEventListener('click',e=>{const card=e.target.closest('[data-variant]');if(card){variantIndex=Number(card.dataset.variant);renderFamily();}});
$('week-select').addEventListener('change',renderCalendar);
$('export').addEventListener('click',()=>{const payload={application:'Course Compass',roster:result.roster,data_mode:result.data_mode,retrieved_at:result.retrieved_at,family:result.families[familyIndex].name,plan:currentPlan(),warnings:result.warnings,trace};const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`course-compass-${result.roster}-plan.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
fetch('/api/rosters').then(r=>r.json()).then(data=>{const prior=$('roster').value;const rows=data.rosters.map(r=>({value:r.slug||r.value,label:r.descr||r.description||r.slug||r.value})).filter(r=>/^(SP|FA|SU|WI)\d{2}$/.test(r.value));if(rows.length){$('roster').replaceChildren(...rows.map(r=>new Option(r.label,r.value)));if(rows.some(r=>r.value===prior))$('roster').value=prior;}}).catch(()=>{});
