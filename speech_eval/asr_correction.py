"""음성 인식문의 용어 오인식 교정 제안과 숫자 확인 표시 (실험 모듈, 서버 미연결).

사전(config/paramedic_voice_terms.json)의 용어와 발음(자모)이 가까운 말을 찾아 교정을 '제안'한다.
숫자는 고치지 않는다. 잘못 붙었을 가능성이 있는 수치를 확인 대상으로 표시만 한다.
제안은 사람이 확인하기 위한 것이며, 이 모듈은 환자 기록이나 서버 추출 경로를 바꾸지 않는다.
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / 'config' / 'paramedic_voice_terms.json'

_CHO = 'ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ'
_JUNG = 'ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ'
_JONG = ('', 'ㄱ', 'ㄲ', 'ㄱㅅ', 'ㄴ', 'ㄴㅈ', 'ㄴㅎ', 'ㄷ', 'ㄹ', 'ㄹㄱ', 'ㄹㅁ', 'ㄹㅂ', 'ㄹㅅ', 'ㄹㅌ', 'ㄹㅍ', 'ㄹㅎ',
         'ㅁ', 'ㅂ', 'ㅂㅅ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ')
# 발음이 가까워 인식기가 자주 바꿔 적는 자모 쌍. 치환 비용을 절반으로 본다.
_SIMILAR_GROUPS = ('ㄱㅋㄲ', 'ㄷㅌㄸ', 'ㅂㅍㅃ', 'ㅈㅊㅉ', 'ㅅㅆ', 'ㅐㅔ', 'ㅒㅖ', 'ㅙㅚㅞ', 'ㅗㅜ', 'ㅓㅗ', 'ㅡㅜ', 'ㅡㅓ',
                   'ㅏㅑ', 'ㅓㅕ', 'ㅗㅛ', 'ㅜㅠ', 'ㅕㅛ', 'ㅛㅠ', 'ㅜㅝ', 'ㅓㅝ', 'ㅗㅝ', 'ㅢㅣ', 'ㅢㅡ', 'ㅟㅣ')
_SIMILAR = {frozenset((a, b)) for group in _SIMILAR_GROUPS for a in group for b in group if a != b}
_HANGUL = re.compile(r'[가-힣]+')
# 용어는 명사다. 뒤에는 조사나 있음·없음·하다류만 올 수 있다('어지러웠다'를 '어지러움다'로 만들지 않는다).
_MARKER = r'(?:있\S*|없\S*|의심\S*|미확인\S*|아님|아닌\S*|입니다|임|이다|인가요|하\S*|한\S*|합\S*|해\S*|됨|된\S*|되\S*)'
_NOUN_TAIL = re.compile(r'(?P<particle>이나|이고|이며|으로|이|가|은|는|을|를|과|와|도|의|만|에|로)?' + _MARKER + r'?')
_JOINING = frozenset(('과', '와', '이나'))
# 두 음절 용어는 종류에 맞는 문맥이 바로 뒤에 있거나, 앞에 추가·정정 명령이 있을 때만 제안한다.
# 측정 항목 뒤에는 수치, 증상 뒤에는 있음·없음류나 발생 시점('10분 전')이 와야 한다.
_AFTER = {'measurement': re.compile(r'[0-9]'),
          'symptom': re.compile(r'있|없|의심|미확인|아님|아닌|호소|[0-9]+\s?(?:분|시간|일|주|개월)\s?전')}
_BEFORE = re.compile(r'(?:추가|정정|수정)\S{0,2}')
_NEXT_WORD = re.compile(r'[0-9가-힣]\S*')
MAX_RATIO = 0.34
MARGIN = 0.5


def jamo(text):
    """한글 음절을 자모로 푼다. 소리가 없는 초성 ㅇ은 버려 연음(확인/화긴)을 같게 본다."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            cho, jung, jong = _CHO[code // 588], _JUNG[code % 588 // 28], _JONG[code % 28]
            if cho != 'ㅇ':
                out.append(cho)
            out.append(jung)
            out.extend(jong)
        elif not ch.isspace():
            out.append(ch)
    return out


def distance(a, b):
    """자모 편집 거리. 비슷한 자모의 치환은 0.5, 나머지 치환·삽입·삭제는 1."""
    previous = [float(i) for i in range(len(b) + 1)]
    for i, x in enumerate(a, 1):
        current = [float(i)]
        for j, y in enumerate(b, 1):
            cost = 0.0 if x == y else 0.5 if frozenset((x, y)) in _SIMILAR else 1.0
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def _limit(jamo_count):
    return 1.0 if jamo_count <= 6 else 1.5 if jamo_count <= 9 else 2.0


_NUMBER_WORDS = re.compile(r'[영공일이삼사오육칠팔구십백천점에도\s]+')


def load_terms(path=CATALOG):
    """교정 대상 용어와 종류(symptom·measurement·state). 사전 파일에서만 만들며, 오인식 사례를 따로 적어 넣지 않는다."""
    catalog = json.loads(Path(path).read_text(encoding='utf-8'))
    terms = catalog['terms']
    vocabulary, kinds = {}, {}

    def add(word, kind):
        compact = ''.join(word.split())
        if _HANGUL.fullmatch(compact) and len(compact) >= 2 and compact not in vocabulary:
            vocabulary[compact], kinds[compact] = word, kind

    # 용어는 한 덩어리로만 쓴다. '배가 아픔'이나 '혈당 체크 결과'를 낱말로 나누면 '배가'·'결과' 같은 흔한 말이 교정 대상이 된다.
    for entry in terms['symptoms']:
        for word in (entry['canonical'], *entry['synonyms']):
            add(word, 'symptom')
    # 측정 항목은 이름과 같은 글자를 가진 동의어만 쓴다. 약어의 음차(비티·피알)는 짧고 다른 말과 헷갈린다.
    measurement_words = set()
    for spec in terms['measurements'].values():
        add(spec['label'], 'measurement')
        measurement_words.update(spec['label'].split())
        for word in spec['synonyms']:
            if set(''.join(word.split())) & set(''.join(spec['label'].split())):
                add(word, 'measurement')
    for word in catalog.get('speech_recognition_hints', []):
        if len(word.split()) == 1:
            if word in measurement_words:
                add(word, 'measurement')                              # '혈압'처럼 항목 이름의 일부이면서 혼자도 쓰는 말
        elif not _NUMBER_WORDS.fullmatch(word):
            add(word, 'state')                                        # '의식 명료'는 통째로만. '정상'을 따로 넣으면 '증상'이 걸린다
    # 부정·불확실 표현(없음·아님·추정)은 넣지 않는다. 수정/추정, 아니/아님처럼 뜻이 다른 말과 한 글자 차이다.
    return vocabulary, kinds


def load_vocabulary(path=CATALOG):
    return load_terms(path)[0]


_PARTICLE_START = re.compile(r'[이가은는을를과와도의만에로]')
# 용어와 한 자모만 다른 흔한 낱말. '이상 없음'을 '외상 없음'으로 바꾸지 않도록 제안에서 뺀다.
# 저장소 문서의 한국어 문장 약 81만 자(교정이 필요 없는 글)에 제안이 붙은 낱말을 모은 것이며, 완전한 목록이 아니다.
# '의심'은 보고의 상태 표현이다. '흉통 의심'을 '흉통 오심'으로 바꾸면 미확인이 증상 있음으로 뒤집힌다(합성 음성 검증에서 발견).
LOOKALIKES = frozenset(('거절', '구조', '기전', '부족', '의심', '이상', '포함'))


class Corrector:
    def __init__(self):
        self.vocabulary, self.kinds = load_terms()
        self.by_length = {}
        for compact, written in self.vocabulary.items():
            self.by_length.setdefault(len(compact), []).append((compact, written, jamo(compact)))
        self.lengths = sorted(self.by_length, reverse=True)

    def _best(self, joined, minimum):
        """joined의 앞부분과 가장 가까운 용어. 정확히 일치하는 용어가 있으면 그보다 짧은 교정은 하지 않는다."""
        # '혈당이'처럼 사전 용어에 조사가 붙은 말은 이미 맞는 말이다.
        if any(joined[:m] in self.vocabulary and _PARTICLE_START.match(joined[m:]) for m in range(max(minimum, 1) + 1, len(joined))):
            return None
        for n in self.lengths:
            if n > len(joined) or n <= minimum:
                continue
            prefix = joined[:n]
            if prefix in self.vocabulary:
                return None
            if joined[minimum:n] in LOOKALIKES:
                continue
            source = jamo(prefix)
            scored = sorted((distance(source, target), compact, written) for compact, written, target in self.by_length[n])
            best = scored[0]
            if best[0] > _limit(len(source)) or best[0] / max(len(source), 1) > MAX_RATIO:
                continue
            if len(scored) > 1 and scored[1][0] < best[0] + MARGIN:
                continue                                              # 후보가 여럿이면 고르지 않는다
            return n, best
        return None

    def suggest(self, text):
        """교정 제안 목록. 각 항목은 원문 위치와 바꿀 말, 거리다. 원문은 바꾸지 않는다."""
        tokens = [(m.start(), m.group()) for m in _HANGUL.finditer(text)]
        suggestions, i = [], 0
        while i < len(tokens):
            found = None
            for width in (3, 2, 1):
                window = tokens[i:i + width]
                if len(window) < width or any(text[a + len(s):b].strip() for (a, s), (b, _) in zip(window, window[1:])):
                    continue
                joined = ''.join(s for _, s in window)
                minimum = len(joined) - len(window[-1][1])           # 마지막 낱말까지 걸치는 용어만
                match = self._best(joined, minimum)
                if not match:
                    continue
                n, (score, compact, written) = match
                start, end = window[0][0], window[-1][0] + len(window[-1][1])
                remainder = joined[n:]
                tail = _NOUN_TAIL.fullmatch(remainder)
                if not tail:
                    continue
                # 띄어 쓰는 용어('열이 나')는 마지막 낱말만 다를 수 있다. '여기 나오는'을 '열이 나오는'으로 만들지 않는다.
                words = written.split()
                if width > 1 and len(words) > 1 and [s for _, s in window[:-1]] != words[:-1]:
                    continue
                if n <= 2 and not self._has_context(text, start, end, remainder, tail.group('particle'), self.kinds[compact]):
                    continue
                found = dict(start=start, end=end, original=text[start:end], replacement=written + remainder,
                             term=written, distance=score, width=width, heard=joined[:n] if width == 1 else None)
                break
            if found:
                suggestions.append(found)
                i += found['width']
            else:
                i += 1
        # 한 발화에서 같은 말로 잘못 들린 낱말은 똑같이 다룬다. 한 곳만 고치면 '있음/없음' 충돌이 가려진다.
        heard = {s['heard']: s for s in suggestions if s['heard']}
        covered = {(s['start'], s['end']) for s in suggestions}
        for start, token in tokens:
            for form, first in heard.items():
                tail = token[len(form):]
                # Keep the attested honorific as well as ordinary noun tails.
                if (token.startswith(form) and (_NOUN_TAIL.fullmatch(tail) or tail == '이신')
                        and not any(a <= start < b for a, b in covered)):
                    suggestions.append(dict(first, start=start, end=start + len(token), original=token,
                                            replacement=first['term'] + tail, repeated=True))
                    covered.add((start, start + len(token)))
        return sorted(suggestions, key=lambda s: s['start'])

    def _has_context(self, text, start, end, remainder, particle, kind):
        following = _NEXT_WORD.search(text, end)
        following = following.group() if following else ''
        names_term = any(following.startswith(term) and self.kinds[term] == kind for term in self.vocabulary)
        if particle in _JOINING:
            return names_term                                         # 'A과 B'는 B도 같은 종류의 용어일 때만 ('부정과 정정' 제외)
        after = _AFTER[kind]
        if after.match(remainder[len(particle or ''):]) or after.match(text[end:].lstrip(' ,')) or names_term:
            return True
        previous = text[:start].split()[-1:]
        return bool(previous and _BEFORE.fullmatch(previous[0]))

    def apply(self, text):
        """제안을 모두 받아들였을 때의 문장과 제안 목록. 실험에서 '사람이 모두 확인했다'는 가정의 상한을 본다."""
        suggestions = self.suggest(text)
        corrected, offset = text, 0
        for s in suggestions:
            corrected = corrected[:s['start'] + offset] + s['replacement'] + corrected[s['end'] + offset:]
            offset += len(s['replacement']) - (s['end'] - s['start'])
        return corrected, suggestions


_SINO_UNIT = dict(zip('일이삼사오육칠팔구', range(1, 10)))
_NEAR_UNIT = {'고': 9, '규': 9, '리': 2, '니': 2, '렬': 6, '싸': 4, '파': 8, '치': 7}   # 숫자 낱말과 발음이 가까운 글자
_NATIVE_TENS = {'열': 10, '스물': 20, '서른': 30, '마흔': 40, '쉰': 50, '예순': 60, '일흔': 70, '여든': 80, '아흔': 90}
# 0으로 끝나는 수 바로 뒤의 한 글자. '90 이상', '80이고'처럼 조사·어미로 이어지는 말과 '120사이'(범위)는 뺀다.
_TENS_THEN_HANGUL = re.compile(r'(?<![0-9.])([1-9][0-9]{0,2}(?<=0))(?!사이)([가-힣])(?![상하고며다라었나면니])')
_NATIVE_THEN_DIGIT = re.compile(r'(' + '|'.join(sorted(_NATIVE_TENS, key=len, reverse=True)) + r')\s{0,2}([1-9])(?![0-9])')
_BP_MERGED = re.compile(r'혈압[은는이가]?\s{0,3}([0-9]{4,6})(?![0-9])')


_LABEL_LOOKBACK = 16
# '이'와 '일'은 주격 조사·서술격으로도 쓰인다("80이 나왔어", "90일 때"). 숫자 2·1로 볼 근거가 약하다.
_PARTICLE_LIKE = frozenset('이일')
_VALUE_END = re.compile(r'\s*(?:$|[,.!?]|추가|정정|수정)')
# 단위 접두어만 같은 일반 낱말('회복', '도착')은 제외한다.
_MEASUREMENT_UNIT = re.compile(
    r'\s*(?:%|퍼센트|프로|회(?:/분)?|번|bpm|mmHg|밀리미터\s*수은주|'
    r'mg(?:/dL)?|밀리그램|°C|℃|도)'
    r'(?:입니다|이에요|예요|이요|이고|이며|으로|로|이|가|은|는|을|를|요)?(?!\w)', re.I,
)
_BP_BEFORE_FIRST_VALUE = re.compile(r'혈압[은는이가]?\s*[:：]?\s*\Z')
_BP_SECOND_VALUE = re.compile(
    r'(?:\s*[/／–−에의-]\s*|\s+)[0-9]{2,3}'
    r'(?=$|[\s,.!?]|mmHg|밀리미터|입니다|이에요|이요|[이일](?!\w))', re.I,
)
_LABEL_LOOKAHEAD = 12


def _after_measurement_label(text, start, labels):
    """수치 바로 앞(같은 구절 안)에 측정 항목 이름이 있는가. '9월 30일', '160사례' 같은 일반 숫자를 걸러 낸다."""
    window = ''.join(re.split(r'[.!?]', text[max(0, start - _LABEL_LOOKBACK):start])[-1].split())
    return any(label in window for label in labels)


def _value_ends(text, start, end, labels):
    """수치 뒤에 구절 경계, 측정 단위, 다음 측정 항목 또는 혈압의 두 번째 값이 이어지는지 본다."""
    if _VALUE_END.match(text, end) or _MEASUREMENT_UNIT.match(text, end):
        return True
    if _BP_SECOND_VALUE.match(text, end) and _BP_BEFORE_FIRST_VALUE.search(
        text[max(0, start - _LABEL_LOOKBACK):start]
    ):
        return True
    following = ''.join(text[end:end + _LABEL_LOOKAHEAD].split())
    return any(following.startswith(label) for label in labels)


def numeric_flags(text, labels=None):
    """측정값이 잘못 붙어 적혔을 수 있는 자리. 값을 고치지 않고 확인 대상과 후보만 돌려준다."""
    if labels is None:
        vocabulary, kinds = load_terms()
        labels = [term for term in vocabulary if kinds[term] == 'measurement']
    flags = []
    for m in _TENS_THEN_HANGUL.finditer(text):
        unit = _SINO_UNIT.get(m.group(2), _NEAR_UNIT.get(m.group(2)))
        if not unit or not _after_measurement_label(text, m.start(), labels):
            continue
        if m.group(2) not in _PARTICLE_LIKE or _value_ends(text, m.start(), m.end(), labels):
            flags.append(dict(kind='tens_then_unit', start=m.start(), end=m.end(), original=m.group(),
                              candidate=int(m.group(1)) + unit))
    for m in _NATIVE_THEN_DIGIT.finditer(text):
        if _after_measurement_label(text, m.start(), labels):
            flags.append(dict(kind='native_tens_then_digit', start=m.start(), end=m.end(), original=m.group(),
                          candidate=_NATIVE_TENS[m.group(1)] + int(m.group(2))))
    for m in _BP_MERGED.finditer(text):
        digits = m.group(1)
        pairs = [(int(digits[:k]), int(digits[k:])) for k in (2, 3) if k < len(digits)]
        plausible = [p for p in pairs if 50 <= p[0] <= 300 and 20 <= p[1] <= 200 and p[0] > p[1]]
        flags.append(dict(kind='merged_blood_pressure', start=m.start(1), end=m.end(1), original=digits, candidates=plausible))
    return sorted(flags, key=lambda f: f['start'])


def correct_recordings(package, corrector=None):
    """speech_eval 녹음 묶음의 인식문에 제안을 적용한 사본과 문장별 내역을 만든다."""
    corrector = corrector or Corrector()
    result, details = dict(package, recordings=[]), []
    for row in package['recordings']:
        corrected, suggestions = corrector.apply(row.get('hypothesis') or '')
        result['recordings'].append(dict(row, hypothesis=corrected, condition=row['condition'] + '+term-correction'))
        details.append(dict(recording_id=row['recording_id'], case_id=row['case_id'], original=row.get('hypothesis') or '',
                            corrected=corrected, suggestions=suggestions, numeric_flags=numeric_flags(row.get('hypothesis') or '')))
    return result, details


def main(argv=None):
    parser = argparse.ArgumentParser(description='인식문 묶음에 용어 교정 제안을 적용한 사본을 만든다.')
    parser.add_argument('--recordings', required=True)
    parser.add_argument('--output', required=True, help='교정 사본 JSON (이미 있으면 거부)')
    parser.add_argument('--details', required=True, help='문장별 제안·숫자 표시 JSON (이미 있으면 거부)')
    args = parser.parse_args(argv)
    output, details_path = Path(args.output), Path(args.details)
    if output.resolve() == details_path.resolve():
        parser.error('--output과 --details는 서로 다른 경로여야 합니다.')
    for target in (output, details_path):
        if target.exists():
            parser.error(f'{target} 가 이미 있습니다.')
    package = json.loads(Path(args.recordings).read_text(encoding='utf-8'))
    corrected, details = correct_recordings(package)
    for target, value in ((output, corrected), (details_path, details)):
        try:
            with target.open('x', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=1)
        except FileExistsError:
            parser.error(f'{target} 가 이미 있습니다.')
    print(json.dumps(dict(recordings=len(details), changed=sum(d['original'] != d['corrected'] for d in details),
                          suggestions=sum(len(d['suggestions']) for d in details),
                          numeric_flags=sum(len(d['numeric_flags']) for d in details)), ensure_ascii=False))


if __name__ == '__main__':
    main()
