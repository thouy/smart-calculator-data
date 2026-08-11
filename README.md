# smart-calculator-data

[스마트 계산기](https://play.google.com/store/apps/details?id=com.thouy.smartcalculator) 앱이
읽는 **요율·세율 표**를 서빙하는 공개 저장소.

앱은 실행할 때마다 `assets/*.json`을 익명으로 내려받아 캐시한다. 따라서 **이 저장소는
반드시 public이어야 한다** — 비공개면 GitHub가 404를 돌려주고, 앱은 조용히 내장값으로
폴백해 개정 내용이 영영 반영되지 않는다. (실제로 그 상태였던 적이 있어 앱에
`데이터 갱신 상태` 진단 화면을 두었다.)

앱 소스는 별도 비공개 저장소에 있다. 여기에는 데이터와 빌드 도구만 둔다.

## 서빙 중인 표

| 파일 | 내용 | 갱신 시기 |
|---|---|---|
| `assets/tax_table.json` | 소득세 누진세율·공제 | 매년 1월 |
| `assets/insurance_rates.json` | 4대보험 요율 | 매년 1월 |
| `assets/real_estate_table.json` | 중개보수·취득세·양도세·전월세 전환 | 매년 1월 + 대책 시 |
| `assets/stock_tax_table.json` | 주식 양도소득세 | 매년 1월 |
| `assets/ev_subsidy_table.json` | 전기차 보조금 (**자동 빌드**) | 매년 1~3월 |

앱이 읽는 주소 형식:
```
https://raw.githubusercontent.com/thouy/smart-calculator-data/main/assets/<파일>
```

## 전기차 보조금만 빌드 파이프라인이 있다

금액 정본인 ev.or.kr이 자동 수집을 막아 두어(pnp4web), **수집만 사람이 하고 변환·검증·
배포는 자동화**한다. `data/`에 공고 CSV를 올리면 GitHub Actions가 검증 후
`assets/ev_subsidy_table.json`을 자동 커밋한다. 절차는 [`data/README.md`](data/README.md).

검증에 걸리면 JSON을 건드리지 않고 실패한다 — 잘못된 금액이 배포되는 것보다 배포가
멈추는 편이 낫다.

## 나머지 표는 직접 수정

`assets/*.json`을 고쳐 `main`에 푸시하면 사용자는 다음 앱 실행에서 반영된 값을 본다.
**앱 배포 불필요.** 각 표는 `effectiveLabel`(적용 기준)과 `updatedAt`(기준일)을 갖고,
앱 화면에 그대로 표시된다 — 고칠 때 함께 갱신할 것.

## 주의

- 모든 금융 계산은 **간이 추정치**이며 앱 화면에 면책이 표시된다.
- 표에 개인정보·비밀은 넣지 않는다. 공개 저장소다.
