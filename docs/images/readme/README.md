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
