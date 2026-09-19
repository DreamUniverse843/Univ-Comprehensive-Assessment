// 表头筛选与排序只改变展示顺序，不修改业务数据。
const collator=new Intl.Collator('zh-CN',{numeric:true,sensitivity:'base'});
const blank=v=>v===null||v===undefined||v==='';
export function matchesColumn(value,query,type='text'){
 const q=String(query??'').trim();if(!q)return true;
 if(type==='number'){
  if(blank(value)||!Number.isFinite(Number(value)))return false;
  const match=q.replace(/≥/g,'>=').replace(/≤/g,'<=').match(/^(>=|<=|>|<|=)?\s*(-?\d+(?:\.\d+)?)$/);
  if(!match)return false;const n=Number(value),target=Number(match[2]);
  return ({'>=':()=>n>=target,'<=':()=>n<=target,'>':()=>n>target,'<':()=>n<target,'=':()=>n===target})[match[1]||'=']();
 }
 const text=String(value??'').toLocaleLowerCase();return q.startsWith('=')?text===q.slice(1).toLocaleLowerCase():text.includes(q.toLocaleLowerCase());
}
export function selectRows(rows,columns,state={}){
 const cols=columns.filter(Boolean),sort=cols.find(c=>c.key===state.sort);
 const result=rows.filter(row=>cols.every(c=>matchesColumn(c.value(row),state.filters?.[c.key],c.type)));
 if(sort&&state.direction)result.sort((a,b)=>{
  const av=sort.value(a),bv=sort.value(b);if(blank(av)||blank(bv))return blank(av)===blank(bv)?0:blank(av)?1:-1;
  return (sort.type==='number'?Number(av)-Number(bv):collator.compare(String(av),String(bv)))*state.direction;
 });return result;
}
export function createTableTools({scope,redraw,beforeChange}){
 const states=new Map(),models=new Map();
 const identity=key=>scope()+'|'+key;
 const stateFor=key=>{const id=identity(key);if(!states.has(id))states.set(id,{filters:{},sort:'',direction:0});return states.get(id)};
 function prepare(key,rows,columns){models.set(key,{rows,columns});return selectRows(rows,columns,stateFor(key))}
 function mount(table,key,localUpdate){
  const model=models.get(key);if(!model||table.dataset.controlsReady)return;table.dataset.controlsReady='1';
  const panels=[];
  const state=stateFor(key),heads=[...table.querySelectorAll('thead tr:first-child th')];
  const update=(column,focus=false)=>{beforeChange?.();if(localUpdate){localUpdate();sync()}else{redraw();if(focus){const target=[...document.querySelectorAll('input[data-column-filter]')].find(e=>e.dataset.tableKey===key&&e.dataset.columnFilter===column);target?.focus();if(target)target.setSelectionRange(target.value.length,target.value.length)}}};
  function sync(){heads.forEach((th,i)=>{const c=model.columns[i];if(!c)return;const b=th.querySelector('.column-sort');b.textContent=state.sort===c.key?(state.direction===1?'↑':'↓'):'↕';th.setAttribute('aria-sort',state.sort===c.key?(state.direction===1?'ascending':'descending'):'none');th.classList.toggle('column-filtered',!!state.filters[c.key]);th.querySelector('.column-filter-toggle')?.classList.toggle('active',!!state.filters[c.key]);});panels.forEach(({panel,toggle,column})=>{const open=state.open===column;toggle.setAttribute('aria-expanded',String(open));if(open){if(!panel.matches(':popover-open'))panel.showPopover();const rect=toggle.getBoundingClientRect();panel.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-248))+'px';panel.style.top=Math.max(8,Math.min(rect.bottom+6,window.innerHeight-panel.offsetHeight-8))+'px'}else if(panel.matches(':popover-open'))panel.hidePopover()});}
  heads.forEach((th,i)=>{
   const c=model.columns[i];if(!c)return;const label=th.textContent.trim();
   const wrap=document.createElement('div');wrap.className='column-heading';const title=document.createElement('span');title.textContent=label;wrap.append(title);
   const sort=document.createElement('button');sort.type='button';sort.className='column-sort';sort.title=label+'：升序 / 降序 / 默认顺序';sort.setAttribute('aria-label',label+'排序');sort.addEventListener('click',e=>{e.stopPropagation();state.open='';if(state.sort!==c.key){state.sort=c.key;state.direction=1}else if(state.direction===1)state.direction=-1;else{state.sort='';state.direction=0}update(c.key)});wrap.append(sort);
   const input=document.createElement('input');input.type='search';input.className='column-filter';input.dataset.columnFilter=c.key;input.dataset.tableKey=key;input.value=state.filters[c.key]||'';input.placeholder=c.type==='number'?'如 ≥2、≤5、=0':'输入筛选';input.setAttribute('aria-label',label+'筛选');input.title=c.type==='number'?'输入数值表示相等，也支持 ≥、≤、>、<':'输入文字匹配，=文字 表示完全匹配';
   input.addEventListener('input',e=>{if(e.isComposing)return;state.filters[c.key]=input.value;update(c.key,true)});input.addEventListener('compositionend',()=>{state.filters[c.key]=input.value;update(c.key,true)});input.addEventListener('keydown',e=>{if(e.key==='Enter')e.preventDefault()});
   const toggle=document.createElement('button');toggle.type='button';toggle.className='column-filter-toggle';toggle.setAttribute('aria-label',label+'筛选按钮');toggle.title=label+'筛选';toggle.innerHTML='<svg width=14 height=14 viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h12L9 8.5V13l-2-1V8.5Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';
   const panel=document.createElement('div');panel.className='column-filter-panel';panel.setAttribute('popover','manual');panel.setAttribute('role','group');panel.setAttribute('aria-label',label+'筛选设置');
   const caption=document.createElement('strong');caption.textContent=label+'筛选';const hint=document.createElement('small');hint.textContent=input.title;
   const actions=document.createElement('div');actions.className='column-filter-actions';const clear=document.createElement('button');clear.type='button';clear.textContent='清除此列';clear.addEventListener('click',()=>{state.filters[c.key]='';input.value='';update(c.key,true)});
   const close=document.createElement('button');close.type='button';close.textContent='完成';close.addEventListener('click',()=>{state.open='';sync();toggle.focus()});actions.append(clear,close);panel.append(caption,input,hint,actions);
   panel.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();state.open='';sync();toggle.focus()}});
   toggle.addEventListener('click',e=>{e.stopPropagation();state.open=state.open===c.key?'':c.key;sync();if(state.open)input.focus()});panels.push({panel,toggle,column:c.key});wrap.append(toggle,panel);th.replaceChildren(wrap);
  });
  const reset=document.createElement('button');reset.type='button';reset.className='table-reset';reset.textContent='清除表头筛选与排序';reset.addEventListener('click',()=>{state.filters={};state.open='';state.sort='';state.direction=0;heads.forEach(th=>{const input=th.querySelector('.column-filter');if(input)input.value=''});update()});table.parentElement.insertBefore(reset,table);sync();
 }
 function mountLocal(table,key){
  if(table.dataset.controlsReady)return;
  const body=table.tBodies[0];if(!body)return;
  const rows=[...body.rows],headers=[...table.querySelectorAll('thead tr:first-child th')];
  const columns=headers.map((th,i)=>{const label=th.textContent.trim();if(!label||['序号','选择','操作'].includes(label))return null;const numeric=/^(原表分值|原分值|本次计入|计入|奖励分|处罚分|分值|数量)$/.test(label);return {key:String(i),type:numeric?'number':'text',value:row=>{const text=row.cells[i]?.textContent.trim()||'';return numeric?(text.match(/^-?[\d,.]+/)?.[0]?.replace(/,/g,'')??null):text}}});
  prepare(key,rows,columns);
  const status=document.createElement('p');status.className='table-filter-count';table.after(status);
  const apply=()=>{const visible=new Set(prepare(key,rows,columns));for(const row of rows){row.hidden=!visible.has(row);if(row.hidden)row.querySelectorAll('input[type=checkbox]').forEach(x=>x.checked=false)}for(const row of visible)body.append(row);status.textContent=`筛选后 ${visible.size} / ${rows.length} 条`;};
  mount(table,key,apply);apply();
 }
 return {prepare,mount,mountLocal};
}
