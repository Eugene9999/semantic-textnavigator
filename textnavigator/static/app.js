'use strict';
const $ = id => document.getElementById(id);
const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const kindNames = {contract:'Договор',amendment:'Допсоглашение',email:'Письмо',draft:'Проект',unknown:'Неизвестный тип'};
const stateNames = {active:'Применим на дату проверки',proposal:'Не согласовано',future:'Не действует на выбранную дату',review:'Требует проверки'};
const fieldNames = {amount:'Сумма договора',delivery:'Срок поставки'};
let files = [], result = null, busy = false, modelState = 'loading', filterActive = false;
let selectedField = null;
const dateText = value => value ? value.split('-').reverse().join('.') : 'нет даты';
const moneyText = value => new Intl.NumberFormat('ru-RU', {maximumFractionDigits:2}).format(value);
const valueText = (field, value) => value === null || value === undefined ? 'Нет данных' : field === 'amount' ? moneyText(value) + ' ₽' : dateText(value);

function message(id, text) { $(id).textContent = text; $(id).hidden = !text; }
function updateButton() { $('analyze').disabled = busy || !files.length || (modelState !== 'ready' && !$('rules-only').checked); }
function setBusy(value) {
  busy = value; updateButton();
  $('demo').disabled = value; $('files').disabled = value; $('as-of').disabled = value; $('rules-only').disabled = value;
  $('analyze').textContent = value ? 'Проверяем документы…' : 'Проверить условия →';
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Не удалось выполнить запрос.');
  return data;
}
async function pollStatus() {
  try {
    const data = await api('/api/status'); modelState = data.state;
    $('engine-status').textContent = data.state === 'ready' ? '● Локальная модель готова · multilingual-e5-small + явные правила' : data.state === 'error' ? data.error : data.state === 'idle' ? 'Модель не загружена. Доступен явно выбранный режим «Только правила».' : 'Подготовка локальной модели… При первом запуске скачиваются веса, около 470 МБ.';
    if (data.state === 'loading') setTimeout(pollStatus, 2500);
  } catch (_) { $('engine-status').textContent = 'Нет связи с приложением. Проверьте, запущен ли локальный сервер.'; setTimeout(pollStatus, 5000); }
  updateButton();
}
function invalidate(text) {
  result = null; selectedField = null; $('workspace').hidden = true; $('empty').hidden = false;
  message('notice', text); message('error', ''); updateButton();
}
function selectFiles(next) {
  files = next;
  $('file-label').textContent = `Выбрано файлов: ${files.length}`;
  $('selected-files').innerHTML = files.map(f => `<span class="file-chip">${esc(f.name)}</span>`).join('');
  invalidate('Комплект готов к проверке. Новая загрузка заменяет предыдущий комплект.');
}
$('files').addEventListener('change', async event => {
  try {
    const chosen = [...event.target.files];
    if (!chosen.length) return;
    if (chosen.length > 30) throw new Error('Можно загрузить не более 30 файлов.');
    const next = [];
    for (const file of chosen) {
      if (!file.name.toLowerCase().endsWith('.txt') || file.size > 1024 * 1024) throw new Error(`${file.name}: нужен TXT не больше 1 МБ.`);
      let text;
      try { text = new TextDecoder('utf-8', {fatal:true}).decode(await file.arrayBuffer()); }
      catch (_) { throw new Error(`${file.name}: сохраните файл в кодировке UTF-8.`); }
      next.push({name:file.name,text});
    }
    selectFiles(next);
  } catch (error) { message('error', error.message); }
  event.target.value = '';
});
$('demo').addEventListener('click', async () => {
  try { selectFiles((await api('/api/demo')).files); }
  catch (error) { message('error', error.message); }
});
$('as-of').addEventListener('change', () => { if (files.length) invalidate('Дата проверки изменена. Нажмите «Проверить условия», чтобы пересчитать результаты.'); });
$('rules-only').addEventListener('change', () => { updateButton(); if (result) message('notice', 'Режим изменится при следующей проверке. Текущие результаты пока сохранены.'); });
$('analyze').addEventListener('click', async () => {
  setBusy(true); message('error',''); message('notice','');
  try {
    result = await api('/api/analyze', {files,as_of:$('as-of').value,mode:$('rules-only').checked?'rules':'semantic'});
    filterActive = false; selectedField = null; $('reset-filter').hidden = true;
    render();
    const note = result.mode === 'semantic' ? 'Проверка завершена. Смысловой поиск включен; применимость условий проверена правилами.' : 'Проверка выполнена только правилами. Embedding-модель и смысловой поиск в этом результате не использовались.';
    message('notice', note + (result.duplicate_count ? ` Одинаковые копии исключены: ${result.duplicate_count}.` : ''));
  } catch (error) { message('error', error.message); }
  finally { setBusy(false); }
});

function render() {
  $('empty').hidden = true; $('workspace').hidden = false;
  $('stat-contracts').textContent = result.contracts.length;
  const proposals = result.contracts.flatMap(c => c.proposals);
  $('stat-proposals').textContent = proposals.length; $('proposal-count').textContent = proposals.length;
  $('stat-attention').textContent = result.contracts.reduce((n,c) => n + Object.values(c.fields).filter(f=>f.status!=='found').length,0);
  $('run-info').textContent = `${result.documents.length} документов · ${result.elapsed_seconds} с · ${result.mode==='semantic'?'модель + правила':'только правила'}`;
  $('as-of-caption').textContent = `По состоянию на ${dateText(result.as_of)}`;
  $('warnings').innerHTML = result.warnings.map(w=>`<p>${esc(w.message)}</p>`).join('');
  renderTable(); renderProposals();
  $('source-select').innerHTML = result.documents.map(d=>`<option value="${d.id}">${esc(d.filename)} · ${esc(kindNames[d.kind])}</option>`).join('');
  $('search-contract').innerHTML = '<option value="">Все договоры</option>' + result.contracts.map(c=>`<option value="${esc(c.id)}">${esc(c.id)}</option>`).join('');
  $('search-results').innerHTML = ''; $('search-button').disabled = result.mode !== 'semantic';
  $('search-note').textContent = result.mode === 'semantic' ? 'Результаты поиска помогают найти пункт, но не определяют его применимость.' : 'В режиме «Только правила» смысловой поиск недоступен.';
  if (result.contracts.length) showField(result.contracts[0].id, 'amount', false);
  else if (result.documents.length) showDocument(result.documents[0].id);
}
function renderTable() {
  const visible = result.contracts.filter(c=>!filterActive || (c.fields.delivery.status==='found' && c.fields.delivery.value < $('before').value));
  $('contracts').innerHTML = visible.length ? visible.map(c=>`<tr><td><div class="contract-id">${esc(c.id)}</div><div class="supplier">${esc(c.supplier || 'Поставщик не определен')}</div>${c.problems.length?`<span class="badge amber" title="${esc(c.problems.join(' '))}">Требует проверки</span>`:''}</td>${['amount','delivery'].map(name=>{
    const f=c.fields[name];
    const text=f.status==='review'?'Требует проверки':f.value===null?'Нет данных':name==='amount'?moneyText(f.value):dateText(f.value);
    const active=selectedField && selectedField.id===c.id && selectedField.name===name;
    return `<td><button class="value-button ${f.status!=='found'?'missing':''} ${active?'active':''}" data-contract="${esc(c.id)}" data-field="${name}" aria-label="${esc(c.id+' — '+fieldNames[name]+': '+text+' — открыть источник')}">${esc(text)}</button><span class="cell-note ${f.changed_by?'changed':''}">${f.status==='review'?'Неоднозначное основание':f.status==='missing'?'Значение не указано':f.changed_by?'↗ Изменено допсоглашением':'Из исходного договора'}</span></td>`;
  }).join('')}</tr>`).join('') : '<tr><td colspan="3"><span class="subtle">По выбранному условию договоров не найдено.</span></td></tr>';
  const unresolved = result.contracts.filter(c=>c.fields.delivery.status!=='found').length;
  $('table-note').textContent = filterActive ? `Срок строго раньше ${dateText($('before').value)}. Показано: ${visible.length}.` + (unresolved?` Договоров с неопределенным сроком, исключенных из фильтра: ${unresolved}.`:'') : 'Нажмите на сумму или срок, чтобы проверить основание.';
}
function renderProposals() {
  const rows = result.contracts.flatMap(c=>c.proposals.map(p=>({c,p}))).filter(({c})=>!filterActive || (c.fields.delivery.status==='found' && c.fields.delivery.value<$('before').value));
  $('proposal-count').textContent = rows.length;
  $('proposals').innerHTML = rows.length ? rows.map(({c,p})=>{
    const changes=Object.entries(p.fields).filter(([_,f])=>f.status!=='missing').map(([name,f])=>`${name==='amount'?'Сумма':'Срок'}: ${f.status==='found'?valueText(name,f.value):'требует проверки'}`).join(' · ');
    return `<div class="proposal"><div><div class="proposal-title">${esc(c.id)} <span class="badge amber">${esc(kindNames[p.kind])}</span></div><p class="proposal-copy">${esc(changes || 'Условия требуют проверки')}</p><p class="proposal-file">${esc(p.filename)}</p></div><button class="text-button" data-proposal="${p.document_id}">Открыть ↗</button></div>`;
  }).join('') : '<p class="muted-empty">В выбранных договорах несогласованных предложений не найдено.</p>';
}
function quoteButton(e) { return `<button class="quote-button" data-document="${e.document_id}" data-paragraph="${e.paragraph_id}">${esc(e.quote)}<small>${esc(e.filename)} · строка ${e.line} ↗</small></button>`; }
function showField(id, name, scroll = true) {
  const c=result.contracts.find(c=>c.id===id); if (!c) return;
  const f=c.fields[name]; selectedField={id,name}; renderTable();
  $('source-title').textContent = `${id} · ${fieldNames[name].toLowerCase()}`;
  let content=`<div class="evidence-value">${esc(f.status==='review'?'Требует проверки':valueText(name,f.value))}</div><p class="evidence-reason">${esc(f.reason)}</p>`;
  if(c.problems.length) content += `<p class="evidence-reason">${esc(c.problems.join(' '))}</p>`;
  const seen=new Set(); const unique=f.evidence.filter(e=>{if(seen.has(e.paragraph_id))return false;seen.add(e.paragraph_id);return true;});
  content += unique.map(quoteButton).join('');
  if (f.changed_by) {
    const doc=result.documents.find(d=>d.id===f.changed_by);
    content += `<details class="history"><summary>Почему допсоглашение применено</summary><p>${esc(doc.state_reason)}</p>${doc.metadata_evidence.map(quoteButton).join('')}</details>`;
  }
  if(f.history?.length) content += `<details class="history"><summary>Предыдущие значения (${f.history.length})</summary>${f.history.map(h=>`<p>${esc(valueText(name,h.value))}</p>${h.evidence.map(quoteButton).join('')}`).join('')}</details>`;
  $('evidence-detail').innerHTML=content;
  const first=f.evidence[0]; showDocument(first?.document_id || c.document_ids[0],first?.paragraph_id);
  if(scroll && window.innerWidth<800) $('source-panel').scrollIntoView({behavior:'smooth',block:'start'});
}
function showDocument(id, paragraphId) {
  const doc=result.documents.find(d=>d.id===id); if(!doc)return;
  $('source-select').value=id;
  $('source-meta').innerHTML=`<span class="badge ${doc.state==='active'?'green':doc.state==='review'?'red':'amber'}">${esc(stateNames[doc.state])}</span><span class="subtle">${esc(doc.state_reason)}</span>`;
  $('source-text').innerHTML=doc.paragraphs.map(p=>`<div id="${p.id}" class="source-paragraph ${p.id===paragraphId?'highlight':''}"><span class="line-num">${p.line}</span><span class="paragraph-content">${esc(p.text)}</span></div>`).join('');
  if(paragraphId) requestAnimationFrame(()=>{
    const node=$(paragraphId), panel=$('source-text');
    if(node) panel.scrollTo({top:node.getBoundingClientRect().top-panel.getBoundingClientRect().top+panel.scrollTop-15,behavior:'smooth'});
  }); else $('source-text').scrollTop=0;
}
$('source-select').addEventListener('change',e=>showDocument(e.target.value));
document.addEventListener('click',event=>{
  const field=event.target.closest('[data-field]');
  if(field && result) showField(field.dataset.contract,field.dataset.field);
  const quote=event.target.closest('[data-paragraph]');
  if(quote && result) {
    if(quote.classList.contains('search-result')) {
      const doc=result.documents.find(d=>d.id===quote.dataset.document);
      $('source-title').textContent=`${doc.contract_id || 'Документ'} · результат поиска`;
      $('evidence-detail').innerHTML='<p class="evidence-reason">Фрагмент найден по смыслу. Для действующих суммы и срока используйте таблицу условий.</p>';
    }
    showDocument(quote.dataset.document,quote.dataset.paragraph);
    if(quote.classList.contains('search-result') && window.innerWidth<800) $('source-panel').scrollIntoView({behavior:'smooth'});
  }
  const proposal=event.target.closest('[data-proposal]');
  if(proposal && result) {
    const doc=result.documents.find(d=>d.id===proposal.dataset.proposal);
    $('source-title').textContent=`${doc.contract_id} · несогласованное предложение`;
    $('evidence-detail').innerHTML='<p class="evidence-reason">Этот документ содержит предложение и не изменяет действующие условия.</p>';
    showDocument(doc.id,doc.fields.delivery.evidence[0]?.paragraph_id);
    if(window.innerWidth<800) $('source-panel').scrollIntoView({behavior:'smooth'});
  }
});
$('filter').addEventListener('click',()=>{if(!$('before').value){message('error','Выберите дату для фильтра.');return;}filterActive=true;$('reset-filter').hidden=false;renderTable();renderProposals();});
$('reset-filter').addEventListener('click',()=>{filterActive=false;$('reset-filter').hidden=true;renderTable();renderProposals();});
$('before').addEventListener('change',()=>{if(filterActive && $('before').value){renderTable();renderProposals();}});
$('search-form').addEventListener('submit',async event=>{
  event.preventDefault(); $('search-button').disabled=true; message('error',''); $('search-note').textContent='Ищем подходящие фрагменты…';
  try{
    const data=await api('/api/search',{query:$('query').value,contract_id:$('search-contract').value});
    $('search-results').innerHTML=data.matches.map(m=>`<button class="search-result" data-document="${m.document_id}" data-paragraph="${m.paragraph_id}"><span class="badge ${m.state==='active'?'green':'amber'}">${esc(kindNames[m.kind])} · ${esc(stateNames[m.state])}</span><p>${esc(m.quote)}</p><small>${esc(m.filename)} · строка ${m.line} ↗</small></button>`).join('');
    $('search-note').textContent=`Найдено фрагментов: ${data.matches.length}. Это близость по смыслу, а не подтверждение действующего условия.`;
  }catch(error){message('error',error.message);$('search-note').textContent='Поиск не выполнен.';}
  finally{$('search-button').disabled=result?.mode!=='semantic';}
});
pollStatus();
