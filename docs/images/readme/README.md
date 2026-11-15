# README 화면 자료

2026-11-15에 저장소 코드 `26170ee1a79688df3aff91a84ab3200df3ec9f58`을 실행해 브라우저에서 직접 캡처했다. 원본 화면의 정보나 상태를 이미지 편집으로 바꾸지 않았다.

| 파일 | 화면과 상태 |
| --- | --- |
| [paramedic-note.jpg](paramedic-note.jpg) | `/paramedic/?flow=field`에서 가상 현장 설명을 텍스트로 입력한 대화형 이송 노트 |
| [hospital-inquiry.jpg](hospital-inquiry.jpg) | `/hospital/?hid=H01`에서 가상 환자 두 명의 문의와 새 문의 알림 확인 |
| [patient-transport.jpg](patient-transport.jpg) | `README-DEMO-01`의 H01 수용·담당자 목적지 확정·출발 후 이송 화면 |
| [hospital-arrived.jpg](hospital-arrived.jpg) | 같은 환자를 병원 화면에서 도착 확인한 뒤의 기록 |

## 촬영 조건

- 새 가상 DB와 `server.team_runtime --mode voice-only`를 사용했다. 자동 문의·배정 워커는 비활성이다.
- 병원 H01·H02와 환자 `README-DEMO-01`·`README-DEMO-02`는 합성 시연 데이터다. 실제 환자 정보나 외부 병원 전송은 없다.
- 구급대 노트는 별도 입력 예시다. 병원·이송의 세 화면은 동일한 `README-DEMO-01` 이송 건이며, 문의 생성 후 수용·확정·출발·도착을 직접 조작했다.
- 촬영용 현장 설명: “47세 남성, 20분 전부터 흉통과 식은땀이 있습니다. 의식 명료, 호흡 정상입니다. 혈압 120에 80, 맥박 92, 산소포화도 98퍼센트, 체온 36.7도입니다.”
- 마이크를 통한 인식 성능, 실제 이동시간, 임상 정확도, 자동 배정 성능을 측정하는 캡처가 아니다. 연구 수치는 [별도 실험 근거](../../ALLOCATION_PRESENTATION_EVIDENCE_20261105.md)를 따른다.

[프로젝트 README로 돌아가기](../../../README.md)

## 시뮬레이션 화면과 비교 그래프

2026-11-15에 코드 `a73dd5320fe06205be6530b4ff4a56e51fada1ba`의 `simulation.viewer_server`를 `--reuse-data`로 실행해 캡처했다. 기존 저장 재생 자료를 사용했으며 새 알고리즘 성능 실험을 실행한 것은 아니다.

| 파일 | 화면과 범위 |
| --- | --- |
| [simulation-replay.jpg](simulation-replay.jpg) | `lab.html?scenario=road-online-simultaneous#replay`, 경과 24:00, 전체 환자·전체 정책, ‘환자 위치 보기’로 이동 위치를 함께 표시 |
| [simulation-comparison.jpg](simulation-comparison.jpg) | `lab.html?scenario=online-large_simultaneous-11101#compare`, 관측 종료 90:00. 합성 환자 14명의 5규칙 최종 배정 |
| [algorithm-comparison.png](algorithm-comparison.png) · [SVG](algorithm-comparison.svg) | 2026-10-13의 합성 검증 108시나리오·규칙당 433명 재집계. Matplotlib로 만든 3개 지표 비교 그래프 |

도로 화면은 실제 병원 위치와 저장된 카카오모빌리티 일반 자동차 경로·예상시간을 사용한 설명용 시나리오다. 병원 역량·자원·수용 응답·환자는 가상이다. 배경은 OpenStreetMap contributors, 경계는 geoBoundaries / citypopulation.de(CC BY 3.0, 2020)이며 원래 화면의 출처 표기를 보존했다. [도로 시연과 출처](../../ROAD_ONLINE_REPLAY.md) · [지도 배경 출처](../../../simulation/viewer/assets/README.md)

도로 화면과 108시나리오 그래프는 서로 다른 자료다. 결과 비교 화면은 108시나리오 중 해당 유형의 첫 검증 seed를 사용하며, 전체 평균을 대표하는 화면으로 해석하지 않는다. 화면의 A1~A5는 그래프의 R0·R1·R2·R3·C1에 대응한다.

그래프의 미배정·문의는 각 규칙의 전체 합계다. 도착 제한 경과 평균은 도착 시 `final_arrive_t - occur_t`, 미도착 시 `observation_end_t - occur_t`를 전체 환자 수로 나눈 값이다. 실제 환자의 효과나 모든 환자의 지연 감소를 뜻하지 않는다. [사례별 수치·원본 해시](../../evidence/20261115/online-allocation-comparison.json) · [실험 해석](../../ONLINE_ALLOCATION.md)

### 그래프 재생성

저장소 루트에서 실행한다. 검증 명령은 Python 표준 라이브러리만 사용하고, 이미지 생성에는 Matplotlib과 한국어 글꼴이 필요하다.

```powershell
python scripts/docs/build_readme_comparison.py --verify-only
python -m pip install matplotlib
python scripts/docs/build_readme_comparison.py
```

Windows는 맑은 고딕을 사용한다. 다른 환경은 `--font <한국어 글꼴 파일>`을 지정한다. 이번 PNG·SVG는 Python 3.12.14, Matplotlib 3.11.2, 맑은 고딕으로 생성했다. 기본 명령은 Git에 포함한 사례별 발췌 JSON을 읽는다. 원본 재추출은 `--extract-source <원본 JSON 경로>`를 쓰며, 고정 SHA-256이 일치해야 한다. 원본 대용량 JSON은 Git에 포함하지 않았다.
