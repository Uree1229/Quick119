/* Both input methods use the server-owned, versioned crew draft. */
(() => {
  'use strict';
  const groups = [
    ['나이', 'age', 'age_group'], ['성별', 'sex'], ['증상', 'symptoms', 'denied_symptoms', 'uncertain_symptoms'],
    ['의식', 'consciousness'], ['호흡', 'breathing'], ['혈압', 'systolic_bp', 'diastolic_bp'],
    ['맥박', 'pulse'], ['산소포화도', 'spo2'], ['체온', 'temperature'], ['호흡수', 'respiratory_rate'],
    ['혈당', 'glucose'], ['발생 시각', 'onset_at']
  ];
  const labels = {male:'남성', female:'여성', unknown:'미확인', normal:'정상', abnormal:'비정상',
    absent:'없음', alert:'명료', impaired:'저하', unresponsive:'무반응'};
  function value(key, v) {
    if (v == null) return '';
    if (key === 'onset_at') return new Date(v * 1000).toLocaleString('ko-KR');
    const text = Array.isArray(v) ? v.join(', ') : labels[v] || String(v);
    if (key === 'denied_symptoms') return '없음 · ' + text;
    if (key === 'uncertain_symptoms') return '미확인 · ' + text;
    return text;
  }

  const target = /^(?:(?:목적지(?:로)?|이송(?:지|할|하기로한|예정인)|수용(?:을)?(?:한|허가한|확정한|확정된|결정된)|받아준|선정된|배정된|확정(?:된|한))(?:병원|응급실)?|병원|응급실)(?:측|쪽)?(?:으로|에게|에|로|이랑|과|의)?(?:좀)?/;
  const call = /^(?:(?:전화|통화)(?:를)?(?:좀)?(?:연결(?:해)?|걸어|걸|해)|연락(?:을)?(?:해)|(?:전화번호|연락처)(?:를)?(?:알려|보여))(?:줘|주세요|줄래요?|주실래요|주시겠어요)?$/;
  function isHospitalCall(text) {
    const phrase = text.trim().replace(/\s+/g, '').replace(/[.!?]+$/, '').replace(/^(?:지금|이제|그럼)/, '');
    const match = target.exec(phrase);
    return !!match && call.test(phrase.slice(match[0].length));
  }
  function dialNumber(phone) {
    const number = String(phone || '').replace(/[\s()-]/g, '');
    return /^(?:0|\+82)(?:2\d{7,8}|[3-6]\d{8,9}|70\d{8})$/.test(number) ? number : '';
  }

  window.Quick119Intake = {isHospitalCall, dialNumber, bind({$, api, path, uuid, canEdit, search, error}) {
    const panel = $('command-panel');
    if (!panel) return null;
    let draft, loading, intent, reviewed, posting = false, followup = null, answering = null;
    let confirmation, lastHints = [];
    const revision = () => draft ? [draft.id, draft.version] : null;
    const endpoint = () => path() + '/intake-draft';
    function feedback(message) {
      $('intake-feedback').textContent = message;
      $('intake-feedback').hidden = !message;
    }
    async function refresh() {
      if (!loading) loading = api(endpoint()).then(data => { draft = data.draft; })
        .finally(() => { loading = null; });
      await loading;
    }
    function draw(mode) {
      intent = mode;
      reviewed = revision();
      const fields = draft.fields;
      $('command-title').textContent = mode === 'search' ? '이 정보로 병원을 탐색할까요?' : '환자정보 확인';
      $('command-summary').textContent = draft.speak_text;
      const list = $('command-fields'); list.replaceChildren();
      for (const [label, ...keys] of groups) {
        const item = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
        dt.textContent = label;
        if (label === '혈압' && (fields.systolic_bp != null || fields.diastolic_bp != null)) {
          dd.textContent = (fields.systolic_bp ?? '미확인') + ' / ' + (fields.diastolic_bp ?? '미확인') + ' mmHg';
        } else dd.textContent = keys.map(key => value(key, fields[key])).filter(Boolean).join(' · ') || '미확인';
        item.append(dt, dd); list.append(item);
      }
      $('command-warnings').textContent = [...(draft.warnings || []), ...(draft.asr_hints || [])].join('\n');
      $('command-confirm').hidden = mode === 'view';
      $('command-confirm').textContent = mode === 'search' ? '확인하고 병원 탐색' : '환자정보 확정';
      document.dispatchEvent(new CustomEvent('quick119:before-view', {detail: {view:'review'}}));
      if (!panel.open) panel.showModal();
      (mode === 'view' ? $('command-close') : $('command-confirm')).focus();
    }
    function handle(result) {
      draft = result.draft; lastHints = result.hints || [];
      const current = JSON.stringify(revision()) === JSON.stringify(result.revision);
      feedback([result.feedback, ...lastHints].filter(Boolean).join('\n'));
      if (current && ['review','repeat_question','missing'].includes(result.action) &&
          window.speechSynthesis && window.SpeechSynthesisUtterance) {
        window.speechSynthesis.cancel();
        const reading = new SpeechSynthesisUtterance(result.feedback);
        reading.lang = 'ko-KR'; window.speechSynthesis.speak(reading);
      }
      followup = current && ['missing','repeat_question'].includes(result.action) ? draft?.followup?.next_question : null;
      answering = null;
      $('answer').hidden = !followup;
      if (followup) $('answer').textContent = followup.question + ' · 답변하기';
      if (current && canEdit()) {
        if (result.effect === 'explicit_submit_api_required') draw('search');
        else if (result.effect === 'confirmation_requires_tap') draw('confirm');
      }
    }
    $('answer').addEventListener('click', () => {
      answering = answering ? null : followup?.id || null;
      $('answer').textContent = answering ? '질문 답변 취소' : followup.question + ' · 답변하기';
      feedback(answering ? followup.question : '자유 입력으로 돌아왔습니다.'); $('message').focus();
    });
    $('command-close').addEventListener('click', () => { if (!posting) panel.close(); });
    panel.addEventListener('cancel', event => { if (posting) event.preventDefault(); });
    $('command-confirm').addEventListener('click', async () => {
      if (posting || !reviewed || !panel.open || !canEdit() || intent === 'view') return;
      posting = true; $('command-confirm').disabled = $('command-close').disabled = true;
      const key = JSON.stringify([reviewed, intent]);
      if (confirmation?.key !== key) confirmation = {key, body:{request_id:uuid(), revision:reviewed, intent}};
      try {
        const response = await api(endpoint() + '/confirm', confirmation.body);
        draft = response.draft;
        if (intent === 'search') await search();
        panel.close();
        feedback(intent === 'search' ? '확인한 환자정보를 반영했습니다.' : '현재 버전의 환자정보를 확정했습니다.');
        confirmation = null;
      } catch (failure) {
        $('command-warnings').textContent = failure.message;
        if (failure.status === 409) {
          confirmation = null; panel.close(); await refresh(); error(failure.message);
        }
      } finally { posting = false; $('command-confirm').disabled = $('command-close').disabled = false; }
    });
    return {
      refresh, handle,
      async request(text, id, recognition) {
        if (draft === undefined) await refresh();
        return {text, request_id:id, revision:revision(), source:recognition ? 'speech' : 'text',
          ...(recognition?.alternatives?.length ? {alternatives:recognition.alternatives} : {}),
          ...(answering ? {question_id:answering} : {})};
      },
      async open(edit, fallback) {
        try {
          if (draft === undefined) await refresh();
          if (!draft || (!Object.keys(draft.fields).length && !Object.keys(draft.field_metadata).length)) fallback();
          else draw(edit ? 'search' : 'view');
        } catch (failure) { error(failure.message); }
      }
    };
  }};
})();
