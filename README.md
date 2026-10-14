# opendict-db

국립국어원 **우리말샘**(https://opendict.korean.go.kr) Open API로 만드는 개인용 로컬 사전 DB입니다.
[stdict-db](../stdict-db), [urimal-history-db](../urimal-history-db)와 같은 구조(원본 수집 → 조회용 DB 빌드 → 조회/MCP)를 따릅니다.

## 사전 규모 (2026년 9월 통계)

- 표제어 1,212,844개 (단어 783,461 · 구 429,383), 일상어 506,415 · 전문어 706,422
- target_code는 1부터 약 1,553,000까지, 그 안에서 존재하는 코드는 약 75% (나머지는 빈 코드)
- 같은 코드라도 `/api/view`가 HTTP 500을 내는 것이 있다(예: 13, 27, 132 …). 수집기는 `unavailable`로 기록하고 넘어간다.

## Open API

| 구분 | URL |
|---|---|
| 검색 | `https://opendict.korean.go.kr/api/search` (q, start 1~1000, num 10~100, advanced=y 에서 target·method·date_s/e(올린 날짜)·**update_s/e(고친 날짜)** 등) |
| 내용 | `https://opendict.korean.go.kr/api/view` (`method=word_info` 또는 `target_code`) |

- 키는 `.env`의 `OPENDICT_API_KEY`에만 두고 저장소에 올리지 않습니다. 하루 호출 한도는 명세에 없어서 다른 사전과 같은 50,000건으로 보고 45,000건에서 멈춥니다(확인 필요).
- **고친 날짜(`update_s`/`update_e`) 검색이 있어서** 정기 업데이트에서 변경을 날짜로 찾을 수 있습니다(우리말역사어휘사전에는 없음).
- 전체 사전 내려받기는 보이지 않습니다(통계 내려받기만 있음). 그래서 `target_code`를 1부터 훑어 `view`로 받습니다.

## 사용

```
python3 opendict_fetch.py fetch --range 1 1600000    # 하루 한도에서 멈추고, 다시 실행하면 이어서(전체 약 35일)
python3 opendict_fetch.py status
python3 opendict_fetch.py fetch --scheduled --limit 49000   # launchd(매일 09:15)가 실행: 잠금·시각 붙은 로그 data/fetch.log
python3 opendict_fetch.py fetch --range 1 1600000 --retry-unavailable   # HTTP 500 이던 코드 다시 시도
```

원본은 XML 그대로 `data/raw.sqlite`에 압축 저장합니다(`data/`는 저장소에 올리지 않음).

## 계획

1. (진행 중) 원본 수집
2. 스키마 설계(뜻풀이·용례·관련어·원어·전문 분야 등) → 빌드 → 조회 → MCP
3. 정기 업데이트(고친 날짜 검색 + 신규 확인)

## 데이터 출처

국립국어원 우리말샘(CC BY-SA 2.0 KR, 출전이 있는 용례는 원저작권자 권리). 이 저장소에는 코드만 올리고 사전 데이터는 포함하지 않습니다.
