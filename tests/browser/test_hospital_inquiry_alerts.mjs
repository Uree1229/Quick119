// 병원 메인의 새 수용 문의 알림 회귀 검사. 모듈 단위 검사와 실제 hospital-ui/index.html 스크립트 실행을 함께 한다.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {InquiryAlerts,alertsMarkup,MAX_SHOWN} from '../../hospital-ui/inquiry-alerts.mjs';

const html=readFileSync(new URL('../../hospital-ui/index.html',import.meta.url),'utf8');
const source=html.split('<script>')[1].split('</script>')[0].replaceAll("import('./","__dynamicImport('./");
const settle=async()=>{for(let i=0;i<15;i++)await new Promise(resolve=>setImmediate(resolve));};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const severity=p=>p.effective_priority>=3?['중증','t-high']:['경증','t-gray'];
const inquiry=(id,extra={},patient={})=>({id,state:'PENDING',version:1,attempts:1,created_at:100,elapsed_seconds:5,overdue:false,
  patient:{id:`P${id}`,age:40+id,symptoms:`증상${id}`,reporter_role:'transporter',priority_source:'expert',effective_priority:2,resource_demands:{},required_capabilities:[],danger_signs:[],...patient},...extra});

test('첫 조회는 기준선이고 그 뒤에 새로 들어온 문의만 알린다',()=>{
  const alerts=new InquiryAlerts();
  assert.equal(alerts.sync([inquiry(1),inquiry(2)]),false);assert.deepEqual(alerts.list(),[]);
  assert.equal(alerts.sync([inquiry(1),inquiry(2)]),false);
  assert.equal(alerts.sync([inquiry(1),inquiry(2),inquiry(3)]),true);assert.deepEqual(alerts.list().map(q=>q.id),[3]);
  assert.equal(alerts.sync([inquiry(1),inquiry(2),inquiry(3)]),false,'같은 문의를 다시 알리지 않음');
});

test('대기 목록에서 빠진 문의의 알림은 사라지고 닫은 알림은 다시 뜨지 않는다',()=>{
  const alerts=new InquiryAlerts();alerts.sync([]);
  alerts.sync([inquiry(1),inquiry(2)]);
  assert.equal(alerts.sync([inquiry(2)]),true,'다른 담당자 응답·만료');assert.deepEqual(alerts.list().map(q=>q.id),[2]);
  assert.equal(alerts.dismiss(2),true);assert.equal(alerts.dismiss(2),false);
  assert.equal(alerts.sync([inquiry(2)]),false);assert.deepEqual(alerts.list(),[]);
  alerts.sync([]);
  assert.equal(alerts.sync([inquiry(2)]),true,'목록에서 빠졌다가 다시 들어온 문의는 새 문의로 알림');
});

test('최신 내용으로 갱신하고 우선순위가 높은 문의를 위에 둔다',()=>{
  const alerts=new InquiryAlerts();alerts.sync([]);
  alerts.sync([inquiry(1),inquiry(2,{},{effective_priority:4}),inquiry(3,{},{effective_priority:4})]);
  assert.deepEqual(alerts.list().map(q=>q.id),[2,3,1]);
  alerts.sync([inquiry(1,{},{symptoms:'바뀐 증상'}),inquiry(2,{},{effective_priority:4}),inquiry(3,{},{effective_priority:4})]);
  assert.equal(alerts.list()[2].patient.symptoms,'바뀐 증상');
  alerts.reset();assert.equal(alerts.sync([inquiry(9)]),false,'초기화 뒤 첫 조회는 다시 기준선');
});

test('표시: 중증도·환자 요약·버튼을 보여 주고 서버 값은 이스케이프하며 넘치면 건수만 알린다',()=>{
  assert.equal(alertsMarkup([],{esc,severity}),'');
  const one=alertsMarkup([inquiry(7,{},{effective_priority:4,symptoms:'<b>흉통</b>'})],{esc,severity});
  assert.match(one,/data-alert-id="7"/);assert.match(one,/class="sev-dot t-high"/);assert.match(one,/<span class="sr-only">중증<\/span>/);
  assert.match(one,/47세 · &lt;b&gt;흉통&lt;\/b&gt;/);assert.doesNotMatch(one,/<b>흉통/);
  assert.match(one,/<button type="button"[^>]*data-alert-view="7">문의 보기<\/button>/);assert.match(one,/data-alert-dismiss="7">닫기/);
  assert.doesNotMatch(one,/data-alert-view-all/);
  const many=alertsMarkup([1,2,3,4,5].map(i=>inquiry(i)),{esc,severity});
  assert.equal((many.match(/data-alert-id=/g)||[]).length,MAX_SHOWN);assert.match(many,/새 문의가 2건 더 있습니다/);assert.match(many,/data-alert-view-all/);
});

// ---- 실제 index.html 실행
class El {
  constructor(id){this.id=id;this.dataset={};this.value='';this.hidden=false;this.disabled=false;this.textContent='';this.className='';this.style={};this.listeners={};this.innerHTML='';this.scrolled=0;
    this.classList={toggle(){}};}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  focus(){}setSelectionRange(){}querySelector(){return null;}querySelectorAll(){return [];}closest(){return null;}contains(){return false;}matches(){return false;}
  scrollIntoView(){this.scrolled++;}
}
class Page {
  constructor(responses){
    this.responses=responses;this.posts=[];this.els=new Map();
    const reply=(status,body)=>({ok:status<400,status,json:async()=>body});
    const fetch=async(url,opts={})=>{
      const {pathname,origin}=new URL(url),hid=decodeURIComponent(pathname.split('/')[3]||'');
      if(opts.method&&opts.method!=='GET'){this.posts.push(pathname);return reply(500,{});}
      if(pathname==='/api/hospitals')return reply(200,[{id:'H1',name:'가상 병원 1',source:'synthetic'},{id:'H2',name:'가상 병원 2',source:'synthetic'}]);
      if(pathname.endsWith('/inquiries'))return reply(200,{open:this.responses[origin]?.[hid]||[],recent:[]});
      if(pathname.endsWith('/incoming'))return reply(200,{incoming:[],affected:[],planning:[]});
      if(pathname.endsWith('/transport-change-reviews'))return reply(200,{reviews:[]});
      return reply(200,{name:'가상 병원',source:'synthetic',resources:[],restrictions:[],capabilities:[],public_data:[]});
    };
    const document={activeElement:null,title:'',getElementById:id=>this.el(id),querySelector:()=>this.el('any'),querySelectorAll:()=>[]};
    this.context=vm.createContext({document,location:{search:'?hid=H1&server=http://one',pathname:'/hospital/',origin:'http://one'},localStorage:{getItem:()=>null,setItem(){},removeItem(){},length:0,key:()=>null},URL,URLSearchParams,console,crypto:{randomUUID:()=>'id'},
      fetch,setInterval:()=>0,clearInterval(){},setTimeout:()=>0,clearTimeout(){},
      __dynamicImport:path=>path.includes('inquiry-alerts')?import('../../hospital-ui/inquiry-alerts.mjs'):new Promise(()=>{})});
    vm.runInContext(source,this.context);
  }
  el(id){if(!this.els.has(id))this.els.set(id,new El(id));return this.els.get(id);}
  eval(code){return vm.runInContext(code,this.context);}
  fire(id,type,event){for(const fn of this.el(id).listeners[type]||[])fn(event);}
  click(attribute,value){this.fire('inquiryAlerts','click',{target:{closest:selector=>selector===`[${attribute}]`?{dataset:{[attribute.replace('data-','').replace(/-(\w)/g,(_,c)=>c.toUpperCase())]:String(value)}}:null}});}
  async poll(){await this.eval('poll()');await settle();}
  get alerts(){return this.el('inquiryAlerts');}
  get badge(){return this.el('inquiryBadge');}
}
async function open(list=[inquiry(1)]){
  const responses={'http://one':{H1:list,H2:[]}};
  const page=new Page(responses);await settle();await page.poll();
  return {page,set:(next,hid='H1')=>{responses['http://one'][hid]=next;}};
}

test('페이지를 열 때 있던 문의는 알리지 않고 탭에 대기 건수만 표시한다',async()=>{
  const {page}=await open([inquiry(1),inquiry(2)]);
  assert.equal(page.alerts.hidden,true);assert.equal(page.alerts.innerHTML,'');
  assert.equal(page.badge.hidden,false);assert.equal(String(page.badge.textContent),'2');
  assert.match(html,/id="inquiryAlerts"[^>]*aria-live="polite"/);
});

test('다른 탭을 보는 중에 새 문의가 오면 알림이 뜨고 문의 보기로 그 문의가 열린다',async()=>{
  const {page,set}=await open();
  page.eval("setTab('incoming')");
  set([inquiry(1),inquiry(5,{},{effective_priority:4,symptoms:'흉통'})]);await page.poll();
  assert.equal(page.alerts.hidden,false);assert.match(page.alerts.innerHTML,/data-alert-id="5"/);assert.match(page.alerts.innerHTML,/45세 · 흉통/);
  assert.equal(String(page.badge.textContent),'2');
  assert.equal(page.eval('activeTab'),'incoming','알림만으로 화면을 옮기지 않음');assert.equal(page.eval('selectedId'),null);
  page.click('data-alert-view',5);
  assert.equal(page.eval('activeTab'),'inquiries');assert.equal(page.eval('selectedId'),5);
  assert.match(page.el('detail').innerHTML,/45세 · 흉통/);assert.equal(page.el('detail').scrolled,1);
  assert.equal(page.alerts.hidden,true,'본 문의의 알림은 닫힘');
  assert.equal(page.posts.length,0,'알림은 응답 요청을 보내지 않음');
});

test('같은 문의의 환자 상태가 바뀌면 보이는 알림과 우선순위를 갱신한다',async()=>{
  const {page,set}=await open([]);
  const initial=[1,2,3,4].map(i=>inquiry(i));
  set(initial);await page.poll();
  assert.doesNotMatch(page.alerts.innerHTML,/data-alert-id="4"/);
  const revised=inquiry(4,{},{effective_priority:4,age:68,symptoms:'호흡 곤란'});
  set([...initial.slice(0,3),revised]);await page.poll();
  assert.match(page.alerts.innerHTML,/data-alert-id="4"[\s\S]*?긴급[\s\S]*?68세 · 호흡 곤란/);
  assert.doesNotMatch(page.alerts.innerHTML,/data-alert-id="3"/);
  assert.equal(page.alerts.innerHTML.match(/data-alert-id="(\d+)"/)[1],'4');
});

test('닫기는 그 알림만 없애고 문의는 그대로 둔다',async()=>{
  const {page,set}=await open([]);
  set([inquiry(1),inquiry(2)]);await page.poll();
  page.click('data-alert-dismiss',1);
  assert.doesNotMatch(page.alerts.innerHTML,/data-alert-id="1"/);assert.match(page.alerts.innerHTML,/data-alert-id="2"/);
  assert.equal(page.eval('selectedId'),null);assert.equal(page.eval('data.open.length'),2);
  await page.poll();assert.doesNotMatch(page.alerts.innerHTML,/data-alert-id="1"/,'닫은 알림은 다음 조회에 다시 뜨지 않음');
});

test('목록에서 직접 고른 문의의 알림도 닫힌다',async()=>{
  const {page,set}=await open([]);
  set([inquiry(1),inquiry(2)]);await page.poll();
  page.fire('openList','click',{target:{closest:s=>s==='[data-id]'?{dataset:{id:'2'}}:null}});
  assert.doesNotMatch(page.alerts.innerHTML,/data-alert-id="2"/);assert.match(page.alerts.innerHTML,/data-alert-id="1"/);
});

test('다른 담당자가 처리해 목록에서 빠진 문의의 알림은 사라진다',async()=>{
  const {page,set}=await open([]);
  set([inquiry(1)]);await page.poll();assert.equal(page.alerts.hidden,false);
  set([]);await page.poll();
  assert.equal(page.alerts.hidden,true);assert.equal(page.badge.hidden,true);
});

test('병원을 바꾸면 이전 병원의 알림을 지우고 새 병원의 기존 문의는 알리지 않는다',async()=>{
  const {page,set}=await open([]);
  set([inquiry(1)]);await page.poll();assert.equal(page.alerts.hidden,false);
  set([inquiry(8),inquiry(9)],'H2');
  page.el('hospital').value='H2';page.fire('hospital','change',{});
  assert.equal(page.alerts.hidden,true,'전환 즉시 이전 병원 알림 제거');
  await settle();await page.poll();
  assert.equal(page.alerts.hidden,true);assert.equal(String(page.badge.textContent),'2');
  set([inquiry(8),inquiry(9),inquiry(10)],'H2');await page.poll();
  assert.match(page.alerts.innerHTML,/data-alert-id="10"/);assert.doesNotMatch(page.alerts.innerHTML,/data-alert-id="(1|8|9)"/);
});

test('한꺼번에 많이 오면 세 건만 보이고 나머지는 목록 열기로 안내한다',async()=>{
  const {page,set}=await open([]);
  page.eval("setTab('incoming')");
  set([1,2,3,4,5].map(i=>inquiry(i)));await page.poll();
  assert.equal((page.alerts.innerHTML.match(/data-alert-id=/g)||[]).length,3);assert.match(page.alerts.innerHTML,/2건 더/);
  page.click('data-alert-view-all','');
  assert.equal(page.eval('activeTab'),'inquiries');assert.equal(page.eval('selectedId'),null);
});
