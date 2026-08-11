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
        "priceTiers": [
            {"limit": 53000000, "rate": 1.0},
            {"limit": 85000000, "rate": 0.5},
            {"limit": None, "rate": 0.0},
        ],
    }
    m.update(over)
    return m


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
        "priceTiers 마지막 칸 limit이 null이 아니면 거부",
        lambda: b.validate_meta(
            meta(priceTiers=[{"limit": 53000000, "rate": 1.0}]), today=TODAY),
        contains="null",
    )
    expect_error(
        "priceTiers rate 오름차순이면 거부",
        lambda: b.validate_meta(meta(priceTiers=[
            {"limit": 53000000, "rate": 0.5},
            {"limit": None, "rate": 1.0},
        ]), today=TODAY),
        contains="내림차순",
    )

    print("models 검증")
    expect_ok(
        "정상 행",
        lambda: b.parse_models([{"name": "기아 EV3", "nationalBase": "4700000"}]),
    )
    expect_ok(
        "천단위 쉼표·'원' 허용",
        lambda: b.parse_models([{"name": "A", "nationalBase": "4,700,000원"}]),
    )
    expect_error(
        "국비 자릿수 오타(상한 초과) 거부",
        lambda: b.parse_models([{"name": "A", "nationalBase": "50000000"}]),
        contains="자릿수",
    )
    expect_error(
        "국비 0 거부",
        lambda: b.parse_models([{"name": "A", "nationalBase": "0"}]),
        contains="0입니다",
    )
    expect_error(
        "차종명 중복 거부",
        lambda: b.parse_models([
            {"name": "A", "nationalBase": "4000000"},
            {"name": "A", "nationalBase": "4100000"},
        ]),
        contains="중복",
    )
    expect_error(
        "판매가가 국비보다 작으면 열 밀림으로 보고 거부",
        lambda: b.parse_models(
            [{"name": "A", "nationalBase": "4700000", "price": "4200000"}]),
        contains="열이 밀리",
    )
    expect_error(
        "숫자가 아니면 거부",
        lambda: b.parse_models([{"name": "A", "nationalBase": "사백칠십만"}]),
        contains="숫자가 아닙니다",
    )

    print("regions 검증")
    expect_ok("정상 행", lambda: b.parse_regions([{"name": "서울", "local": "1800000"}]))
    expect_error(
        "지방비 자릿수 오타 거부",
        lambda: b.parse_regions([{"name": "서울", "local": "18000000"}]),
        contains="자릿수",
    )
    expect_error(
        "지역명 중복 거부",
        lambda: b.parse_regions([
            {"name": "서울", "local": "1800000"},
            {"name": "서울", "local": "1900000"},
        ]),
        contains="중복",
    )

    print("급변(drift) 검사")
    prev = {"models": [{"name": "A", "nationalBase": 5000000}], "regions": []}
    same = {"models": [{"name": "A", "nationalBase": 5200000}], "regions": []}
    shifted = {"models": [{"name": "A", "nationalBase": 52000000}], "regions": []}
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
