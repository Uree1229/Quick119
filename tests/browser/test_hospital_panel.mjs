// 병원 채팅 패널(hospital-ui/panel.html)의 상태 계산 검사. 화면과 통신은 quick119-pwa의 실서버 브라우저 검사에서 본다.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildThreads,threadStatus,summaryFields,acceptBlock,inquiryLog,chatLog,compactChatLog,actionCount,inquiryStage,needsReview,
  etaLabel,patientTitle,severityKey,chatSummary,threadBadge,SEVERITY,DECLINE_REASONS,MAX_CLOSED,vitalsText,QUICK_REPLIES} from '../../hospital-ui/panel-state.mjs';

const NOW=1000;
const patient=(id,extra={})=>({id,age:60,symptoms:'흉통',priority_source:'expert',effective_priority:2,resource_demands:{ed_bed:1},
  required_capabilities:[],danger_signs:[],consciousness:null,breathing:null,state:'WAITING',eta_at:null,...extra});
const inquiry=(id,state,extra={},p={})=>({id,state,version:1,created_at:NOW-30,expires_at:null,question:null,answer:null,reason:null,overdue:false,
  patient:patient(`P${id}`,p),...extra});
const stages=threads=>threads.map(t=>`${t.patientId}:${t.stage}`);

test('반복 기록만 접고 대화와 중요한 알림은 유지한다',()=>{
  const items = [
    {kind:'system',type:'patient_updated',text:'환자정보가 수정됐습니다.',id:'1',order_key:[1]},
    {kind:'system',type:'other',text:'이송 관련 기록이 등록됐습니다.',id:'2',order_key:[2]},
    {kind:'message',sender_role:'patient',text:'환자 상태',id:'3',order_key:[3]},
    {kind:'system',type:'enroute_patient_change_requested',text:'확인 필요',id:'4',order_key:[4]},
  ];
  const result = compactChatLog(chatLog(items));
  assert.deepEqual(result.map(e=>e.kind),['routine-group','crew','notice']);
  assert.deepEqual(result[0].entries.map(e=>e.text),[items[0].text,items[1].text]);
  assert.equal(result[2].text,'확인 필요');
});

test('환자 한 명이 대화 하나이고, 목록은 상태나 중증도와 상관없이 먼저 온 순서다',()=>{
  const threads=buildThreads({
    open:[inquiry(3,'PENDING',{created_at:NOW-10},{effective_priority:4}),inquiry(1,'CHECKING',{created_at:NOW-300}),inquiry(2,'PENDING',{created_at:NOW-120})],
    recent:[inquiry(4,'ACCEPTED',{created_at:NOW-200,expires_at:NOW+600}),inquiry(5,'DECLINED',{created_at:NOW-400,reason:'병상'})],
    incoming:[patient('P6',{state:'IN_TRANSIT',eta_at:NOW+240})],
  },NOW);
  assert.deepEqual(stages(threads),['P1:checking','P4:accepted','P2:new','P3:new','P6:confirmed','P5:declined']);
  assert.equal(threads.at(-1).finished,true,'끝난 대화는 진행 중인 대화 아래에 둔다');
  assert.equal(threads.some(t=>t.finished&&t.stage!=='declined'),false);
  assert.equal(actionCount(threads,NOW),2,'새 문의 두 건만 답해야 하는 건수로 센다');
  const tie=buildThreads({open:[inquiry(9,'PENDING'),inquiry(8,'PENDING')]},NOW);
  assert.deepEqual(stages(tie),['P8:new','P9:new'],'같은 시각이면 문의 번호 순');
});

test('우리 병원으로 확정된 환자는 문의 기록보다 우선하고 한 번만 나온다',()=>{
  const confirmed=patient('P1',{state:'CONFIRMED'});
  const threads=buildThreads({recent:[{...inquiry(1,'CONFIRMED'),patient:patient('P1')}],incoming:[confirmed]},NOW);
  assert.deepEqual(stages(threads),['P1:confirmed']);
  assert.equal(threads[0].inquiry.id,1,'확정 전 문의는 요약 카드용으로 붙여 둔다');
  const arrived=buildThreads({incoming:[patient('P2',{state:'ARRIVED'})]},NOW);
  assert.equal(arrived[0].stage,'arrived');
});

test('같은 환자의 문의가 여러 건이면 최신 문의만 쓰고, 끝난 대화는 몇 건만 남긴다',()=>{
  const same=buildThreads({open:[{...inquiry(7,'PENDING'),patient:patient('PX')}],recent:[{...inquiry(3,'CANCELLED'),patient:patient('PX')}]},NOW);
  assert.deepEqual(stages(same),['PX:new']);
  const many=buildThreads({recent:Array.from({length:9},(_,i)=>inquiry(i+1,'DECLINED',{reason:'기타'}))},NOW);
  assert.equal(many.length,MAX_CLOSED);
  assert.deepEqual(many.map(t=>t.inquiry.id),[6,7,8,9],'최근에 끝난 몇 건만, 역시 먼저 온 순서로');
});

test('수용 응답 유효시간이 지나면 확정 대기가 아니라 종료로 본다',()=>{
  assert.equal(inquiryStage(inquiry(1,'ACCEPTED',{expires_at:NOW+1}),NOW),'accepted');
  assert.equal(inquiryStage(inquiry(1,'ACCEPTED',{expires_at:NOW}),NOW),'closed');
  assert.equal(threadStatus({stage:'closed',inquiry:inquiry(1,'ACCEPTED',{expires_at:NOW}),patient:patient('P1')},NOW).text,'수용 응답 만료');
});

test('상태 문구: 경과 시간, 답변 도착, 도착 예정, 상태 변경',()=>{
  const status=(stage,q,p=patient('P1'))=>threadStatus({stage,inquiry:q,patient:p},NOW);
  assert.deepEqual(status('new',inquiry(1,'PENDING')),{text:'새 문의',tone:'alert'});
  assert.equal(status('new',inquiry(1,'PENDING',{created_at:NOW-200})).text,'새 문의 · 3분 경과');
  assert.equal(status('new',inquiry(1,'PENDING',{overdue:true})).text,'새 문의 · 응답 지연');
  assert.deepEqual(status('checking',inquiry(1,'CHECKING',{question:'복용약?'})),{text:'구급대 답변 대기',tone:'wait'});
  assert.deepEqual(status('checking',inquiry(1,'CHECKING',{question:'복용약?',answer:'없음'})),{text:'답변 도착 · 회신 필요',tone:'alert'});
  assert.equal(status('confirmed',null,patient('P1',{state:'IN_TRANSIT',eta_at:NOW+250})).text,'우리 병원 확정 · 5분 뒤 도착');
  assert.equal(status('confirmed',null,patient('P1',{state:'CONFIRMED'})).text,'우리 병원 확정 · 출발 전');
  const review={enabled:true,risk_signature:'b',acknowledged_signature:'a'};
  assert.deepEqual(status('confirmed',null,patient('P1',{state:'IN_TRANSIT',transport_review:review})),{text:'상태 변경 · 확인 필요',tone:'alert'});
  assert.equal(needsReview(patient('P1',{transport_review:{...review,acknowledged_signature:'b'}})),false);
  assert.equal(status('confirmed',null,patient('P1',{state:'IN_TRANSIT',needs_adjustment:true})).text,'수용 취소 요청함');
  assert.equal(etaLabel(patient('P1',{state:'IN_TRANSIT',eta_at:NOW-5}),NOW),'곧 도착');
  assert.equal(etaLabel(patient('P1',{state:'IN_TRANSIT',eta_at:NOW+511*60}),NOW),'8시간 31분 뒤 도착');
  assert.equal(patientTitle({age:null,symptoms:''}),'나이 미확인 · 증상 미입력');
});

test('요약 카드는 있는 정보만 보여 주고 우리 병원 여유를 함께 표시한다',()=>{
  const hospital={resources:[{resource_type:'ed_bed',status:'available',free:3,stale:false}],restrictions:[]};
  const fields=summaryFields(patient('P1',{consciousness:'alert',breathing:'unknown',danger_signs:['호흡곤란'],required_capabilities:['응급의학']}),hospital);
  assert.deepEqual(fields.map(f=>f.label),['의식','위험 징후','필요 진료','필요 자원','우리 병원 여유']);
  assert.deepEqual(fields.at(-1),{label:'우리 병원 여유',value:'응급병상 3',tone:'ok'});
  assert.deepEqual(summaryFields(patient('P1',{resource_demands:{}}),hospital),[],'없는 항목은 만들지 않는다');
  const short={resources:[{resource_type:'ed_bed',status:'available',free:0}]};
  assert.equal(summaryFields(patient('P1'),short).at(-1).tone,'alert');
  assert.equal(summaryFields(patient('P1'),{resources:[]}).at(-1).value,'응급병상 미확인');
});

test('수용 제한 중이거나 서버 기준 여유가 모자라면 수용 가능을 막는다',()=>{
  const ok={resources:[{resource_type:'ed_bed',status:'available',free:1}],restrictions:[]};
  assert.equal(acceptBlock(patient('P1'),ok,NOW),null);
  assert.match(acceptBlock(patient('P1'),{...ok,resources:[{resource_type:'ed_bed',status:'available',free:0}]},NOW),/응급병상 여유/);
  const restricted={...ok,restrictions:[{scope:'all',reason:'병상 포화',status:'active',cleared_at:null,ends_at:null,starts_at:null}]};
  assert.match(acceptBlock(patient('P1'),restricted,NOW),/수용 제한 중\(기관 전체 · 병상 포화\)/);
  const cleared={...ok,restrictions:[{scope:'all',reason:'병상 포화',status:'active',cleared_at:NOW-1}]};
  assert.equal(acceptBlock(patient('P1'),cleared,NOW),null);
  const other={...ok,restrictions:[{scope:'소아청소년과',reason:'전문의 부재',status:'active',cleared_at:null}]};
  assert.equal(acceptBlock(patient('P1'),other,NOW),null,'이 환자에게 필요 없는 진료 분야의 제한은 막지 않는다');
  assert.equal(acceptBlock(patient('P1'),null,NOW),null);
});

test('확정 전 대화는 질문·답변과 회신 상태로, 확정 후 대화는 서버 순서대로 편다',()=>{
  assert.deepEqual(inquiryLog(inquiry(1,'PENDING')),[]);
  assert.deepEqual(inquiryLog(inquiry(1,'CHECKING',{question:'복용약?',answer:'없음'})).map(e=>e.kind),['me','crew']);
  assert.deepEqual(inquiryLog(inquiry(1,'CHECKING')).map(e=>e.text),['확인 중으로 회신했습니다.']);
  assert.deepEqual(inquiryLog(inquiry(1,'ACCEPTED')).map(e=>e.text),['수용 가능으로 회신했습니다.']);
  assert.match(inquiryLog(inquiry(1,'DECLINED',{reason:'병상'})).at(-1).text,/수용 불가\(병상\)/);
  const log=chatLog([
    {id:'message:2',kind:'message',sender_role:'hospital',sender_name:'의료진',body:'2번 베드로',at:12,order_key:[5,1,2]},
    {id:'event:5',kind:'system',text:'목적지가 확정됐습니다.',at:10,order_key:[5,0,5]},
    {id:'message:1',kind:'message',sender_role:'patient',sender_name:'구급대원',body:'출발합니다',at:11,order_key:[5,1,1]},
  ]);
  assert.deepEqual(log.map(e=>[e.kind,e.text]),[['notice','목적지가 확정됐습니다.'],['crew','출발합니다'],['me','2번 베드로']]);
});

test('불가 사유는 서버가 받는 값과 같고, 패널은 서버 문구를 마크업으로 쓰지 않는다',()=>{
  const service=readFileSync(new URL('../../server/service.py',import.meta.url),'utf8');
  const accepted=service.match(/DECLINE_REASONS = \{([^}]*)\}/)[1].split(',').map(s=>s.trim().replace(/'/g,'')).sort();
  assert.deepEqual([...DECLINE_REASONS].sort(),accepted);
  const panel=readFileSync(new URL('../../hospital-ui/panel.mjs',import.meta.url),'utf8');
  assert.equal(/innerHTML|insertAdjacentHTML|document\.write/.test(panel),false);
});

test('의식·호흡 코드값은 한글로 보여 주고, 나쁜 상태는 눈에 띄게 한다',()=>{
  const fields=summaryFields(patient('P1',{consciousness:'impaired',breathing:'normal',resource_demands:{}}),null);
  assert.deepEqual(fields,[{label:'의식',value:'저하',tone:'alert'},{label:'호흡',value:'정상'}]);
  assert.deepEqual(summaryFields(patient('P1',{consciousness:'unknown',breathing:'unknown',resource_demands:{}}),null),[]);
});

test('요약 카드에 구급대가 확인한 활력징후와 증상 시작 시각을 보인다',()=>{
  const p=patient('P1',{resource_demands:{},onset_at:Date.UTC(2026,10,23,5,6)/1000,
    extra:{vitals:{sbp:150,dbp:90,hr:110,spo2:94,temp:36.8,rr:22,glucose:129,nrs:5}}});
  assert.equal(vitalsText(p),'혈압 150/90 · 맥박 110 · 산소포화도 94% · 체온 36.8℃ · 호흡수 22 · 혈당 129');
  const fields=summaryFields(p,null);
  assert.deepEqual(fields.map(f=>f.label),['활력징후','증상 시작']);
  assert.equal(fields[0].wide,true,'한 줄 전체를 쓴다');
  assert.match(fields[1].value,/\d{1,2}:06/);
  assert.equal(vitalsText(patient('P1',{extra:{vitals:{sbp:100,hr:0}}})),'수축기 100 · 맥박 0','0도 값이고, 이완기가 없으면 수축기만');
  assert.equal(vitalsText(patient('P1',{extra:{vitals:{}}})),'');
  assert.equal(vitalsText(patient('P1',{extra:null})),'');
  assert.deepEqual(summaryFields(patient('P1',{resource_demands:{},extra:{vitals:{}}}),null),[],'값이 없으면 칸을 만들지 않는다');
});

test('빠른 답장은 입력칸에 넣기만 하고 보내지 않는다',()=>{
  assert.ok(QUICK_REPLIES.length>=3&&QUICK_REPLIES.every(t=>typeof t==='string'&&t.length<=30));
  const panel=readFileSync(new URL('../../hospital-ui/panel.mjs',import.meta.url),'utf8');
  const chip=panel.slice(panel.indexOf("class: 'qp-quick'"),panel.indexOf("class: 'qp-quick'")+400);
  assert.match(chip,/\$\('message'\)\.value = text/);
  assert.doesNotMatch(chip,/sendChat|respond\(|decide\(/,'누르면 바로 보내지 않음');
});

test('목록의 중증도 구분과 배지: 답할 일 1건에 안 읽은 구급대 메시지를 더한다',()=>{
  assert.deepEqual([4,3,2,1].map(n=>severityKey(patient('P1',{effective_priority:n}))),['urgent','severe','moderate','mild']);
  assert.equal(severityKey(patient('P1',{priority_source:'unknown',effective_priority:4})),'unknown','확인되지 않은 중증도는 색으로 단정하지 않는다');
  assert.deepEqual(Object.keys(SEVERITY),['urgent','severe','moderate','mild','unknown']);
  const items=[
    {id:'event:1',kind:'system',text:'확정',at:10},
    {id:'message:1',kind:'message',message_id:1,sender_role:'patient',body:'출발합니다',at:11},
    {id:'message:2',kind:'message',message_id:2,sender_role:'hospital',body:'2번 베드로',at:12},
    {id:'message:3',kind:'message',message_id:3,sender_role:'patient',body:'5분 뒤 도착',at:13},
  ];
  assert.deepEqual(chatSummary(items,0),{unread:2,maxId:3,lastAt:13,preview:'5분 뒤 도착'});
  assert.equal(chatSummary(items,1).unread,1,'본 메시지와 내가 보낸 메시지는 세지 않는다');
  assert.equal(chatSummary(items.slice(0,3),3).preview,'나: 2번 베드로');
  assert.deepEqual(chatSummary([]),{unread:0,maxId:0,lastAt:null,preview:''});
  const fresh={stage:'new',inquiry:inquiry(1,'PENDING'),patient:patient('P1')};
  const moving={stage:'confirmed',inquiry:null,patient:patient('P2',{state:'IN_TRANSIT'})};
  assert.equal(threadBadge(fresh,NOW),1);
  assert.equal(threadBadge(moving,NOW,2),2);
  assert.equal(threadBadge(moving,NOW),0);
});

test('구급대에게 하는 안내는 병원 대화에서 빼고, 진행 기록은 접고, 목적지 확정은 병원 입장 문구로 보인다',()=>{
  const items=[
    {kind:'system',type:'patient_created',text:'시스템과 구급대의 현장 접수를 시작했습니다. 확인한 환자 상태와 활력징후를 남겨 주세요.',id:'e1',order_key:[1]},
    {kind:'message',sender_role:'patient',body:'62세 남성 흉통',id:'m1',order_key:[2]},
    {kind:'system',type:'intake_report_received',text:'현장 보고가 이송 기록에 저장됐습니다.',id:'r1',order_key:[2,0,0,1]},
    {kind:'system',type:'inquiry_workflow_batch',text:'후보 3곳에 문의했습니다.',id:'e2',order_key:[3]},
    {kind:'system',type:'destination_confirmed',text:'가상 병원으로 목적지가 확정됐습니다. 병원이 대화에 참여했습니다.',id:'e3',order_key:[4]},
    {kind:'system',type:'transport_position_updated',text:'이송자의 위치 관측이 갱신됐습니다.',id:'e4',order_key:[5]},
    {kind:'system',type:'adjustment_needed',text:'조정을 요청했습니다.',id:'e5',order_key:[6]},
  ];
  const result=compactChatLog(chatLog(items));
  assert.deepEqual(result.map(e=>e.kind),['crew','routine-group','notice','routine-group','notice']);
  const text=JSON.stringify(result);
  assert.doesNotMatch(text,/현장 접수를 시작했습니다|이송 기록에 저장됐습니다/,'구급대 안내는 보이지 않음');
  assert.equal(result[1].entries[0].text,'후보 3곳에 문의했습니다.');
  assert.equal(result[2].text,'구급대가 우리 병원으로 목적지를 확정했습니다.');
  assert.equal(result[4].text,'조정을 요청했습니다.','행동이 필요한 알림은 접지 않음');
});
