#!/usr/bin/env python3
"""전기차 보조금 표 빌더.

사람이 올린 CSV/메타 파일을 앱이 읽는 `assets/ev_subsidy_table.json`으로 변환한다.
ev.or.kr이 자동 수집을 막아 두어(pnp4web) 수집만 사람이 하고, 변환·검증·배포는
자동화한다는 설계다.

입력
  data/ev_subsidy_meta.json     기준 라벨·가격구간·추가지원·세제
  data/ev_subsidy_models.csv    name,nationalBase[,price]
  data/ev_subsidy_regions.csv   name,local

출력
  assets/ev_subsidy_table.json

검증에 실패하면 **출력 파일을 건드리지 않고** 0이 아닌 종료 코드로 끝난다.
잘못된 금액이 사용자에게 나가는 것보다 배포가 멈추는 편이 낫다.

사용:
  python tool/build_ev_subsidy.py [--check] [--allow-drift]
    --check        파일을 쓰지 않고 검증만 (CI의 사전 점검용)
    --allow-drift  이전 표 대비 급변 검사를 건너뜀 (요율이 실제로 크게 바뀐 해)
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
MODELS = os.path.join(ROOT, "data", "ev_subsidy_models.csv")
REGIONS = os.path.join(ROOT, "data", "ev_subsidy_regions.csv")
OUT = os.path.join(ROOT, "assets", "ev_subsidy_table.json")

# 앱이 "직접 입력"을 첫 항목으로 기대한다(표에 없는 차종·지자체를 위한 탈출구).
# CSV에는 실제 공고 데이터만 넣고 여기서 자동으로 앞에 붙인다.
MANUAL = "직접 입력"

# 자릿수 오타(500만 → 5,000만)를 잡기 위한 상한. 공고값이 이를 넘는 해가 오면
# 여기를 올리는 편이 조용히 틀린 값을 내보내는 것보다 낫다.
MAX_NATIONAL = 20_000_000
MAX_LOCAL = 10_000_000
DRIFT_RATIO = 0.5  # 이전 표 대비 ±50% 넘게 움직이면 열 밀림을 의심한다.


class BuildError(Exception):
    """검증 실패. 메시지가 그대로 CI 로그에 찍힌다."""


def _read_csv(path: str, required: list[str]) -> list[dict]:
    if not os.path.exists(path):
        raise BuildError(f"입력 파일이 없습니다: {os.path.relpath(path, ROOT)}")
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f)]
    if not rows:
        raise BuildError(f"{os.path.relpath(path, ROOT)}: 데이터 행이 없습니다.")
    missing = [c for c in required if c not in rows[0]]
    if missing:
        raise BuildError(
            f"{os.path.relpath(path, ROOT)}: 필수 컬럼 누락 {missing} "
            f"(현재 컬럼: {list(rows[0])})"
        )
    return rows


def _money(value: str, *, where: str, field: str) -> float:
    raw = (value or "").strip().replace(",", "").replace("원", "")
    if raw == "":
        raise BuildError(f"{where}: '{field}' 값이 비어 있습니다.")
    try:
        n = float(raw)
    except ValueError:
        raise BuildError(f"{where}: '{field}' 값이 숫자가 아닙니다 → {value!r}")
    if n < 0:
        raise BuildError(f"{where}: '{field}'가 음수입니다 → {n:,.0f}")
    return n


def parse_models(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for i, r in enumerate(rows, start=2):  # 2 = 헤더 다음 줄
        where = f"models.csv {i}행"
        name = (r.get("name") or "").strip()
        if not name:
            raise BuildError(f"{where}: 차종명이 비어 있습니다.")
        if name in seen:
            raise BuildError(f"{where}: 차종명 중복 → {name}")
        seen.add(name)

        national = _money(r.get("nationalBase", ""), where=where, field="nationalBase")
        if national == 0:
            raise BuildError(f"{where}: 국비가 0입니다 → {name}")
        if national > MAX_NATIONAL:
            raise BuildError(
                f"{where}: 국비 {national:,.0f}원이 상한({MAX_NATIONAL:,})을 넘습니다. "
                f"자릿수 오타가 아닌지 확인하세요 → {name}"
            )

        entry = {"name": name, "nationalBase": national}
        price_raw = (r.get("price") or "").strip()
        if price_raw:
            price = _money(price_raw, where=where, field="price")
            if price <= national:
                raise BuildError(
                    f"{where}: 판매가({price:,.0f})가 국비({national:,.0f}) 이하입니다. "
                    f"열이 밀리지 않았는지 확인하세요 → {name}"
                )
            entry["price"] = price
        out.append(entry)
    return out


def parse_regions(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for i, r in enumerate(rows, start=2):
        where = f"regions.csv {i}행"
        name = (r.get("name") or "").strip()
        if not name:
            raise BuildError(f"{where}: 지역명이 비어 있습니다.")
        if name in seen:
            raise BuildError(f"{where}: 지역명 중복 → {name}")
        seen.add(name)

        local = _money(r.get("local", ""), where=where, field="local")
        if local > MAX_LOCAL:
            raise BuildError(
                f"{where}: 지방비 {local:,.0f}원이 상한({MAX_LOCAL:,})을 넘습니다. "
                f"자릿수 오타가 아닌지 확인하세요 → {name}"
            )
        out.append({"name": name, "local": local})
    return out


def validate_meta(meta: dict, *, today: datetime.date) -> None:
    label = str(meta.get("effectiveLabel", ""))
    if not label:
        raise BuildError("meta: effectiveLabel이 비어 있습니다.")
    if "임시값" in label:
        raise BuildError(
            "meta: effectiveLabel에 '임시값'이 남아 있습니다. 공고값으로 교체한 뒤 "
            "이 문구를 지우세요."
        )

    updated = str(meta.get("updatedAt", "")).strip()
    if not (len(updated) == 7 and updated[4] == "." and updated[:4].isdigit()
            and updated[5:].isdigit()):
        raise BuildError(f"meta: updatedAt 형식은 YYYY.MM 이어야 합니다 → {updated!r}")
    year = int(updated[:4])
    if year != today.year:
        raise BuildError(
            f"meta: updatedAt이 올해({today.year})가 아닙니다 → {updated}. "
            "보조금 공고는 해마다 새로 나옵니다. 갱신을 잊지 않았는지 확인하세요."
        )

    tiers = meta.get("priceTiers")
    if not isinstance(tiers, list) or not tiers:
        raise BuildError("meta: priceTiers가 비어 있습니다.")
    if tiers[-1].get("limit") is not None:
        raise BuildError("meta: priceTiers의 마지막 칸은 limit이 null이어야 합니다.")
    prev_limit = 0.0
    prev_rate = None
    for t in tiers:
        rate = t.get("rate")
        if not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
            raise BuildError(f"meta: priceTiers rate는 0~1 이어야 합니다 → {rate!r}")
        if prev_rate is not None and rate > prev_rate:
            raise BuildError("meta: priceTiers의 rate는 내림차순이어야 합니다.")
        prev_rate = rate
        limit = t.get("limit")
        if limit is None:
            continue
        if limit <= prev_limit:
            raise BuildError("meta: priceTiers의 limit은 오름차순이어야 합니다.")
        prev_limit = limit


def check_drift(new: dict, previous: dict | None) -> list[str]:
    """이전 표와 비교해 급변한 항목을 경고 문자열로 돌려준다.

    열이 한 칸 밀리면 금액이 배 단위로 튀는데, 형식 검사만으로는 잡히지 않는다.
    """
    if not previous:
        return []
    warnings = []
    for key, field in (("models", "nationalBase"), ("regions", "local")):
        old = {e["name"]: e.get(field, 0) for e in previous.get(key, [])}
        for e in new.get(key, []):
            before = old.get(e["name"])
            if not before:
                continue
            after = e.get(field, 0)
            if abs(after - before) > before * DRIFT_RATIO:
                warnings.append(
                    f"{key}: {e['name']} {field} {before:,.0f} → {after:,.0f} "
                    f"({(after / before - 1) * 100:+.0f}%)"
                )
    return warnings


def build(*, today: datetime.date, allow_drift: bool) -> dict:
    if not os.path.exists(META):
        raise BuildError(f"입력 파일이 없습니다: {os.path.relpath(META, ROOT)}")
    with open(META, encoding="utf-8") as f:
        meta = json.load(f)
    validate_meta(meta, today=today)

    models = parse_models(_read_csv(MODELS, ["name", "nationalBase"]))
    regions = parse_regions(_read_csv(REGIONS, ["name", "local"]))

    table = dict(meta)
    table.pop("_note", None)
    table["models"] = [{"name": MANUAL, "nationalBase": 0}] + models
    table["regions"] = [{"name": MANUAL, "local": 0}] + regions

    previous = None
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                previous = json.load(f)
        except (OSError, json.JSONDecodeError):
            previous = None

    drift = check_drift(table, previous)
    if drift:
        joined = "\n  - ".join(drift)
        if allow_drift:
            print(f"[warn] 이전 표 대비 급변 항목(허용됨):\n  - {joined}")
        else:
            raise BuildError(
                "이전 표 대비 ±50%를 넘게 움직인 항목이 있습니다. 열이 밀리지 않았는지 "
                f"확인하고, 실제 변경이라면 --allow-drift로 다시 실행하세요:\n  - {joined}"
            )
    return table


def _force_utf8_output() -> None:
    """Windows 콘솔 기본 코드페이지(cp949)에서 한글·em dash 출력이 죽는 것을 막는다."""
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

    body = json.dumps(table, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        print(f"[ok] 검증 통과 — 차종 {len(table['models']) - 1}종 / "
              f"지역 {len(table['regions']) - 1}곳 ({table['updatedAt']} 기준)")
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
    print(f"[ok] {os.path.relpath(OUT, ROOT)} 갱신 — 차종 {len(table['models']) - 1}종 / "
          f"지역 {len(table['regions']) - 1}곳 ({table['updatedAt']} 기준)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
