# 응답 구조와 스키마 초안 (2026-11-16)

원본(`data/raw.sqlite`, 약 123만 건) 중 5분의 1(247,102건)을 전수 파싱해 집계했다. 파싱 실패 0건.

## 1. 응답 구조

- 한 코드 = 한 응답 = `item` 1개 = `senseInfo` 1개. 즉 **target_code 는 뜻풀이(sense) 단위**다(`sense_no` 와 같은 값 체계).
- `group_code`(약 115만 그룹)가 같은 표제어의 뜻풀이들을 묶고, `group_order` 가 그 안의 순서다. 그룹당 뜻풀이는 1개가 95%(1,095,497/1,153,550).
- `wordInfo` 는 코드마다 따로 들어 있고 같은 그룹 안에서도 다를 수 있다(표본의 약 10%: `방순-하다`/`방순하다` 같은 표기 변이, 뜻풀이별 발음). 그래서 표제어 정보를 그룹으로 합치지 않고 **코드 행마다 둔다**.
- 속성(attribute)은 없고 텍스트는 대부분 CDATA.

| 경로 | 설명 | 있는 비율(표본) / 반복 |
|---|---|---|
| target_code, group_code, group_order | 식별 | 100% |
| wordInfo/word, word_unit(어휘·구·속담·관용구), word_type(한자어·고유어·혼종어·외래어·빈값 3,219건) | 표제어 | 100% |
| wordInfo/original_language_info (original_language, language_type) | 원어 | 77%, 코드당 여러 개 |
| wordInfo/pronunciation_info/pronunciation | 발음 | 40%, 여러 개 |
| wordInfo/conju_info (conjugation_info{conjugation, pronunciation_info}, abbreviation_info{abbreviation, pronunciation_info}) | 활용·준말 | 12% |
| wordInfo/allomorph, origin | 이형태, 어원(약어 풀이 등) | 1%, 1.6% |
| senseInfo/sense_no, type(일반어·방언·북한어·옛말), pos(품사 22종, 63%만), definition, definition_original | 뜻풀이 | 100% |
| senseInfo/cat_info/cat | 전문 분야 | 58%, 여러 개 |
| senseInfo/example_info (example, source, origin, region, translation) | 용례 | 23%, 여러 개 |
| senseInfo/relation_info (word, type 11종, link_target_code, link) | 관련어 | 36%, 코드당 평균 약 2.7개 |
| senseInfo/translation_info (language_type, translation) | 번역어 | 19% (영어가 99%) |
| senseInfo/region_info/region | 방언 지역 | 9% |
| senseInfo/pattern_info/pattern, grammar_info/grammar | 문형, 문법 | 4%, 0.7% |
| senseInfo/proverb_info (word, type, definition, link_target_code) | 속담·관용구 연결 | 0.5% |
| senseInfo/history_info (word_form, allomorph, desc, remark, history_sense_info/history_century_info{century, mark, history_example_info{example, source}}) | 어휘 역사 | 0.9% |
| senseInfo/multimedia_info (type, label, link), norm_info (type: 순화·외래어 표기, role, desc), scientific_name, sl_info_link | 멀티미디어, 규범, 학명, 수어 | 1% 안팎 |

## 2. 텍스트 안의 표지

- 용례: 쓰인 말을 `{…}` 로 감쌈. 표본 1/50 중 22%.
- `definition` 안의 `<FL>…</FL>`: 외국어 표기 구간(1.9%). `definition_original`: 뜻풀이 속 낱말에 `<sense_no>코드</sense_no>` 를 박아 **다른 표제어를 가리키는 링크**(코드 번호)를 담는다.
- 옛한글 등은 사용자 정의 영역(PUA, U+E000~F8FF)의 글자로 온다(2.4%). 그대로 저장하고, 검색용 정규화는 따로 둔다.
- `<sup>`, `&lt;` 가 드물게 있음.

## 3. 스키마 초안 (stdict-db 와 같은 규칙, 이름 맞춤)

`stdict_build.py` 와 같은 틀(새 DB를 `.new` 로 만들어 검증 후 교체, 이전 판은 `.prev`)을 쓴다.

- `entries`(target_code PK, group_code, group_order, word, word_plain(하이픈·어깨번호 제거), word_unit, word_type, sense_type(일반어·방언·북한어·옛말), pos, definition(표지 제거본), definition_raw(원문), origin, allomorph, scientific_name, sl_link, grammar)
- `origins`(target_code, original_language, language_type) / `pronunciations`(target_code, pronunciation) / `conjugations`(…, 발음은 `conj_pron`) / `abbreviations`
- `sense_cats`(target_code, cat) / `sense_regions` / `sense_patterns`
- `examples`(target_code, example, source, origin, region, translation) / `translations`(target_code, language_type, translation)
- `relations`(target_code, type, word, link_code) — 링크 코드로 다른 `entries` 와 이어짐
- `proverbs`, `history`(century·용례 정리), `multimedia`, `norm_info`
- `fts_words`, `fts_defs` (trigram) — stdict 와 같은 방식
- `meta`(빌드 시각, 원본 건수, 통계 대조값)

## 4. 정해야 할 것

1. `definition_original` 의 코드 링크: 조회 결과에서 다른 표제어로 이어 주려면 별도 `definition_links` 표로 뽑는다(추천).
2. 방언·북한어·옛말(약 3.8만 건)은 `sense_type` 으로 구분해 같은 표에 둔다. 검색에서 기본으로 포함할지(추천: 포함하되 표시)는 MCP 단계에서 정한다.
3. 통계 대조: 사전통계_202609.xls 의 표제어 1,212,844 와 빌드 결과(`group_code` 수 약 115만, 코드 수 약 123만)가 같은 단위인지 먼저 확인한다.
4. 수집이 끝나기 전에 빌드를 시험하면 일부가 빠지므로, 최종 빌드는 확장분(1,600,001~1,870,000)까지 끝난 뒤에 한다.

## 5. 확정 사항 (2026-11-16 첫 빌드 반영)

- `definition_links` 로 `definition_original` 의 링크를 분리했다: kind='sense' 는 뜻풀이 코드(target_code), kind='word' 는 **group_code**(대상 일치 94.9%, 나머지는 미수집·삭제 표제어). anchor 는 링크 뒤에 붙은 글자(휴리스틱).
- 방언·북한어·옛말은 같은 `entries` 에 두고 `sense_type`(일반어·방언·북한어·옛말)으로 구분했다.
- `definition`/`example` 은 표지를 뗀 평문, 표지(<sub>, <FL>, <DR /> 등)가 있을 때만 `definition_html`/`example_html` 에 원문을 보관한다.
- 빌드는 알려진 태그만 허용한다(모르는 태그가 나오면 실패). 전체 파싱에서 표본에 없던 `history_example_info/origin` 이 나와 `history_examples.origin` 을 추가했다.
- 통계 대조(2026-11-16, 확장분 수집 전): 어휘 783,118 / 통계 단어 783,461(차 343), 구+속담+관용구 451,722 / 통계 구 429,383. 단위가 같은지(속담·관용구가 통계의 구에 들어가는지)는 수집이 끝난 뒤 다시 본다.
- 한계: trigram 이라 3글자 미만 검색은 word_plain/word_key/pron_plain/form 열을 직접 조회한다(원어 2글자 한자 포함).
