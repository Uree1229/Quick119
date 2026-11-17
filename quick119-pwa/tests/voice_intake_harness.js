(async () => {
  const output = document.getElementById('test-result');
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const $ = id => document.getElementById(id);
  let sr;
  class FakeRecognition {
    constructor() { sr = this; }
    start() { this.onstart(); }
    stop() { this.onend(); }
    emit(text, final = true) {
      const result = [{transcript: text}]; result.isFinal = final;
      this.onresult({resultIndex: 0, results: [result]});
    }
  }
  window.SpeechRecognition = FakeRecognition;
  window.confirm = () => true;
  let failure = true, requests = [], resolveFetch;
  window.fetch = async (url, options) => {
    assert(url === '/api/emergency-intakes', 'only internal save endpoint');
    requests.push(JSON.parse(options.body));
    if (failure) throw new Error('synthetic offline');
    if (resolveFetch) await new Promise(resolve => { resolveFetch = resolve; });
    return {ok: true, json: async () => ({saved: true, id: 42, created_at: '2026-11-16T00:00:00Z'})};
  };
  try {
    (0, eval)(VOICE_SOURCE);
    const extract = window.Quick119Voice.extract;
    let data = extract('60세 남성, 의식 명료, 호흡 정상. 30분 전부터 흉통. 혈압 120에 80, 맥박 90, 체온 36.5도, 산소포화도 97퍼센트.');
    assert(data.age === '60세' && data.sex === '남성', 'demographics');
    assert(data.consciousness === '명료' && data.breathing === '정상', 'state');
    assert(data.onset === '30분 전부터' && data.symptoms.includes('흉통'), 'symptoms and onset');
    assert(data.systolic === 120 && data.diastolic === 80 && data.pulse === 90 && data.temperature === 36.5 && data.spo2 === 97, 'all vitals');
    data = extract('여성, 의식 없음, 호흡 없음, 흉통 없음. 심박수 0, 체온 36점5도, SpO2 88%');
    assert(data.consciousness === '없음' && data.breathing === '없음' && data.symptoms === '흉통 없음', 'negation preserved');
    assert(data.pulse === 0 && data.temperature === 36.5 && data.spo2 === 88, 'zero and decimal');
    data = extract('의식 저하 없음, 호흡곤란 없음, 흉통 없음');
    assert(data.consciousness === '저하 없음' && data.breathing === '곤란 없음', 'negative state predicates preserved');
    data = extract('의식 명료하지 않음 호흡 정상이지 않음, 맥박 미측정');
    assert(data.consciousness === '명료하지 않음' && data.breathing === '정상이지 않음', 'negative descriptive predicates');
    assert(extract('고혈압 병력, 혈압 미측정, 맥박 미측정').systolic === null, 'no history or missing inference');
    assert(Object.values(extract('환자 상태 확인 중')).every(v => v === null), 'unknown defaults');
    data = extract('혈압 100/60, 혈압 130/90, 맥박 90, 맥박 100, 체온 999, 산소포화도 200');
    assert(data.systolic === null && data.diastolic === null && data.pulse === null && data.temperature === null && data.spo2 === null, 'conflicting or invalid numbers');
    data = extract('수축기 혈압 120, 이완기 혈압 미측정, 심박수 90.5');
    assert(data.systolic === 120 && data.diastolic === null && data.pulse === null, 'partial measurement and invalid integer');
    assert(extract('의식 있음, 호흡 정상').spo2 === null, 'normal breathing cannot imply oxygen');
    $('start').click();
    assert($('save-report').disabled && $('transcript').disabled && sr.lang === 'ko-KR' && sr.continuous && sr.interimResults, 'recognition controls');
    sr.emit('60세 남성, 맥박 90', false);
    assert($('report-bubble').textContent.includes('60세') && $('state-pulse').value === '90', 'live interim extraction');
    $('state-pulse').value = '88'; $('state-pulse').dispatchEvent(new Event('input'));
    sr.emit('60세 남성, 맥박 91', true); $('stop').click();
    assert($('state-pulse').value === '88' && $('transcript').value.includes('91'), 'manual edit and original preserved');
    assert(!$('save-report').disabled, 'can confirm after recognition');
    const submit = () => $('report-form').onsubmit({preventDefault() {}});
    await submit();
    assert($('save-status').dataset.result === 'error' && $('transcript').value.includes('91') && !$('save-report').disabled, 'failed save retains state');
    const first = requests[0];
    assert(first.state.pulse === 88 && first.transcript.includes('91') && first.state.spo2 === null, 'save corrected state and raw original');
    failure = false; resolveFetch = true;
    const saving = submit();
    assert($('save-report').disabled && $('state-pulse').disabled, 'save in progress locks payload');
    await submit();
    assert(requests.length === 2 && requests[1].request_id === first.request_id, 'retry key stable and double click ignored');
    resolveFetch(); await saving;
    assert($('save-status').dataset.result === 'success' && $('save-status').textContent.includes('42'), 'save success visible');
    assert($('save-report').disabled && $('state-pulse').disabled, 'saved record locked');
    $('new-report').click();
    assert($('transcript').value === '' && $('state-pulse').value === '' && $('report-patient').value === '' && !$('start').disabled, 'fresh patient reset');
    $('start').click(); sr.emit('体温誤認識', false); sr.onerror({error: 'not-allowed'}); sr.onend();
    assert($('status').textContent.includes('拒否') || $('status').textContent.includes('거부'), 'permission error visible');
    assert($('transcript').value === '' && !$('transcript').disabled, 'unconfirmed interim not saved, fallback enabled');
    $('transcript').value = '<img src=x onerror=alert(1)> 흉통 없음';
    $('transcript').dispatchEvent(new Event('input'));
    assert(!$('report-bubble').querySelector('img') && $('report-bubble').textContent.includes('<img'), 'transcript rendered as text');
    $('state-pulse').value = '80'; $('state-pulse').dispatchEvent(new Event('input'));
    $('auto-extract').click();
    assert($('state-pulse').value === '', 'manual override can be explicitly reset');
    output.textContent = 'PASS: field report extraction and interaction';
  } catch (error) { output.textContent = 'FAIL: ' + error.message + '\n' + error.stack; }
})();
