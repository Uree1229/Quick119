"""음성 인식문 용어 교정 제안과 숫자 확인 표시의 회귀 검사."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from speech_eval import asr_correction as ac

ROOT = Path(__file__).resolve().parents[2]


class JamoDistance(unittest.TestCase):
    def test_silent_onset_is_dropped_so_liaison_spellings_match(self):
        self.assertEqual(ac.jamo('확인'), ac.jamo('화긴'))
        self.assertEqual(ac.jamo('응'), ['ㅡ', 'ㅇ'])

    def test_similar_jamo_cost_half_and_others_cost_one(self):
        self.assertEqual(ac.distance(ac.jamo('고토'), ac.jamo('구토')), 0.5)
        self.assertEqual(ac.distance(ac.jamo('흥통'), ac.jamo('흉통')), 1.0)
        self.assertEqual(ac.distance(ac.jamo('채원'), ac.jamo('체온')), 1.0)
        self.assertEqual(ac.distance(ac.jamo('산소폼화도'), ac.jamo('산소포화도')), 1.0)
        self.assertEqual(ac.distance(ac.jamo('흉통'), ac.jamo('흉통')), 0.0)


class Vocabulary(unittest.TestCase):
    def setUp(self):
        self.vocabulary = ac.load_vocabulary()

    def test_built_from_catalog_terms_only(self):
        for term in ('흉통', '호흡곤란', '피섞인가래', '산소포화도', '체온', '혈압', '맥박', '심박수', '의식명료', '호흡정상'):
            self.assertIn(term, self.vocabulary)

    def test_excludes_function_words_phrase_fragments_and_abbreviation_sounds(self):
        for term in ('없음', '아님', '추정', '확인안', '배가', '아픔', '열이', '비티', '피알', '알알', '새츄', '펄스',
                     '의식', '명료', '정상', '백이십에팔십'):
            self.assertNotIn(term, self.vocabulary)

    def test_every_term_has_a_kind(self):
        vocabulary, kinds = ac.load_terms()
        self.assertEqual(set(vocabulary), set(kinds))
        self.assertEqual((kinds['흉통'], kinds['체온'], kinds['의식명료']), ('symptom', 'measurement', 'state'))


class Suggestions(unittest.TestCase):
    def setUp(self):
        self.corrector = ac.Corrector()

    def corrected(self, text):
        return self.corrector.apply(text)[0]

    def test_near_sounding_terms_are_suggested_with_particles_kept(self):
        self.assertEqual(self.corrected('흥통 의심'), '흉통 의심')
        self.assertEqual(self.corrected('흥통이 없는 것은 아닌'), '흉통이 없는 것은 아닌')
        self.assertEqual(self.corrected('경연과 실신없음.'), '경련과 실신없음.')
        self.assertEqual(self.corrected('마비 없고 유통없음.'), '마비 없고 요통없음.')
        self.assertEqual(self.corrected('정정 채운 37.4'), '정정 체온 37.4')
        self.assertEqual(self.corrected('산소폼화도 읽어줘.'), '산소포화도 읽어줘.')
        self.assertEqual(self.corrected('호흡소 24'), '호흡수 24')

    def test_multiword_terms_are_matched_across_spaces(self):
        self.assertEqual(self.corrected('기침, 피 섞인 가레가 있습니다.'), '기침, 피 섞인 가래가 있습니다.')
        self.assertEqual(self.corrected('의식 명력, 호흡 정상'), '의식 명료, 호흡 정상')

    def test_suggestion_records_position_original_and_term(self):
        text = '추가 횡통 이슴'
        suggestion, = self.corrector.suggest(text)
        self.assertEqual(text[suggestion['start']:suggestion['end']], '횡통')
        self.assertEqual((suggestion['original'], suggestion['replacement'], suggestion['term']), ('횡통', '흉통', '흉통'))
        self.assertEqual(suggestion['distance'], 1.0)

    def test_exact_terms_and_unrelated_words_are_left_alone(self):
        for text in ('흉통과 호흡곤란 있음. 구토 없음.', '혈압 110에 70으로 수정해.', '맥박 90 아니 92로 정정.',
                     '확인 완료.', '혈당 백오 밀리그램 퍼 데시리터.', '호흡 불규칙.', '맥박 마이너스 십.'):
            self.assertEqual(self.corrector.suggest(text), [], text)

    def test_short_terms_need_clinical_context_and_a_particle_like_tail(self):
        self.assertEqual(self.corrector.suggest('흥통이신 순환기 내 과로 가야 할 것 같음'), [])   # 뒤가 조사·있음류가 아님
        self.assertEqual(self.corrector.suggest('어제 경연 대회에 다녀옴'), [])                    # 옆에 임상 문맥이 없음
        self.assertEqual(self.corrected('경연 있음'), '경련 있음')

    def test_same_misheard_word_is_treated_alike_within_one_utterance(self):
        self.assertEqual(self.corrected('흥통이신 흥통 없은'), '흉통이신 흉통 없은')      # 한 곳만 고치면 있음/없음 충돌이 가려진다
        self.assertEqual(self.corrector.suggest('흥통이신 순환기 내 과로'), [])            # 근거가 된 제안이 없으면 그대로 둔다

    def test_same_prefix_in_an_unrelated_word_is_not_changed(self):
        text = '어제 경연대회 참석. 지금 경연 있음. 물류 유통량 증가. 마비 없고 유통없음.'
        self.assertEqual(self.corrected(text), '어제 경연대회 참석. 지금 경련 있음. 물류 유통량 증가. 마비 없고 요통없음.')

    def test_term_plus_particle_and_ordinary_words_are_not_touched(self):
        self.assertEqual(self.corrector.suggest('혈당이 240이었던 적이 있음'), [])            # 혈당 + 조사
        self.assertEqual(self.corrector.suggest('증상이 시작될 당시에는 앉아 있었음'), [])      # '정상'은 따로 교정 대상이 아님
        self.assertEqual(self.corrector.suggest('증상이 있음'), [])
        self.assertEqual(self.corrector.suggest('속이 울렁거린다고 말합니다'), [])              # 용어 뒤에 올 수 없는 어미

    def test_clean_long_reports_get_no_suggestions_or_flags(self):
        reports = json.loads((ROOT / 'datasets' / 'voice' / 'team_long_reports_20261031.json').read_text(encoding='utf-8'))
        for case in reports['cases']:
            self.assertEqual(self.corrector.suggest(case['reference_text']), [], case['id'])
            self.assertEqual(ac.numeric_flags(case['reference_text']), [], case['id'])

    def test_context_must_fit_the_kind_of_term(self):
        self.assertEqual(self.corrected('채온 36.8도입니다'), '체온 36.8도입니다')          # 측정 항목 + 수치
        self.assertEqual(self.corrector.suggest('채온 없음'), [])                           # 측정 항목에 있음·없음은 문맥이 아님
        self.assertEqual(self.corrected('형통은 10분 전 시작했고'), '흉통은 10분 전 시작했고')  # 증상 + 발생 시점
        self.assertEqual(self.corrector.suggest('보통 3건으로 집계'), [])                    # 증상에 일반 숫자는 문맥이 아님
        self.assertEqual(self.corrector.suggest('부정과 정정을 구분하는 문장'), [])          # 'A과 B'의 B가 용어가 아님
        self.assertEqual(self.corrected('채원과 혈당은 빈 항목'), '체온과 혈당은 빈 항목')
        self.assertEqual(self.corrector.suggest('여기 나오는 대상자와 수치'), [])            # 띄어 쓰는 용어는 끝 낱말만 다를 수 있음
        self.assertEqual(self.corrector.suggest('그만큼 어지러웠다라는 표현'), [])           # 용어 뒤에 올 수 없는 어미

    def test_common_words_one_jamo_from_a_term_are_never_suggested(self):
        for text in ('호흡 이상 없음', '이상 있음', '수용 거절이 있으면', '자원 부족이 있어야', '추가 구조 요청', '손상 기전 외상', '정보가 포함되어 있다'):
            self.assertEqual(self.corrector.suggest(text), [], text)
        self.assertEqual(ac.LOOKALIKES & set(ac.load_vocabulary()), set())

    def test_far_or_ambiguous_words_are_not_guessed(self):
        self.assertEqual(self.corrector.suggest('등통과 호흡 곤란 있음'), [])       # 흉통과 두 자모가 다름
        self.assertEqual(self.corrector.suggest('고통 있음'), [])                   # 두통·요통·복통·흉통 중 고를 수 없음

    def test_status_word_is_never_replaced_by_a_symptom(self):
        # '의심'(미확인 표현)을 증상 '오심'으로 바꾸면 뜻이 뒤집힌다.
        for text in ('경련 실신 의심 아님', '흉통 의심. 복통 의심.', '통통 의심 복통 의심', '의심 있음'):
            self.assertEqual(self.corrector.suggest(text), [], text)

    def test_clean_development_scripts_get_no_suggestions(self):
        corpus = json.loads((ROOT / 'speech_eval' / 'corpus_dev.json').read_text(encoding='utf-8'))
        changed = [case['id'] for case in corpus['cases'] if self.corrector.suggest(case['reference_text'])]
        self.assertEqual(changed, [])


class NumericFlags(unittest.TestCase):
    def kinds(self, text):
        return [(f['kind'], f['original'], f.get('candidate', f.get('candidates'))) for f in ac.numeric_flags(text)]

    def test_round_number_followed_by_a_digit_sound_is_flagged_not_changed(self):
        self.assertEqual(self.kinds('산소포화도 80고 경증'), [('tens_then_unit', '80고', 89)])
        self.assertEqual(self.kinds('호흡 수 20이 추가해줘'), [('tens_then_unit', '20이', 22)])
        self.assertEqual(self.kinds('혈당 100이'), [('tens_then_unit', '100이', 102)])

    def test_particles_and_ranges_after_numbers_are_not_flagged(self):
        for text in ('맥박 90 이상', '혈압 90이하', '맥박 100이고 호흡 정상', '체온 40이며', '맥박 92회', '산소포화도 97퍼센트', '혈압 120에 80'):
            self.assertEqual(ac.numeric_flags(text), [], text)

    def test_numbers_without_a_measurement_label_nearby_are_not_flagged(self):
        for text in ('작성일은 9월 30일입니다', '160사례 중 140사례', '3월 20일 발생', '맥박은 정상입니다. 20이 넘는 사례'):
            self.assertEqual(ac.numeric_flags(text), [], text)
        self.assertEqual(self.kinds('혈압은 124의 80이, 맥박은 90입니다'), [('tens_then_unit', '80이', 82)])

    def test_subject_particle_and_copula_after_a_round_number_are_not_flagged(self):
        # '이'(주격 조사)와 '일'(서술격)은 뒤에 서술어·의존명사가 오면 숫자 2·1이 아니다.
        for text in ('혈압 120 80이 나왔어', '맥박이 90일 때 수정해 줘', '맥박 90이 맞아', '산소포화도 90일 경우 알려줘',
                     '맥박 100이 넘습니다', '혈당 100일때 다시 확인', '맥박 90이 정상 범위인지 미확인'):
            self.assertEqual(ac.numeric_flags(text), [], text)

    def test_particle_like_digit_is_flagged_only_where_the_value_ends(self):
        self.assertEqual(self.kinds('혈압은 132의 80이 산소포화도는 97%'), [('tens_then_unit', '80이', 82)])
        self.assertEqual(self.kinds('맥박 100일 산소 포화도 98%'), [('tens_then_unit', '100일', 101)])
        self.assertEqual(self.kinds('맥박 90일.'), [('tens_then_unit', '90일', 91)])
        self.assertEqual(self.kinds('맥박 90일 정정해줘'), [('tens_then_unit', '90일', 91)])
        # 조사로 읽을 수 없는 글자는 뒤에 무엇이 오든 그대로 표시한다.
        self.assertEqual(self.kinds('산소포화도 80고 나왔어'), [('tens_then_unit', '80고', 89)])

    def test_range_word_after_a_round_number_is_not_flagged(self):
        self.assertEqual(ac.numeric_flags('맥박90에서120사이'), [])
        self.assertEqual(self.kinds('맥박 120사 추가'), [('tens_then_unit', '120사', 124)])

    def test_native_tens_word_then_digit_is_flagged(self):
        self.assertEqual(self.kinds('맥박 아흔 2'), [('native_tens_then_digit', '아흔 2', 92)])
        self.assertEqual(ac.numeric_flags('맥박 아흔둘'), [])

    def test_merged_blood_pressure_is_flagged_with_plausible_splits_only(self):
        self.assertEqual(self.kinds('혈압 10060에'), [('merged_blood_pressure', '10060', [(100, 60)])])
        self.assertEqual(self.kinds('혈압 1876, 맥박 84'), [('merged_blood_pressure', '1876', [])])
        self.assertEqual(ac.numeric_flags('혈압 120/80'), [])

    def test_clean_development_scripts_get_no_flags(self):
        corpus = json.loads((ROOT / 'speech_eval' / 'corpus_dev.json').read_text(encoding='utf-8'))
        self.assertEqual([case['id'] for case in corpus['cases'] if ac.numeric_flags(case['reference_text'])], [])


class RecordingPackage(unittest.TestCase):
    def test_corrected_copy_keeps_rows_and_labels_the_condition(self):
        package = {'schema_version': 1, 'dataset_id': 'd', 'corpus_sha256': 'x', 'recordings': [
            {'recording_id': 'R1', 'case_id': 'V049', 'source': 'synthetic_corruption', 'engine': 'e', 'condition': 'tts', 'status': 'ok', 'hypothesis': '흥통 의심'},
            {'recording_id': 'R2', 'case_id': 'V063', 'source': 'synthetic_corruption', 'engine': 'e', 'condition': 'tts', 'status': 'ok', 'hypothesis': '산소포화도 80고'}]}
        corrected, details = ac.correct_recordings(package)
        self.assertEqual([r['hypothesis'] for r in corrected['recordings']], ['흉통 의심', '산소포화도 80고'])   # 숫자는 그대로
        self.assertEqual({r['condition'] for r in corrected['recordings']}, {'tts+term-correction'})
        self.assertEqual(package['recordings'][0]['hypothesis'], '흥통 의심')                                  # 원본 불변
        self.assertEqual(details[0]['suggestions'][0]['term'], '흉통')
        self.assertEqual(details[1]['numeric_flags'][0]['candidate'], 89)


class RecordingCLI(unittest.TestCase):
    def test_distinct_files_contain_corrected_package_and_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output, details = (root / name for name in ('source.json', 'corrected.json', 'details.json'))
            source.write_text(json.dumps({'recordings': []}), encoding='utf-8')

            with redirect_stdout(io.StringIO()):
                ac.main(['--recordings', str(source), '--output', str(output), '--details', str(details)])

            self.assertEqual(json.loads(output.read_text(encoding='utf-8')), {'recordings': []})
            self.assertEqual(json.loads(details.read_text(encoding='utf-8')), [])

    def test_rejects_aliased_output_paths_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'nested').mkdir()
            source = root / 'source.json'
            source.write_text(json.dumps({'recordings': []}), encoding='utf-8')
            output = root / 'result.json'
            alias = root / 'nested' / '..' / 'result.json'

            with redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as error:
                ac.main(['--recordings', str(source), '--output', str(output), '--details', str(alias)])

            self.assertEqual(error.exception.code, 2)
            self.assertIn('--output', stderr.getvalue())
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
