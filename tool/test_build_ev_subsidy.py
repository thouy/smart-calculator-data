#!/usr/bin/env python3
"""build_ev_subsidy.py 검증 규칙 자체 테스트.

CI에서 빌더보다 먼저 돌려, 검증 게이트가 **실제로 막는지**를 확인한다.
게이트가 조용히 망가지면 오타가 그대로 사용자에게 나가므로 여기서 잡는다.

  python tool/test_build_ev_subsidy.py
"""

from __future__ import annotations

import datetime
import sys

import build_ev_subsidy as b

TODAY = datetime.date(2026, 3, 1)
_failures: list[str] = []


def expect_error(name: str, fn, *, contains: str) -> None:
    try:
        fn()
    except b.BuildError as e:
        if contains in str(e):
            print(f"  ok   {name}")
        else:
            _failures.append(f"{name}: 메시지에 {contains!r}가 없음 → {e}")
    except Exception as e:  # noqa: BLE001
        _failures.append(f"{name}: BuildError가 아닌 예외 → {type(e).__name__}: {e}")
    else:
        _failures.append(f"{name}: 통과해선 안 되는데 통과함")


def expect_ok(name: str, fn) -> None:
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        _failures.append(f"{name}: 실패하면 안 되는데 실패 → {e}")
    else:
        print(f"  ok   {name}")


def meta(**over) -> dict:
    m = {
        "effectiveLabel": "2026년 기준",
        "updatedAt": "2026.02",
        "youthBonusRate": 0.2,
    }
    m.update(over)
    return m


def row(**over) -> dict:
    r = {
        "region": "서울특별시",
        "maker": "기아",
        "model": "EV3 GT",
        "national": "2480000",
        "local": "740000",
        "convNational": "500000",
        "convLocal": "150000",
    }
    r.update(over)
    return r


def main() -> int:
    b._force_utf8_output()

    print("meta 검증")
    expect_ok("정상 meta", lambda: b.validate_meta(meta(), today=TODAY))
    expect_error(
        "임시값 문구가 남아 있으면 거부",
        lambda: b.validate_meta(
            meta(effectiveLabel="2026년 기준(임시값 · 공고 확인 필요)"), today=TODAY),
        contains="임시값",
    )
    expect_error(
        "updatedAt이 작년이면 거부(갱신 잊음)",
        lambda: b.validate_meta(meta(updatedAt="2025.12"), today=TODAY),
        contains="올해",
    )
    expect_error(
        "updatedAt 형식 오류 거부",
        lambda: b.validate_meta(meta(updatedAt="2026-02"), today=TODAY),
        contains="YYYY.MM",
    )
    expect_error(
        "youthBonusRate 범위 밖 거부",
        lambda: b.validate_meta(meta(youthBonusRate=1.5), today=TODAY),
        contains="0~1",
    )

    print("rows 검증")
    expect_ok("정상 행", lambda: b.parse_rows_from([row()]))
    expect_ok(
        "천단위 쉼표·'원' 허용",
        lambda: b.parse_rows_from([row(national="2,480,000원")]),
    )
    expect_ok(
        "전환지원금은 비어 있어도 된다(승합은 해당 열이 없다)",
        lambda: b.parse_rows_from([row(convNational="", convLocal="")]),
    )
    expect_error(
        "필수 컬럼 누락 거부",
        lambda: b.parse_rows_from([{"region": "서울특별시", "model": "EV3"}]),
        contains="필수 컬럼 누락",
    )
    expect_error(
        "빈 목록 거부",
        lambda: b.parse_rows_from([]),
        contains="데이터 행이 없습니다",
    )
    expect_error(
        "국비 자릿수 오타(상한 초과) 거부",
        lambda: b.parse_rows_from([row(national="50000000")]),
        contains="자릿수",
    )
    expect_error(
        "국비 0 거부",
        lambda: b.parse_rows_from([row(national="0")]),
        contains="국비가 0",
    )
    expect_error(
        "지방비 자릿수 오타 거부",
        lambda: b.parse_rows_from([row(local="99000000")]),
        contains="상한",
    )
    expect_error(
        "같은 지역·같은 모델 중복 거부",
        lambda: b.parse_rows_from([row(), row()]),
        contains="중복",
    )
    expect_ok(
        "지역이 다르면 같은 모델이어도 통과",
        lambda: b.parse_rows_from([row(), row(region="부산광역시", local="1040000")]),
    )
    expect_error(
        "지역명이 비면 거부",
        lambda: b.parse_rows_from([row(region="")]),
        contains="비어 있습니다",
    )
    expect_error(
        "숫자가 아니면 거부",
        lambda: b.parse_rows_from([row(national="이백사십팔만")]),
        contains="숫자가 아닙니다",
    )
    expect_error(
        "음수 거부",
        lambda: b.parse_rows_from([row(local="-100")]),
        contains="음수",
    )

    print("급변(drift) 검사")
    prev = {"rows": [{"region": "서울특별시", "maker": "기아", "model": "EV3 GT",
                     "national": 2480000, "local": 740000}]}
    same = [{"region": "서울특별시", "maker": "기아", "model": "EV3 GT",
             "national": 2600000, "local": 780000}]
    shifted = [{"region": "서울특별시", "maker": "기아", "model": "EV3 GT",
                "national": 24800000, "local": 740000}]
    if b.check_drift(same, prev):
        _failures.append("소폭 변동인데 급변으로 잡힘")
    else:
        print("  ok   소폭 변동은 통과")
    if b.check_drift(shifted, prev):
        print("  ok   10배 튄 값은 급변으로 잡힘")
    else:
        _failures.append("10배 튀었는데 급변으로 안 잡힘")
    if b.check_drift(shifted, None):
        _failures.append("이전 표가 없으면 비교하지 말아야 함")
    else:
        print("  ok   이전 표 없으면 비교 안 함")
    if b.check_drift([{"region": "부산광역시", "maker": "기아", "model": "EV3 GT",
                       "national": 24800000, "local": 0}], prev):
        _failures.append("이전 표에 없던 (지역,모델)은 비교 대상이 아니다")
    else:
        print("  ok   새로 추가된 행은 급변 비교에서 제외")

    print()
    if _failures:
        for f in _failures:
            print(f"FAIL {f}", file=sys.stderr)
        print(f"\n{len(_failures)}건 실패", file=sys.stderr)
        return 1
    print("모든 검증 규칙 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
