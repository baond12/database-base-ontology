/* Giao diện bằng JavaScript thuần. Mọi kết quả pipeline lấy từ API Python. */
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const tag = (text, type = 'source') => `<span class="tag ${type}">${escapeHtml(text)}</span>`;
const json = (value) => escapeHtml(JSON.stringify(value, null, 2));
const empty = (title, text) => `<div class="empty-panel">${icon('cube')}<h3>${escapeHtml(title)}</h3><p>${escapeHtml(text)}</p></div>`;
const steps = ['Cung cấp schema', 'Base Ontology', 'Context tham chiếu', 'Tạo Relations', 'Kiểm định & xuất'];
const explanations = [
 ['Đầu vào từ schema', 'Bảng, cột và constraints được đọc trực tiếp từ nguồn.', 'INPUT', 'source'],
 ['Tạo Base Ontology', 'LLM đề xuất ngữ nghĩa; code giữ cấu trúc và kiểm tra phạm vi Methods.', 'LLM + CODE', 'llm'],
 ['Context tham chiếu một bước', 'Ghép Class/Properties qua incoming, outgoing và self-FK; không chuyển Methods.', 'CODE', 'code'],
 ['Relations từ FK', 'Endpoints và cardinality từ schema; LLM bổ sung tên, mô tả và assumptions.', 'CODE + LLM', 'llm'],
 ['Kiểm định và xuất kết quả', 'Tách kiểm định cấu trúc, AI review và review nghiệp vụ.', 'KIỂM ĐỊNH', 'review']
];
let state = {id:null, run:null, selected:null, step:1, tab:'overview', detail:null, follow:true};
let config = {};
let pollTimer, toastTimer, loadVersion = 0, detailVersion = 0;

async function api(path, options = {}) {
 const response = await fetch(path, {cache:'no-store', ...options});
 const body = await response.json();
 if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
 return body;
}

function toast(text) {
 $('#toast').textContent = text; $('#toast').hidden = false;
 clearTimeout(toastTimer); toastTimer = setTimeout(() => {$('#toast').hidden = true;}, 6000);
}

function navigate(hash) {
 if (location.hash === hash) route(); else location.hash = hash;
}

async function route() {
 clearTimeout(pollTimer); ++loadVersion; ++detailVersion;
 const match = location.hash.match(/^#workspace\/(sample|new|[0-9a-f]{16})$/);
 const workspace = Boolean(match);
 document.body.classList.toggle('in-workspace', workspace);
 $('#landing').hidden = workspace;
 $('#workspace').hidden = !workspace;
 $('#landing-nav').hidden = workspace;
 if (!workspace) return;
 state = {id:null, run:null, selected:null, step:1, tab:'overview', detail:null, follow:true};
 $('#class-search').value = '';
 $('#upload-error').hidden = true;
 if (match[1] === 'new') {
  $('#upload-view').hidden = false; $('#results-view').hidden = true; $('#run-status').hidden = true;
  $('#run-name').textContent = 'Tạo Ontology'; $('#mode-tag').textContent = 'LẦN CHẠY MỚI';
  $('#run-subtitle').textContent = 'Upload schema, kiểm tra cấu trúc rồi bắt đầu tạo ontology.';
  $('#upload-form').reset(); $('#file-name').innerHTML = 'Kéo thả file vào đây hoặc <span>chọn file</span>';
  $('#thinking-toggle').checked = config.thinking ?? true;
  renderStepper();
 } else {
  state.id = match[1];
  $('#upload-view').hidden = true; $('#results-view').hidden = false;
  $('#detail-content').innerHTML = '<div class="loading-panel"><span class="spinner"></span>Đang đọc kết quả…</div>';
  await refresh(true);
 }
 window.scrollTo({top:0, behavior:'instant'});
}

function renderStepper() {
 const run = state.run;
 const running = run && ['RUNNING','EXTRACTING'].includes(run.status);
 const done = (n) => run && (run.status === 'COMPLETE' || n < run.step || (n === 1 && run.status === 'READY'));
 $('#stepper').innerHTML = steps.map((name, i) => {
  const n = i+1;
  const label = done(n) ? 'Hoàn tất' : running && n === run.step ? 'Đang xử lý' : run?.status === 'ERROR' && n === run.step ? 'Cần kiểm tra' : 'Chờ xử lý';
  return `<button class="step-button ${n===state.step?'active':''} ${done(n)?'done':''}" data-step="${n}" aria-current="${n===state.step?'step':'false'}"><span class="step-circle">${done(n)?icon('check'):String(n).padStart(2,'0')}</span><span><strong>${name}</strong><small>${label}</small></span></button>`;
 }).join('');
}

function renderStatus() {
 const run = state.run;
 const box = $('#run-status'); box.hidden = !run;
 if (!run) return;
 let type='', mark=icon('info'), title=run.message, extra='', action='';
 if (run.sample) {
  type='sample-state'; mark=icon('table');
  title='Bạn đang xem kết quả mẫu đã lưu. Không có lần gọi LLM mới.';
  extra='Omnichannel Retail · Cấu trúc PASS · AI review hoàn tất · Review nghiệp vụ chưa duyệt.';
 } else if (run.status === 'READY') {
  type='ready-state'; mark=icon('check'); title='Schema đã sẵn sàng. Kiểm tra các bảng trước khi bắt đầu.';
  extra=`${run.counts.tables} bảng · ${run.counts.columns} cột · ${run.counts.foreign_keys} FK. LLM chỉ nhận schema và metadata.`;
  action='<button class="btn primary small" data-action="start">Chạy tạo Ontology '+icon('arrow')+'</button>';
 } else if (run.status === 'ERROR') {
  type='error-state'; title=`Có lỗi tại bước ${run.step || 1}${run.current_table?' · '+run.current_table:''}.`;
  extra=run.error || run.message;
  action='<button class="btn secondary small" data-action="start">Thử lại phần lỗi</button>';
 } else if (run.status === 'COMPLETE') {
  type='complete-state'; mark=icon('check'); title='Pipeline đã hoàn tất. Kết quả sẵn sàng để kiểm tra và tải.';
  extra=run.counts.findings ? `${run.counts.findings} findings cần xem xét.` : 'Không ghi nhận findings trong báo cáo. Review nghiệp vụ vẫn chưa được duyệt.';
 } else {
  mark='<span class="spinner"></span>';
  title=`${steps[(run.step || 1)-1]}${run.current_table?' · '+run.current_table:''}`;
  extra=`${run.message || 'Đang xử lý.'} ${run.total ? `Đã hoàn thành ${run.completed || 0}/${run.total} tác vụ của bước này.` : ''}`;
 }
 const progress = ['RUNNING','EXTRACTING'].includes(run.status) && run.total ? `<div class="progress" role="progressbar" aria-label="Tiến trình bước hiện tại" aria-valuenow="${run.completed||0}" aria-valuemin="0" aria-valuemax="${run.total}"><i style="width:${Math.min(100,100*(run.completed||0)/run.total)}%"></i></div>` : '';
 box.className='run-status '+type;
 box.innerHTML=`${mark}<div><strong>${escapeHtml(title)}</strong>${extra?`<small>${escapeHtml(extra)}</small>`:''}${progress}</div>${action}`;
}

function renderMetrics() {
 const c = state.run.counts;
 $('#metrics').innerHTML = [['table', c.tables, 'Bảng nguồn'],['cube', c.properties, 'Properties'],['code', c.methods, 'Methods'],['link', c.relations, 'Relations'],['shield', c.findings, 'Findings']].map(([symbol,value,label]) => `<div class="metric"><span class="metric-icon">${icon(symbol)}</span><div><strong>${value}</strong><small>${label}</small></div></div>`).join('');
}

function renderClassList() {
 const list=$('#class-list'), previous=list.querySelector('.active')?.dataset.table, scrollTop=list.scrollTop;
 const tables = state.run?.tables || [];
 const query = $('#class-search').value.toLowerCase();
 const filtered = tables.filter(t => `${t.name} ${t.class_name}`.toLowerCase().includes(query));
 $('#table-total').textContent=tables.length;
 $('#class-list').innerHTML=filtered.length ? filtered.map(t => `<button class="class-row ${state.selected===t.name?'active':''}" data-table="${escapeHtml(t.name)}" aria-pressed="${state.selected===t.name}">${icon(t.base_complete?'cube':'table')}<div><strong>${escapeHtml(t.class_name||t.name)}</strong>${t.class_name?`<small>${escapeHtml(t.name)}</small>`:''}</div>${t.base_complete?icon('check').replace('<svg','<svg class="row-done"'):''}</button>`).join('') : '<div class="empty-panel"><p>Không có bảng phù hợp.</p></div>';
 if(previous===state.selected) list.scrollTop=scrollTop;
 else {
  const active=list.querySelector('.active');
  if(active)list.scrollTop=active.getBoundingClientRect().top-list.getBoundingClientRect().top-list.clientHeight/2;
 }
}

async function refresh(initial = false) {
 const version = loadVersion;
 const id = state.id;
 try {
  const run = await api(`/api/runs/${id}`);
  if (version !== loadVersion || id !== state.id) return;
  state.run = run;
  if (initial || state.follow) {
   state.step = run.sample ? 2 : run.step || 1;
   state.tab = state.step === 5 ? 'validation' : 'overview';
  }
  if (!state.selected) state.selected = run.tables.find(t=>t.name==='products')?.name || run.tables[0]?.name || null;
  $('#run-name').textContent=run.name || run.database_id || 'Lần chạy mới';
  $('#mode-tag').textContent=run.sample?'KẾT QUẢ MẪU':'LẦN CHẠY CỦA BẠN';
  $('#mode-tag').className='tag '+(run.sample?'llm':'code');
  $('#run-subtitle').textContent=`${run.database_id || 'Đang đọc schema'} · ${run.model || config.model || 'DC5'} · Thinking ${run.thinking?'bật':'tắt'}`;
  renderStepper(); renderStatus(); renderMetrics(); renderClassList(); renderDownloads();
  if (state.selected) await loadDetail();
  else {
   $('#class-heading').innerHTML=''; $('#tabs').innerHTML=''; $('#advanced-area').innerHTML='';
   $('#step-intro').innerHTML='';
   $('#detail-content').innerHTML=empty('Chưa có schema để hiển thị',run.status==='ERROR'?'Kiểm tra lỗi đầu vào ở thông báo bên trên.':'Hệ thống đang trích xuất schema từ file của bạn.');
  }
  $('#connection-status').innerHTML='<i class="green-dot"></i> Backend local';
 } catch(error) {
  if (version !== loadVersion) return;
  $('#connection-status').textContent='Mất kết nối backend';
  if (initial) toast(error.message);
 } finally {
  if (version===loadVersion && state.run && ['RUNNING','EXTRACTING'].includes(state.run.status)) {
   clearTimeout(pollTimer); pollTimer=setTimeout(()=>refresh(),1600);
  }
 }
}

async function loadDetail() {
 const version=++detailVersion, id=state.id, table=state.selected;
 try {
  const detail=await api(`/api/runs/${id}/classes/${encodeURIComponent(table)}`);
  if(version!==detailVersion || id!==state.id || table!==state.selected) return;
  state.detail=detail; renderDetail();
 } catch(error) { if(version===detailVersion) toast(error.message); }
}

function renderDetail() {
 const d=state.detail, b=d.base, s=d.schema, cls=b.class;
 const e=explanations[state.step-1];
 $('#step-intro').innerHTML=`<div><strong>${escapeHtml(e[0])}</strong><p>${escapeHtml(e[1])}</p></div>${tag(e[2],e[3])}`;
 $('#class-heading').innerHTML=`<div><h2>${icon(cls?'cube':'table')}${escapeHtml(cls?.name||s.name)}</h2><p>Bảng nguồn: <code>${escapeHtml(s.name)}</code>${cls?` · ${b.properties.length} Properties · ${b.methods.length} Methods · ${d.relations.length} Relations liên quan`:` · ${s.columns.length} cột · ${s.foreign_keys.length} FK`}</p></div><div class="heading-badges">${tag('Từ schema','source')}${cls?tag('LLM đề xuất','llm'):''}${tag('Cần review nghiệp vụ','review')}</div>`;
 const tabs=[['overview','Tổng quan'],['properties','Properties',b.properties?.length],['methods','Methods',b.methods?.length],['relations','Relations',d.relations.length],['validation','Kiểm định'],['json','JSON']];
 $('#tabs').innerHTML=tabs.map(([key,label,count])=>`<button class="tab-button ${state.tab===key?'active':''}" role="tab" aria-selected="${state.tab===key}" data-tab="${key}">${label}${count!==undefined?`<span class="tab-count">${count}</span>`:''}</button>`).join('');
 const content={overview:renderOverview,properties:renderProperties,methods:renderMethods,relations:renderRelations,validation:renderValidation,json:renderJson};
 $('#detail-content').innerHTML=content[state.tab]();
 renderAdvanced(); renderDownloads();
}

function renderOverview() {
 const d=state.detail, b=d.base, s=d.schema;
 if(state.step===1) return renderSchema();
 if(state.step===3) return renderContext();
 if(!b.class) return empty('Base Ontology chưa được tạo','Sau khi kiểm tra schema, chọn “Chạy tạo Ontology” để bắt đầu.');
 const report=state.run.report;
 const total=state.run.counts;
 return `<div class="overview-block"><div class="content-label">${icon('spark')} MÔ TẢ CLASS ${tag('LLM đề xuất','llm')}</div><p class="detail-description">${escapeHtml(b.class.description)}</p><div class="info-pair-grid"><div class="info-pair"><small>CLASS ID · CODE XÁC ĐỊNH</small><strong class="mono">${escapeHtml(b.class.class_id)}</strong></div><div class="info-pair"><small>ÁNH XẠ NGUỒN</small><strong>${escapeHtml(s.name)} → ${escapeHtml(b.class.name)}</strong></div><div class="info-pair"><small>TRẠNG THÁI NGỮ NGHĨA</small><strong>Đề xuất · Chưa duyệt nghiệp vụ</strong></div></div></div><div class="overview-block"><div class="content-label">METADATA MÔ TẢ NGUỒN ${tag('Từ input','source')}</div><p class="detail-description">${escapeHtml(s.description || 'Chưa có mô tả nguồn cho bảng này.')}</p></div><div class="overview-block"><div class="content-label">COVERAGE TOÀN DATABASE</div><div class="coverage-bars">${[['Table → Class',total.classes,total.tables],['Column → Property',total.properties,total.columns],['FK → Relation',total.relations,total.foreign_keys]].map(([label,current,max])=>`<div class="coverage-item"><div><span>${label}</span><span>${current}/${max}</span></div><div class="coverage-bar"><i style="width:${max?Math.min(100,current/max*100):100}%"></i></div></div>`).join('')}</div><p class="coverage-footnote">${report.structural_status==='PASS'?'Coverage và tính nhất quán đã qua kiểm định cấu trúc.':'Coverage hiển thị số artifacts hiện có; chưa thay thế kết quả kiểm định.'}</p></div>${b.assumptions?.length?`<div class="content-label">ASSUMPTIONS · LLM ĐỀ XUẤT</div><ul class="assumption-list">${b.assumptions.map(x=>`<li>${escapeHtml(x)}</li>`).join('')}</ul>`:''}<div class="notice">${icon('info')}<span>Methods là kế hoạch đọc trong một bảng, chưa triển khai SQL. Các mô tả do LLM đề xuất vẫn cần được đối chiếu nghiệp vụ.</span></div>`;
}

function columnFlags(c, schema) {
 let text='';
 if(c.primary_key_position) text+=tag('PK','review');
 if(schema.foreign_keys.some(f=>f.source_columns.includes(c.name))) text+=tag('FK','code');
 if(schema.indexes.some(i=>i.unique && i.columns.includes(c.name))) text+=tag('UNIQUE index','source');
 return text||'—';
}

function renderSchema() {
 const s=state.detail.schema;
 return `<div class="table-explanation">${icon('table')}Các trường dưới đây lấy trực tiếp từ schema. LLM chưa tham gia bước này.</div><div class="data-table-wrap"><table class="data-table"><thead><tr><th>Cột</th><th>Kiểu dữ liệu</th><th>Constraints</th><th>Nullable</th><th>Metadata mô tả</th></tr></thead><tbody>${s.columns.map(c=>`<tr><td class="col-name"><code>${escapeHtml(c.name)}</code></td><td><code>${escapeHtml(c.data_type)}</code></td><td>${columnFlags(c,s)}</td><td>${c.nullable?'Có':'Không'}</td><td class="desc-col">${escapeHtml(c.description||'—')}</td></tr>`).join('')}</tbody></table></div><p class="coverage-footnote">UNIQUE index có thể gồm nhiều cột hoặc có điều kiện; xem DDL để biết phạm vi đầy đủ.</p><details class="settings-details"><summary>DDL và indexes nguồn</summary><pre class="json-view">${escapeHtml(s.ddl)}\n\n${s.indexes.map(i=>escapeHtml(i.ddl||JSON.stringify(i))).join('\n')}</pre></details>`;
}

function renderProperties() {
 const d=state.detail;
 if(!d.base.properties) return renderSchema();
 return `<div class="table-explanation">${icon('info')}Mỗi cột tương ứng đúng một Property. Tên và mô tả do LLM đề xuất; kiểu và constraints từ schema.</div><div class="data-table-wrap"><table class="data-table"><thead><tr><th>Property · LLM</th><th>Cột nguồn · Schema</th><th>Kiểu / Constraints · Schema</th><th>Mô tả · LLM</th></tr></thead><tbody>${d.base.properties.map(p=>`<tr><td class="col-name"><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.property_id)}</small></td><td><code>${escapeHtml(p.source_column)}</code></td><td><code>${escapeHtml(p.data_type)}</code><small>${p.nullable?'Nullable':'NOT NULL'}</small>${columnFlags({...p,name:p.source_column},d.schema)}</td><td class="desc-col">${escapeHtml(p.description)}${p.metadata_description?`<small>Nguồn: ${escapeHtml(p.metadata_description)}</small>`:''}</td></tr>`).join('')}</tbody></table></div>`;
}

function renderMethods() {
 const methods=state.detail.base.methods;
 if(!methods) return empty('Methods chưa được tạo','LLM sẽ đề xuất Methods trong bước Base Ontology.');
 if(!methods.length) return empty('Không có Method được đề xuất','Class hiện tại chưa có đề xuất Method.');
 return `<div class="table-explanation">${icon('info')}Đề xuất chỉ đọc bảng ${escapeHtml(state.selected)}. Chưa có SQL thực thi; không có JOIN.</div>${methods.map((m,i)=>`<details class="method-card" ${i===0?'open':''}><summary><span class="mono">${escapeHtml(m.name)}</span>${tag(m.operation,'llm')}</summary><div class="method-body"><p>${escapeHtml(m.description)}</p><div class="method-grid"><div class="method-section"><h4>INPUT / FILTERS</h4><pre>${json(m.input_parameters)}</pre></div><div class="method-section"><h4>OUTPUT / AGGREGATION</h4><pre>${json(m.output)}</pre></div></div><div class="method-footer">Cột sử dụng: <code>${escapeHtml(m.source_columns.join(', '))}</code> · Group by: <code>${escapeHtml(m.group_by.join(', ')||'—')}</code></div><div class="scope-pills">${tag('LLM đề xuất','llm')}${tag('Một bảng · Chỉ đọc','code')}${tag('Chưa triển khai SQL','review')}</div></div></details>`).join('')}`;
}

function rangeText(range) { return `${range.min}..${range.max==='many'?'n':range.max}`; }

function renderRelations() {
 const relations=state.detail.relations;
 if(!relations.length) return empty('Chưa có Relations','Bảng có thể chưa được xử lý hoặc không có FK liên quan.');
 return `<div class="table-explanation">${icon('link')}Một FK tạo một Relation toàn cục, được gắn vào các Class liên quan.</div><div class="relations-list">${relations.map(r=>{const fk=r.source_fk;return `<article class="relation-card"><div class="relation-physical"><span class="mono">${escapeHtml(fk.source_table)}.${escapeHtml(fk.source_columns.join(', '))}</span>${icon('arrow')}<span class="mono">${escapeHtml(fk.target_table)}.${escapeHtml(fk.target_columns.join(', '))}</span>${tag('Từ schema','code')}<div class="cardinality-line">Mỗi ${escapeHtml(fk.source_table)} → ${rangeText(r.cardinality.source_to_target)} ${escapeHtml(fk.target_table)} · Mỗi ${escapeHtml(fk.target_table)} → ${rangeText(r.cardinality.target_to_source)} ${escapeHtml(fk.source_table)}</div></div><div class="relation-text"><strong>${escapeHtml(r.name)} ${tag('LLM đề xuất','llm')}</strong><p>${escapeHtml(r.description)}</p>${r.assumptions?.length?`<ul class="assumption-list">${r.assumptions.map(a=>`<li>${escapeHtml(a)}</li>`).join('')}</ul>`:''}</div></article>`;}).join('')}</div>`;
}

function renderContext() {
 const d=state.detail, context=d.context;
 if(!context) return empty('Context chưa được xây','Bước này thực hiện sau khi có Base Ontology của toàn bộ bảng.');
 const current=state.selected;
 const incoming=context.foreign_keys.filter(f=>f.target_table===current&&f.source_table!==current);
 const outgoing=context.foreign_keys.filter(f=>f.source_table===current&&f.target_table!==current);
 const self=context.foreign_keys.filter(f=>f.source_table===current&&f.target_table===current);
 const neighbor=(fk,direction)=>{
  const name=direction==='in'?fk.source_table:fk.target_table;
  const cls=context.references.find(r=>r.ontology.class.source_table===name)?.ontology.class.name||name;
  return `<div class="context-neighbor"><span>${escapeHtml(cls)}<br><span class="mono">${escapeHtml(name)}</span></span><span class="mono">${escapeHtml(fk.source_columns.join(', '))} → ${escapeHtml(fk.target_columns.join(', '))}</span></div>`;
 };
 return `<div class="table-explanation">${icon('info')}Chỉ một bước qua FK trực tiếp, không đệ quy. Context chỉ mang Class/Properties; Methods giữ tại Class sở hữu.</div><div class="context-graph"><div class="context-center">${icon('cube')}${escapeHtml(context.current.class.name)} ${tag('Class hiện tại','llm')}</div><div class="context-columns"><div class="context-column"><h4>Incoming FK → Class hiện tại</h4>${incoming.length?incoming.map(f=>neighbor(f,'in')).join(''):'<div class="context-neighbor">Không có incoming FK</div>'}</div><div class="context-column"><h4>Class hiện tại → Outgoing FK</h4>${outgoing.length?outgoing.map(f=>neighbor(f,'out')).join(''):'<div class="context-neighbor">Không có outgoing FK</div>'}</div></div><div class="self-link">${self.length?self.map(f=>`↻ Self-FK: ${escapeHtml(f.source_columns.join(', '))} → ${escapeHtml(f.target_columns.join(', '))}`).join('<br>'):'Không có self-FK ở Class này.'}</div></div><div class="info-pair-grid"><div class="info-pair"><small>CLASS THAM CHIẾU TRỰC TIẾP</small><strong>${context.references.length}</strong></div><div class="info-pair"><small>FK LIÊN QUAN</small><strong>${context.foreign_keys.length}</strong></div><div class="info-pair"><small>PHẠM VI TRUY VẾT</small><strong>1 bước · Không đệ quy</strong></div></div>`;
}

function renderValidation() {
 const report=state.run.report;
 const ai=report.ai_review_status;
 const structural=report.structural_status;
 const findings=[...(report.findings||[]).map(f=>({kind:'Cấu trúc',text:f})),...(report.ai_review_findings||[]).map(f=>({kind:'AI review',text:JSON.stringify(f),table:f.table}))];
 const classFindings=findings.filter(f=>!f.table||f.table===state.selected);
 return `<div class="validation-grid"><div class="validation-card"><small>KIỂM ĐỊNH CẤU TRÚC</small><strong class="${structural==='PASS'?'status-good':structural==='FAIL'?'status-error':'status-warning'}">${structural||'Chưa kiểm định'}</strong><p>Coverage · IDs · References · Tính nhất quán</p></div><div class="validation-card"><small>REVIEW NGỮ NGHĨA BẰNG LLM</small><strong class="${ai==='COMPLETED'?'status-good':'status-warning'}">${ai==='COMPLETED'?'Hoàn tất':ai==='NOT_RUN'?'Chưa chạy':'Chưa hoàn tất'}</strong><p>${(report.ai_review_findings||[]).length} findings trong báo cáo toàn database</p></div><div class="validation-card"><small>REVIEW NGHIỆP VỤ</small><strong class="status-warning">Chưa duyệt</strong><p>Ngữ nghĩa do LLM đề xuất, cần đối chiếu nguồn</p></div></div><h3>Findings liên quan đến ${escapeHtml(state.detail.base.class?.name||state.selected)}</h3>${classFindings.length?classFindings.map(f=>`<div class="inline-findings">${tag(f.kind,'review')}<p>${escapeHtml(f.text)}</p></div>`).join(''):empty(structural?'Không ghi nhận findings liên quan':'Chưa có báo cáo kiểm định',structural?'Kết quả này phản ánh các kiểm tra đã thực hiện.':'Báo cáo xuất hiện khi pipeline tới bước 5.')}<div class="notice">${icon('info')}<span>PASS cấu trúc chưa chứng minh toàn bộ ý nghĩa nghiệp vụ đúng. AI reviewer dùng cùng model với bước sinh nên vẫn có thể cùng mắc lỗi.</span></div>`;
}

function renderJson() {
 const b=state.detail.base;
 const value=state.jsonScope==='database'?state.ontology:b.class?{...b,relations:state.detail.relations}:state.detail.schema;
 return `<div class="json-toolbar"><div class="advanced-tabs"><button data-json-scope="class" class="${state.jsonScope!=='database'?'active':''}">Class hiện tại</button><button data-json-scope="database" class="${state.jsonScope==='database'?'active':''}" ${state.run.export_ready?'':'disabled'}>Toàn database</button></div><button class="text-button" data-action="copy-json">Sao chép JSON ${icon('code')}</button></div><pre class="json-view">${json(value)}</pre>`;
}

function renderAdvanced() {
 const step=state.step;
 if(![2,4,5].includes(step)) {$('#advanced-area').innerHTML='';return;}
 $('#advanced-area').innerHTML='<details class="advanced-panel" id="advanced-panel"><summary>Nâng cao · Context, Prompt và LLM Response Contract</summary><div class="advanced-body"><div class="advanced-tabs"><button data-advanced="context" class="active">Context</button><button data-advanced="prompt">Prompt</button><button data-advanced="contract">Response Contract</button></div><p class="advanced-note" id="advanced-note">Đang đọc thành phần của request…</p><pre class="json-view" id="advanced-value"></pre></div></details>';
 $('#advanced-panel').addEventListener('toggle', async event=>{
  if(!event.target.open || event.target.dataset.loaded) return;
  const table=state.selected, requestedStep=state.step, id=state.id;
  try {
   const data=await api(`/api/runs/${id}/advanced?table=${encodeURIComponent(table)}&step=${requestedStep}`);
   if(table!==state.selected||requestedStep!==state.step||id!==state.id) return;
   state.advanced=data;event.target.dataset.loaded='true';
   $('#advanced-note').textContent=data.sent?'Các thành phần đã gửi trong request được lưu.':'Bản xem trước. Request được hoàn thiện khi chạy bước này.';
   showAdvanced('context');
  } catch(error){toast(error.message);}
 });
}

function showAdvanced(key) {
 if(!state.advanced) return;
 document.querySelectorAll('[data-advanced]').forEach(x=>x.classList.toggle('active',x.dataset.advanced===key));
 $('#advanced-value').textContent=key==='prompt'?state.advanced.prompt:JSON.stringify(state.advanced[key],null,2);
}

function renderDownloads() {
 const ready=state.run?.export_ready;
 $('#download-actions').innerHTML=`<button class="btn secondary small" data-download="class" ${ready?'':'disabled'}>${icon('file')}Class JSON</button><button class="btn secondary small" data-download="report" ${state.run?.report?.structural_status?'':'disabled'}>${icon('shield')}Báo cáo</button><button class="btn primary small" data-download="all" ${ready?'':'disabled'}>${icon('download')}Tải toàn bộ</button>`;
}

async function startRun() {
 try {
  await api(`/api/runs/${state.id}/start`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  state.follow=true; await refresh();
 } catch(error){toast(error.message);}
}

function downloadBlob(content, filename, type='application/json') {
 const url=URL.createObjectURL(new Blob([content],{type}));
 const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

document.addEventListener('click',async event=>{
 const action=event.target.closest('[data-action]')?.dataset.action;
 if(action==='new') navigate('#workspace/new');
 if(action==='sample') navigate('#workspace/sample');
 if(action==='start') await startRun();
 if(action==='metadata-example') downloadBlob(JSON.stringify({tables:{products:{description:'Mô tả bảng sản phẩm',columns:{product_id:{description:'Mã định danh sản phẩm'}}}}},null,2),'metadata.example.json');
 if(action==='copy-json') {
  const b=state.detail.base;
  const value=state.jsonScope==='database'?state.ontology:b.class?{...b,relations:state.detail.relations}:state.detail.schema;
  try {await navigator.clipboard.writeText(JSON.stringify(value,null,2));toast('Đã sao chép JSON.');}
  catch(error){toast('Trình duyệt chưa cho phép sao chép. Bạn có thể chọn nội dung JSON để copy.');}
 }
 const step=event.target.closest('[data-step]');
 if(step) {state.step=Number(step.dataset.step);state.follow=false;state.tab=state.step===5?'validation':'overview';renderStepper();if(state.detail)renderDetail();}
 const table=event.target.closest('[data-table]');
 if(table) {state.selected=table.dataset.table;state.follow=false;renderClassList();await loadDetail();}
 const tab=event.target.closest('[data-tab]');
 if(tab) {state.tab=tab.dataset.tab;state.follow=false;renderDetail();}
 const advanced=event.target.closest('[data-advanced]');if(advanced)showAdvanced(advanced.dataset.advanced);
 const scope=event.target.closest('[data-json-scope]');
 if(scope&&!scope.disabled) {
  try {
   const id=state.id;
   if(scope.dataset.jsonScope==='database'&&!state.ontology) {
    const ontology=await api(`/api/runs/${id}/download?kind=ontology`);
    if(id!==state.id)return;
    state.ontology=ontology;
   }
   state.jsonScope=scope.dataset.jsonScope;
   $('#detail-content').innerHTML=renderJson();
  }catch(error){toast(error.message);}
 }
 const download=event.target.closest('[data-download]');
 if(download&&!download.disabled) {
  const url=`/api/runs/${state.id}/download?kind=${download.dataset.download}&table=${encodeURIComponent(state.selected)}`;
  try {
   const response=await fetch(url);
   if(!response.ok) throw new Error((await response.json()).error);
   const blob=await response.blob();
   const name=response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1]||'ontology.json';
   downloadBlob(blob,name,blob.type);
  }catch(error){toast(error.message);}
 }
});

$('#class-search').addEventListener('input',renderClassList);
$('#schema-file').addEventListener('change',()=>{
 $('#file-name').textContent=$('#schema-file').files[0]?.name || 'Chọn file schema';
 $('#upload-error').hidden=true;
});
const drop=$('#drop-zone');
drop.addEventListener('dragover',event=>{event.preventDefault();drop.classList.add('dragover');});
drop.addEventListener('dragleave',()=>drop.classList.remove('dragover'));
drop.addEventListener('drop',event=>{
 event.preventDefault();drop.classList.remove('dragover');
 if(event.dataTransfer.files.length){const transfer=new DataTransfer();transfer.items.add(event.dataTransfer.files[0]);$('#schema-file').files=transfer.files;$('#schema-file').dispatchEvent(new Event('change'));}
});

$('#upload-form').addEventListener('submit',async event=>{
 event.preventDefault();
 const button=$('#extract-button');button.disabled=true;button.innerHTML='<span class="spinner"></span> Đang upload…';
 $('#upload-error').hidden=true;
 try {
  const file=$('#schema-file').files[0];
  if(!file)throw new Error('Vui lòng chọn file schema.');
  if(file.size>20*1024*1024)throw new Error('File vượt giới hạn 20 MB.');
  const metadataFile=$('#metadata-file').files[0];
  const metadata=metadataFile?JSON.parse(await metadataFile.text()):{};
  const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
  const run=await api('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:file.name,content_base64:content,metadata,thinking:$('#thinking-toggle').checked})});
  navigate(`#workspace/${run.run_id}`);
 } catch(error){$('#upload-error').textContent=error.message;$('#upload-error').hidden=false;}
 finally{button.disabled=false;button.innerHTML='Trích xuất schema '+icon('arrow');}
});

window.addEventListener('hashchange',route);
api('/api/config').then(data=>{config=data;$('#model-label').textContent='DC5 · '+data.model;$('#thinking-toggle').checked=data.thinking;}).catch(()=>toast('Không kết nối được backend local.'));
route();
