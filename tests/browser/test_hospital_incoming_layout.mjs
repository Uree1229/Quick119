// 병원 도착 예정 탭의 배치 회귀 검사: 이송 대화가 위, 수용 유지·조정·도착 처리는 대화 옆, 긴 환자 ID는 끝자리만.
// 실제 hospital-ui/index.html 스크립트를 최소 DOM 위에서 실행한다.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const html=readFileSync(new URL('../../hospital-ui/index.html',import.meta.url),'utf8');
const source=html.split('<script>')[1].split('</script>')[0].replaceAll("import('./","__dynamicImport('./");
const settle=async()=>{for(let i=0;i<15;i++)await new Promise(resolve=>setImmediate(resolve));};
const LONG_ID='Q119-TEST-4a28fd41-f66b-4aec-b308-af12b4927847';
const patient=(extra={})=>({id:LONG_ID,age:12,symptoms:'복통',state:'CONFIRMED',eta_at:null,version:3,priority_source:'expert',
  effective_priority:2,required_capabilities:[],resource_demands:{emergency_bed:1},danger_signs:[],transport_review:{enabled:false,state:'NOT_STARTED'},...extra});

class El {
  constructor(id){this.id=id;this.dataset={};this.value='';this.hidden=false;this.disabled=false;this.textContent='';this.listeners={};this.innerHTML='';this.style={};
    this.classList={toggle(){}};}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  focus(){}setSelectionRange(){}querySelector(){return null;}querySelectorAll(){return [];}closest(){return null;}contains(){return false;}scrollIntoView(){}
}
class Page {
  constructor(incoming,query=''){
    this.incoming=incoming;
    this.tabButtons=['inquiries','incoming','resources','restrictions','operations'].map(tab=>Object.assign(new El('tab-'+tab),{dataset:{tab}}));this.requests=[];this.els=new Map();this.panelOptions=null;
    const reply=(status,body)=>({ok:status<400,status,json:async()=>body});
    const fetch=async(url,opts={})=>{
      const {pathname}=new URL(url);
      if(opts.method&&opts.method!=='GET'){this.requests.push({path:pathname,body:JSON.parse(opts.body||'{}')});return reply(200,{});}
      if(pathname==='/api/hospitals')return reply(200,[{id:'H1',name:'가상 병원 1',source:'synthetic'}]);
      if(pathname.endsWith('/inquiries'))return reply(200,{open:[],recent:[]});
      if(pathname.endsWith('/incoming'))return reply(200,{incoming:this.incoming,affected:[],planning:[]});
      if(pathname.endsWith('/transport-review'))return reply(200,{review:{enabled:false,state:'NOT_STARTED'},workflow:{}});
      if(pathname.endsWith('/transport-change-reviews'))return reply(200,{reviews:[]});
      if(pathname.includes('/conversation'))return reply(200,{items:[],has_more:false});
      return reply(200,{name:'가상 병원',source:'synthetic',resources:[],restrictions:[],capabilities:[],public_data:[]});
    };
    const page=this;
    class FakePanel{constructor(options){page.panelOptions=options;}sync(){return false;}refresh(){}}
    const document={activeElement:null,title:'',getElementById:id=>this.el(id),querySelector:()=>this.el('any'),querySelectorAll:selector=>selector==='[data-tab]'?this.tabButtons:[]};
    this.context=vm.createContext({document,location:{search:'?hid=H1&server=http://one'+query,pathname:'/hospital/',origin:'http://one'},
      localStorage:{getItem:()=>null,setItem(){},removeItem(){},length:0,key:()=>null},URL,URLSearchParams,console,
      fetch,setInterval:()=>0,clearInterval(){},setTimeout:()=>0,clearTimeout(){},
      __dynamicImport:path=>path.includes('workflow-panel')?Promise.resolve({HospitalWorkflowPanel:FakePanel}):new Promise(()=>{})});
    vm.runInContext(source,this.context);
  }
  el(id){if(!this.els.has(id))this.els.set(id,new El(id));return this.els.get(id);}
  eval(code){return vm.runInContext(code,this.context);}
  fire(id,type,event){for(const fn of this.el(id).listeners[type]||[])fn(event);}
  action(name){this.fire('incomingActions','click',{target:{closest:s=>s==='[data-patient-action]'?{dataset:{patientAction:name}}:null}});}
}
async function open(incoming=[patient()]){
  const page=new Page(incoming);await settle();
  await page.eval('poll()');await settle();
  page.eval("setTab('incoming')");
  page.fire('incomingList','click',{target:{closest:s=>s==='[data-patient]'?{dataset:{patient:LONG_ID}}:null}});
  await settle();
  return page;
}

test('이송 대화가 환자 상세보다 위에 있고 처리 칸은 대화 안에 있다',()=>{
  const chat=html.indexOf('<section id="hospitalChat"'),detail=html.indexOf('<div id="incomingDetail"');
  assert.ok(chat>0&&detail>chat,'이송 대화가 먼저');
  const chatSection=html.slice(chat,html.indexOf('</section>',chat));
  assert.match(chatSection,/<aside id="incomingActions"[^>]*aria-label="이송 처리"/);
  assert.match(chatSection,/id="chatLog"[\s\S]*id="chatForm"[\s\S]*id="incomingActions"/,'대화 기록·입력 옆에 처리 칸');
});

test('목록 카드는 나이·증상을 제목으로, 긴 환자 ID는 끝 6자리만 보여 준다',async()=>{
  const page=await open();
  const list=page.el('incomingList').innerHTML;
  assert.match(list,/<b class="title">12세 · 복통<\/b>/);
  assert.match(list,/<span class="pid" title="Q119-TEST-4a28fd41-f66b-4aec-b308-af12b4927847">ID …927847<\/span>/);
  assert.equal(list.replace(/<[^>]*>/g,'').includes(LONG_ID),false,'화면 글자에는 전체 ID를 쓰지 않음 (속성에만 남김)');
  assert.match(page.el('incomingDetail').innerHTML,/<div class="sub" title="Q119-TEST-[^"]+">환자 …927847<\/div>/);
  assert.equal(page.eval("shortPatientId('TEAM-COMMON-001')"),'TEAM-COMMON-001','짧은 ID는 그대로');
});

test('수용 유지·수용 취소 요청·도착 확인은 대화 옆 처리 칸에만 있다',async()=>{
  const page=await open();
  const actions=page.el('incomingActions').innerHTML,detail=page.el('incomingDetail').innerHTML;
  for(const name of ['keep','adjust','arrive'])assert.match(actions,new RegExp(`data-patient-action="${name}"`));
  assert.match(actions,/id="reviewNote"/);assert.match(actions,/data-patient-action="adjust"[^>]*>수용 취소 요청<\/button>/);assert.match(actions,/그 전까지 목적지는 이 병원으로 유지됩니다/);
  assert.doesNotMatch(detail,/data-patient-action|id="reviewNote"/,'상세에는 처리 버튼이 없음');
  assert.equal(page.el('chatPatient').textContent,'· 12세 · 복통');
  assert.equal(page.el('hospitalChat').hidden,false);
});

test('처리 칸의 버튼이 실제 요청을 보낸다: 조정은 사유가 있어야 하고 도착 확인은 바로 보낸다',async()=>{
  const page=await open();
  page.el('handler').value='가상 담당자';
  page.action('adjust');await settle();
  assert.equal(page.requests.length,0,'사유 없는 조정은 보내지 않음');
  assert.match(page.el('notice').textContent,/수용 취소 요청 사유를 메모에 입력하세요/);
  page.el('reviewNote').value='병상 부족';
  page.fire('incomingActions','input',{target:page.el('reviewNote')});
  page.action('adjust');await settle();
  assert.deepEqual(page.requests.map(r=>[r.path,r.body.decision,r.body.note]),[[`/api/hospitals/H1/patients/${LONG_ID}/decision`,'adjust','병상 부족']]);
  page.action('arrive');await settle();
  assert.equal(page.requests.at(-1).path,`/api/hospitals/H1/patients/${LONG_ID}/arrive`);
});

test('도착한 환자는 처리 칸에 퇴실 확인만, 선택을 풀면 처리 칸을 비운다',async()=>{
  const page=await open([patient({state:'ARRIVED'})]);
  const actions=page.el('incomingActions').innerHTML;
  assert.match(actions,/data-patient-action="discharge"/);assert.doesNotMatch(actions,/data-patient-action="(keep|adjust|arrive)"/);
  page.eval("selectedPatientId=null;selectedPatient=null;renderIncoming()");
  assert.equal(page.el('incomingActions').innerHTML,'');assert.equal(page.el('chatPatient').textContent,'');
});

test('추천·확인 안내는 수용 문의 탭에서만 보이고 도착 예정 탭에서는 숨긴다',async()=>{
  const page=await open();
  assert.ok(page.panelOptions,'추천 패널 모듈 연결');
  assert.equal(page.panelOptions.snapshot().showPlanning,false,'도착 예정 탭');
  page.eval("setTab('inquiries')");
  assert.equal(page.panelOptions.snapshot().showPlanning,true,'수용 문의 탭');
});

test('처리 칸은 내용이 같으면 다시 그리지 않고, 다른 환자로 바꾸면 그 환자의 메모로 다시 그린다',async()=>{
  const other='Q119-TEST-0000aaaa-bbbb-cccc-dddd-eeeeffff0001';
  const page=await open([patient(),patient({id:other,age:80,symptoms:'호흡곤란'})]);
  let writes=0;const el=page.el('incomingActions');let value=el.innerHTML;
  Object.defineProperty(el,'innerHTML',{get:()=>value,set:v=>{writes++;value=v;}});
  await page.eval('poll()');await settle();page.eval('renderIncoming()');
  assert.equal(writes,0,'바뀐 게 없으면 버튼을 새로 만들지 않음');
  page.eval(`reviewDrafts.set(${JSON.stringify(LONG_ID)},'병상 부족')`);page.eval('renderIncoming()');
  assert.equal(writes,1);assert.match(value,/>병상 부족<\/textarea>/);
  page.fire('incomingList','click',{target:{closest:s=>s==='[data-patient]'?{dataset:{patient:other}}:null}});await settle();
  assert.equal(writes,2,'환자를 바꾸면 다시 그림');assert.match(value,new RegExp(`data-for="${other}"`));
  assert.doesNotMatch(value,/병상 부족/,'앞 환자의 메모가 남지 않음');
});

test('자원·제한·업무 계측 탭은 기본으로 숨기고, tools=1이거나 경고에서 열면 보인다',async()=>{
  const tabs=page=>Object.fromEntries(page.tabButtons.map(b=>[b.dataset.tab,b.hidden]));
  const page=await open();
  assert.deepEqual(tabs(page),{inquiries:false,incoming:false,resources:true,restrictions:true,operations:true});
  page.eval("setTab('restrictions')");
  assert.equal(tabs(page).restrictions,false,'경고의 「수용 제한 확인」으로 열면 그 탭은 보임');assert.equal(tabs(page).resources,true);
  page.eval("setTab('inquiries')");assert.equal(tabs(page).restrictions,true,'떠나면 다시 숨김');
  const tools=new Page([patient()],'&tools=1');await settle();
  assert.deepEqual(tabs(tools),{inquiries:false,incoming:false,resources:false,restrictions:false,operations:false});
});
