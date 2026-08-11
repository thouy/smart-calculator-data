#!/usr/bin/env python3
"""전기차 보조금 표 빌더.

`data/ev_subsidy_rows.csv`(지역 × 모델 확정액) + `data/ev_subsidy_meta.json`을
앱이 읽는 `assets/ev_subsidy_table.json`으로 변환한다.

CSV는 `tool/extract_ev_pdf.py`가 무공해차 통합누리집 팝업 PDF에서 뽑는다.
ev.or.kr이 자동 수집을 막아 두어(pnp4web) 수집만 사람이 하고 변환·검증·배포는
자동화한다는 설계다.

표의 국비·지방비는 **가격 구간이 이미 반영된 확정액**이므로 앱은 계수를 다시
곱하지 않는다. 빌더도 값을 가공하지 않고 검증만 한다.

검증에 실패하면 **출력 파일을 건드리지 않고** 0이 아닌 종료 코드로 끝난다.
잘못된 금액이 사용자에게 나가는 것보다 배포가 멈추는 편이 낫다.

사용:
  python tool/build_ev_subsidy.py [--check] [--allow-drift]
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META = os.path.join(ROOT, "data", "ev_subsidy_meta.json")
ROWS = os.path.join(ROOT, "data", "ev_subsidy_rows.csv")
OUT = os.path.join(ROOT, "assets", "ev_subsidy_table.json")

# 자릿수 오타(500만 → 5,000만)를 잡기 위한 상한. 공고값이 이를 넘는 해가 오면
# 여기를 올리는 편이 조용히 틀린 값을 내보내는 것보다 낫다.
MAX_NATIONAL = 20_000_000
MAX_LOCAL = 20_000_000
DRIFT_RATIO = 0.5  # 이전 표 대비 ±50% 넘게 움직이면 열 밀림을 의심한다.

REQUIRED = ["region", "maker", "model", "national", "local"]


class BuildError(Exception):
    """검증 실패. 메시지가 그대로 CI 로그에 찍힌다."""


def _money(value: str, *, where: str, field: str, allow_zero: bool = True) -> float:
    raw = (value or "").strip().replace(",", "").replace("원", "")
    if raw == "":
        if allow_zero:
            return 0.0
        raise BuildError(f"{where}: '{field}' 값이 비어 있습니다.")
    try:
        n = float(raw)
    except ValueError:
        raise BuildError(f"{where}: '{field}' 값이 숫자가 아닙니다 → {value!r}")
    if n < 0:
        raise BuildError(f"{where}: '{field}'가 음수입니다 → {n:,.0f}")
    return n


def parse_rows() -> list[dict]:
    """CSV 파일을 읽어 [parse_rows_from]에 넘긴다."""
    if not os.path.exists(ROWS):
        raise BuildError(f"입력 파일이 없습니다: {os.path.relpath(ROWS, ROOT)}")
    with open(ROWS, encoding="utf-8-sig", newline="") as f:
        return parse_rows_from(list(csv.DictReader(f)))


def parse_rows_from(raw: list[dict]) -> list[dict]:
    """파싱·검증 본체. 파일과 분리해 두어 자체 테스트가 값을 직접 넣을 수 있다."""
    if not raw:
        raise BuildError("ev_subsidy_rows.csv: 데이터 행이 없습니다.")
    missing = [c for c in REQUIRED if c not in raw[0]]
    if missing:
        raise BuildError(
            f"ev_subsidy_rows.csv: 필수 컬럼 누락 {missing} (현재 {list(raw[0])})")

    out, seen = [], set()
    for i, r in enumerate(raw, start=2):
        where = f"rows.csv {i}행"
        region = (r.get("region") or "").strip()
        maker = (r.get("maker") or "").strip()
        model = (r.get("model") or "").strip()
        if not region or not model:
            raise BuildError(f"{where}: 지역명 또는 모델명이 비어 있습니다.")
        key = (region, maker, model)
        if key in seen:
            raise BuildError(f"{where}: 같은 지역에 같은 모델이 중복됩니다 → {region} {maker} {model}")
        seen.add(key)

        national = _money(r.get("national", ""), where=where, field="national",
                          allow_zero=False)
        local = _money(r.get("local", ""), where=where, field="local")
        if national == 0:
            raise BuildError(f"{where}: 국비가 0입니다 → {model}")
        if national > MAX_NATIONAL:
            raise BuildError(
                f"{where}: 국비 {national:,.0f}원이 상한({MAX_NATIONAL:,})을 넘습니다. "
                f"자릿수 오타가 아닌지 확인하세요 → {model}")
        if local > MAX_LOCAL:
            raise BuildError(
                f"{where}: 지방비 {local:,.0f}원이 상한({MAX_LOCAL:,})을 넘습니다 → {model}")

        out.append({
            "region": region,
            "maker": maker,
            "model": model,
            "national": national,
            "local": local,
            "convNational": _money(r.get("convNational", ""), where=where,
                                   field="convNational"),
            "convLocal": _money(r.get("convLocal", ""), where=where,
                                field="convLocal"),
        })
    return out


def validate_meta(meta: dict, *, today: datetime.date) -> None:
    label = str(meta.get("effectiveLabel", ""))
    if not label:
        raise BuildError("meta: effectiveLabel이 비어 있습니다.")
    if "임시값" in label:
        raise BuildError(
            "meta: effectiveLabel에 '임시값'이 남아 있습니다. 공고값으로 교체한 뒤 "
            "이 문구를 지우세요.")

    updated = str(meta.get("updatedAt", "")).strip()
    if not (len(updated) == 7 and updated[4] == "." and updated[:4].isdigit()
            and updated[5:].isdigit()):
        raise BuildError(f"meta: updatedAt 형식은 YYYY.MM 이어야 합니다 → {updated!r}")
    if int(updated[:4]) != today.year:
        raise BuildError(
            f"meta: updatedAt이 올해({today.year})가 아닙니다 → {updated}. "
            "보조금 공고는 해마다 새로 나옵니다. 갱신을 잊지 않았는지 확인하세요.")

    rate = meta.get("youthBonusRate")
    if not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
        raise BuildError(f"meta: youthBonusRate는 0~1 이어야 합니다 → {rate!r}")


def check_drift(rows: list[dict], previous: dict | None) -> list[str]:
    """이전 표와 비교해 급변한 항목을 경고 문자열로 돌려준다.

    열이 한 칸 밀리면 금액이 배 단위로 튀는데, 형식 검사만으로는 잡히지 않는다.
    """
    if not previous:
        return []
    old = {(r.get("region"), r.get("maker"), r.get("model")): r
           for r in previous.get("rows", [])}
    warnings = []
    for r in rows:
        before = old.get((r["region"], r["maker"], r["model"]))
        if not before:
            continue
        for field in ("national", "local"):
            b, a = before.get(field, 0), r[field]
            if b and abs(a - b) > b * DRIFT_RATIO:
                warnings.append(
                    f"{r['region']} {r['model']} {field} {b:,.0f} → {a:,.0f} "
                    f"({(a / b - 1) * 100:+.0f}%)")
    return warnings


def build(*, today: datetime.date, allow_drift: bool) -> dict:
    if not os.path.exists(META):
        raise BuildError(f"입력 파일이 없습니다: {os.path.relpath(META, ROOT)}")
    with open(META, encoding="utf-8") as f:
        meta = json.load(f)
    validate_meta(meta, today=today)

    rows = parse_rows()

    table = {k: v for k, v in meta.items() if k != "_note"}
    table["rows"] = rows

    previous = None
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                previous = json.load(f)
        except (OSError, json.JSONDecodeError):
            previous = None

    drift = check_drift(rows, previous)
    if drift:
        joined = "\n  - ".join(drift[:20])
        more = f"\n  ... 외 {len(drift) - 20}건" if len(drift) > 20 else ""
        if allow_drift:
            print(f"[warn] 이전 표 대비 급변 항목(허용됨):\n  - {joined}{more}")
        else:
            raise BuildError(
                "이전 표 대비 ±50%를 넘게 움직인 항목이 있습니다. 열이 밀리지 "
                f"않았는지 확인하고, 실제 변경이라면 --allow-drift로 다시 실행하세요:"
                f"\n  - {joined}{more}")
    return table


def _force_utf8_output() -> None:
    """Windows 콘솔 기본 코드페이지(cp949)에서 한글 출력이 죽는 것을 막는다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str]) -> int:
    _force_utf8_output()
    ap = argparse.ArgumentParser(description="전기차 보조금 표 빌더")
    ap.add_argument("--check", action="store_true", help="파일을 쓰지 않고 검증만")
    ap.add_argument("--allow-drift", action="store_true", help="급변 검사 건너뜀")
    args = ap.parse_args(argv)

    try:
        table = build(today=datetime.date.today(), allow_drift=args.allow_drift)
    except BuildError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    regions = sorted({r["region"] for r in table["rows"]})
    summary = (f"행 {len(table['rows'])} · 지역 {len(regions)} "
               f"({table['updatedAt']} 기준)")

    body = json.dumps(table, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        print(f"[ok] 검증 통과 — {summary}")
        return 0

    old = None
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = f.read()
    if old == body:
        print("[ok] 변경 없음")
        return 0
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    print(f"[ok] {os.path.relpath(OUT, ROOT)} 갱신 — {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
