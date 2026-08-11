#!/usr/bin/env python3
"""무공해차 통합누리집 「지자체 모델별 보조금」 PDF → CSV.

ev.or.kr은 자동 수집을 막아 두었으므로(pnp4web) 사람이 브라우저에서 팝업을
`Ctrl+P`로 PDF 저장한 것을 입력으로 받는다. 사이트를 긁지 않는다.

두 가지 추출을 **결합**한다 — 어느 하나만으로는 부족하다:
  * 표 추출: 줄바꿈된 모델명("EV3 롱레인지 2WD\\n17인치")을 올바르게 합쳐 주지만
    마지막 열(전환지원금 지방비)을 놓친다.
  * 텍스트 추출: 5개 숫자를 모두 얻지만, 줄바꿈된 모델명이 조각나 앞뒤 줄로 흩어진다.
두 결과는 같은 순서·같은 개수로 나오므로 이름은 표에서, 숫자는 텍스트에서 가져온다.
개수가 어긋나면 조용히 밀린 데이터를 만드느니 그 파일을 건너뛴다.

사용:
  python tool/extract_ev_pdf.py <PDF폴더> [-o data/ev_subsidy_rows.csv]

파일명 규칙: `..._<지역>_승용.pdf` (승합·화물은 계산기가 승용 기준이라 제외).
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import sys

# 파일명의 축약 지역명 → 공식 명칭. 없으면 파일명을 그대로 쓴다.
REGION_FULL = {
    '서울': '서울특별시', '부산': '부산광역시', '대구': '대구광역시',
    '인천': '인천광역시', '광주': '광주광역시', '대전': '대전광역시',
    '울산': '울산광역시', '세종': '세종특별자치시', '경기': '경기도',
    '강원': '강원특별자치도', '충북': '충청북도', '충남': '충청남도',
    '전북': '전북특별자치도', '전남': '전라남도', '경북': '경상북도',
    '경남': '경상남도', '제주': '제주특별자치도',
}

# 국비·지방비·합계·전환지원금(국비)·전환지원금(지방비) 5개 숫자로 끝나는 줄.
ROW_RE = re.compile(r'^(?P<head>.+?)\s+(?P<nums>\d{1,5}(?:\s+\d{1,5}){4})\s*$')

MAN = 10000  # 표는 만원 단위


def _to_won(text: str) -> int:
    return int(text) * MAN


def parse_pdf(path: str) -> list[dict]:
    import pdfplumber

    names: list[tuple[str, str]] = []   # (제조사, 모델명) — 표에서
    numbers: list[list[int]] = []       # 5개 금액 — 텍스트에서

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row or (row[0] and '차종' in str(row[0])):
                        continue
                    cells = [(c or '').replace('\n', ' ').strip() for c in row]
                    if len(cells) < 6 or not cells[1] or not cells[2]:
                        continue
                    names.append((cells[1], re.sub(r'\s+', ' ', cells[2])))
            for line in (page.extract_text() or '').splitlines():
                m = ROW_RE.match(line.strip())
                if m:
                    numbers.append([int(x) for x in m.group('nums').split()])

    if len(names) != len(numbers):
        raise ValueError(
            f'모델명 {len(names)}건 / 금액 {len(numbers)}건 — 개수가 맞지 않아 '
            f'행을 짝지을 수 없습니다'
        )

    region = REGION_FULL.get(
        os.path.basename(path).split('_')[-2],
        os.path.basename(path).split('_')[-2],
    )
    out = []
    for (maker, model), (nat, loc, total, conv_nat, conv_loc) in zip(names, numbers):
        if nat + loc != total:
            raise ValueError(f'{model}: 국비+지방비({nat}+{loc})가 합계({total})와 다릅니다')
        out.append({
            'region': region,
            'maker': maker,
            'model': model,
            'national': _to_won(str(nat)),
            'local': _to_won(str(loc)),
            'convNational': _to_won(str(conv_nat)),
            'convLocal': _to_won(str(conv_loc)),
        })
    return out


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description='전기차 보조금 PDF → CSV')
    ap.add_argument('src', help='PDF가 있는 폴더')
    ap.add_argument('-o', '--out', default='data/ev_subsidy_rows.csv')
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(args.src, '*_승용.pdf')))
    if not files:
        print(f'[error] {args.src} 에 *_승용.pdf 가 없습니다.', file=sys.stderr)
        return 1

    rows: list[dict] = []
    for f in files:
        try:
            got = parse_pdf(f)
        except Exception as e:  # noqa: BLE001 — 한 파일 실패가 전체를 막지 않게
            print(f'[skip] {os.path.basename(f)}: {e}', file=sys.stderr)
            continue
        rows.extend(got)
        print(f'[ok] {os.path.basename(f)}: {len(got)}행')

    if not rows:
        print('[error] 추출된 행이 없습니다.', file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=[
            'region', 'maker', 'model', 'national', 'local',
            'convNational', 'convLocal'])
        w.writeheader()
        w.writerows(rows)

    regions = sorted({r['region'] for r in rows})
    print(f'[ok] {len(rows)}행 → {args.out} (지역 {len(regions)}: {", ".join(regions)})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
