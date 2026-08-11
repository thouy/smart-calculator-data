# 공고 원본 데이터 (사람이 채우는 곳)

앱이 읽는 `assets/ev_subsidy_table.json`은 **여기 있는 파일로부터 자동 생성**된다.
JSON을 직접 고치지 말고 이 폴더의 파일을 고칠 것 — 검증 게이트를 거치지 않으면
금액 오타가 그대로 사용자에게 나간다.

```
data/ev_subsidy_meta.json     ─┐
data/ev_subsidy_models.csv     ├─► tool/build_ev_subsidy.py ─► assets/ev_subsidy_table.json
data/ev_subsidy_regions.csv   ─┘        (GitHub Actions)              │
                                                                      ▼
                                                    앱이 실행할 때마다 원격에서 읽음
```

## 연 1회 갱신 절차

1. **공고 확인** — ev.or.kr은 자동 수집을 막아 두어(pnp4web) 브라우저로 직접 본다.
   - 차종별 국비: 무공해차 통합누리집 → 구매 및 지원 → 차종별 보조금
   - 지자체별 지방비: 같은 메뉴 → 지자체별 보조금 현황
   - 가격 구간·추가지원 요건: 환경부 「보조금 업무처리지침」 공고문
2. `*.example` 파일을 확장자 없이 복사한다 (최초 1회).
   ```
   cp data/ev_subsidy_meta.json.example  data/ev_subsidy_meta.json
   cp data/ev_subsidy_models.csv.example data/ev_subsidy_models.csv
   cp data/ev_subsidy_regions.csv.example data/ev_subsidy_regions.csv
   ```
3. 공고값으로 채운다. `"직접 입력"` 행은 **넣지 않는다** — 빌더가 자동으로 앞에 붙인다.
4. `updatedAt`을 갱신하고 `effectiveLabel`에서 `임시값` 문구를 지운다.
5. 커밋·푸시 → **Actions가 검증 후 JSON을 자동 커밋**한다.
6. 사용자는 다음 앱 실행에서 반영된 값을 본다. **앱 배포 불필요.**

로컬에서 미리 확인하려면:
```
python tool/build_ev_subsidy.py --check     # 파일을 쓰지 않고 검증만
python tool/build_ev_subsidy.py             # 실제 생성
python tool/test_build_ev_subsidy.py        # 검증 규칙 자체 테스트
```

## 검증 게이트가 막아 주는 것

| 검사 | 막는 사고 |
|---|---|
| 국비 0원 / 2,000만원 초과 | 자릿수 오타 (500만 → 5,000만) |
| 지방비 1,000만원 초과 | 자릿수 오타 |
| 판매가 ≤ 국비 | 열 밀림(국비 칸에 다른 값) |
| 차종·지역명 중복/공백 | 복붙 실수 |
| `updatedAt`이 올해가 아님 | 갱신을 잊고 커밋 |
| `effectiveLabel`에 `임시값` 잔존 | 임시 데이터 배포 |
| `priceTiers` 순서·마지막 칸 | 구간표 편집 실수 |
| 이전 표 대비 ±50% 급변 | 열 밀림 (형식 검사로는 안 잡힘) |

검증에 걸리면 **JSON을 건드리지 않고** Actions가 실패한다. 잘못된 금액이 배포되는 것보다
배포가 멈추는 편이 낫다.

요율이 실제로 크게 바뀐 해라면 Actions → `전기차 보조금 표 빌드` → **Run workflow** 에서
`allow_drift`를 켜고 실행한다.

## 주의

- `regions`는 **시·군·구 단위**로 넣을수록 정확하다. 광역 대표값만 넣으면 같은 시·도
  안에서도 실제와 어긋난다 — 그래서 앱에 `직접 입력` 선택지와 면책 문구를 둔다.
- 예산 소진에 따른 **조기 마감은 이 표로 알 수 없다.** 앱은 안내 문구로만 다룬다.
