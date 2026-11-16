#!/usr/bin/env python3
"""우리말샘 조회용 DB 빌드: 원본(data/raw.sqlite) -> 조회용(data/opendict.sqlite). 표준 라이브러리만 사용.

  python3 opendict_build.py                 # 전체 빌드 -> 검증 -> 통과하면 교체(이전 버전 1개 보관)
  python3 opendict_build.py --limit 3000    # 앞의 3,000건만 빌드해 파서를 시험(교체하지 않음)

흐름: 원본 XML 파싱 -> 임시 DB(~/.cache/opendict-db, rsync 되는 폴더 밖) -> 인덱스·FTS5 -> 검증 -> 원자적 교체.
검증을 하나라도 못 넘기면 조회 중인 DB(data/opendict.sqlite)는 건드리지 않는다.
원본은 읽기 전용으로만 연다. API 호출도 인증키도 쓰지 않는다. 수집이 진행 중이어도 돌릴 수 있다(그 시점까지 받은 것만 들어간다).

구조(docs/schema_draft.md): target_code 는 뜻풀이 단위이고 표제어 정보(wordInfo)도 코드마다 따로 저장한다.
group_code 로 같은 표제어의 뜻풀이를 묶는다. TREE 에 없는 태그가 나오면 빌드를 실패시킨다.
"""
import argparse
import html
import os
import re
import shutil
import sqlite3
import sys
import time
import zlib
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

SCHEMA_VERSION = "v1"
ROOT = Path(__file__).resolve().parent
RAW_PATH = Path(os.environ.get("OPENDICT_DB", ROOT / "data" / "raw.sqlite"))
OUT_PATH = ROOT / "data" / "opendict.sqlite"
PREV_PATH = ROOT / "data" / "opendict.prev.sqlite"
CACHE_DIR = Path.home() / ".cache" / "opendict-db"
LINK_BAD_ERROR_RATE = 0.05           # 관련어 링크가 이 비율을 넘게 없는 코드를 가리키면 실패, 0.5% 넘으면 경고
LINK_BAD_WARN_RATE = 0.005

SENSE_TYPES = {"일반어", "방언", "북한어", "옛말"}
WORD_UNITS = {"어휘", "구", "속담", "관용구"}
GOLDEN = [  # (코드, 표제어, 뜻풀이 종류)
    (450817, "고드름", "일반어"),
    (1775001, "나머지모임", "북한어"),
]

_PRON = {"pronunciation": None}
TREE = {
    "target_code": None, "group_code": None, "group_order": None,
    "wordInfo": {
        "word": None, "word_unit": None, "word_type": None, "allomorph": None, "origin": None,
        "original_language_info": {"original_language": None, "language_type": None},
        "pronunciation_info": _PRON,
        "conju_info": {
            "conjugation_info": {"conjugation": None, "pronunciation_info": _PRON},
            "abbreviation_info": {"abbreviation": None, "pronunciation_info": _PRON},
        },
    },
    "senseInfo": {
        "sense_no": None, "pos": None, "type": None, "definition": None, "definition_original": None,
        "scientific_name": None, "sl_info_link": None,
        "cat_info": {"cat": None}, "region_info": {"region": None},
        "pattern_info": {"pattern": None}, "grammar_info": {"grammar": None},
        "example_info": {"example": None, "source": None, "origin": None, "region": None, "translation": None},
        "relation_info": {"word": None, "type": None, "link_target_code": None, "link": None},
        "translation_info": {"language_type": None, "translation": None},
        "proverb_info": {"word": None, "type": None, "definition": None, "link": None, "link_target_code": None},
        "history_info": {
            "allomorph": None, "desc": None, "remark": None, "word_form": None,
            "history_sense_info": {"history_century_info": {
                "century": None, "mark": None,
                "history_example_info": {"example": None, "source": None, "origin": None}}},
        },
        "multimedia_info": {"label": None, "link": None, "type": None},
        "norm_info": {"desc": None, "role": None, "type": None},
    },
}

TABLES = {  # 표 이름 -> 열 정의(삽입 순서)
    "entries": "target_code INTEGER PRIMARY KEY, group_code INTEGER NOT NULL, group_order INTEGER, sense_no TEXT, "
               "word TEXT NOT NULL, word_plain TEXT NOT NULL, word_key TEXT NOT NULL, word_unit TEXT, word_type TEXT, "
               "sense_type TEXT, pos TEXT, definition TEXT, definition_html TEXT, definition_original TEXT, "
               "scientific_name TEXT, sl_link TEXT, origin TEXT, allomorph TEXT, original_text TEXT",
    "origins": "target_code INTEGER NOT NULL, seq INTEGER NOT NULL, original_language TEXT, language_type TEXT",
    "pronunciations": "target_code INTEGER NOT NULL, seq INTEGER NOT NULL, pronunciation TEXT, pron_plain TEXT",
    "conjugations": "id INTEGER PRIMARY KEY, target_code INTEGER NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, form TEXT",
    "conj_pron": "conjugation_id INTEGER NOT NULL, seq INTEGER NOT NULL, pronunciation TEXT",
    "sense_cats": "target_code INTEGER NOT NULL, cat TEXT",
    "sense_regions": "target_code INTEGER NOT NULL, region TEXT",
    "sense_patterns": "target_code INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT",
    "examples": "target_code INTEGER NOT NULL, seq INTEGER NOT NULL, example TEXT, example_html TEXT, source TEXT, "
                "origin TEXT, region TEXT, translation TEXT",
    "translations": "target_code INTEGER NOT NULL, seq INTEGER NOT NULL, language_type TEXT, translation TEXT",
    "relations": "target_code INTEGER NOT NULL, seq INTEGER NOT NULL, rel_type TEXT, word TEXT, link_code INTEGER",
    "proverbs": "target_code INTEGER NOT NULL, seq INTEGER NOT NULL, type TEXT, word TEXT, definition TEXT, link_code INTEGER",
    "definition_links": "target_code INTEGER NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, link_no INTEGER NOT NULL, anchor TEXT",
    "history": "target_code INTEGER PRIMARY KEY, word_form TEXT, allomorph TEXT, desc TEXT, remark TEXT",
    "history_centuries": "id INTEGER PRIMARY KEY, target_code INTEGER NOT NULL, seq INTEGER NOT NULL, century TEXT, mark TEXT",
    "history_examples": "century_id INTEGER NOT NULL, seq INTEGER NOT NULL, example TEXT, source TEXT, origin TEXT",
    "multimedia": "target_code INTEGER NOT NULL, seq INTEGER NOT NULL, type TEXT, label TEXT, link TEXT",
    "norm_info": "target_code INTEGER NOT NULL, seq INTEGER NOT NULL, type TEXT, role TEXT, desc TEXT",
    "meta": "key TEXT PRIMARY KEY, value TEXT",
}

INDEXES = """
CREATE INDEX entries_group ON entries(group_code, group_order);
CREATE INDEX entries_word_plain ON entries(word_plain);
CREATE INDEX entries_word_key ON entries(word_key);
CREATE INDEX entries_word ON entries(word);
CREATE INDEX entries_sense_type ON entries(sense_type);
CREATE INDEX origins_code ON origins(target_code);
CREATE INDEX origins_lang ON origins(original_language);
CREATE INDEX pron_code ON pronunciations(target_code);
CREATE INDEX pron_plain ON pronunciations(pron_plain);
CREATE INDEX conj_code ON conjugations(target_code);
CREATE INDEX conj_form ON conjugations(form);
CREATE INDEX conj_pron_id ON conj_pron(conjugation_id);
CREATE INDEX sense_cats_code ON sense_cats(target_code);
CREATE INDEX sense_cats_cat ON sense_cats(cat);
CREATE INDEX sense_regions_code ON sense_regions(target_code);
CREATE INDEX sense_regions_region ON sense_regions(region);
CREATE INDEX sense_patterns_code ON sense_patterns(target_code);
CREATE INDEX examples_code ON examples(target_code);
CREATE INDEX translations_code ON translations(target_code);
CREATE INDEX relations_code ON relations(target_code);
CREATE INDEX relations_link ON relations(link_code);
CREATE INDEX proverbs_code ON proverbs(target_code);
CREATE INDEX deflinks_code ON definition_links(target_code);
CREATE INDEX deflinks_link ON definition_links(kind, link_no);
CREATE INDEX hist_cent_code ON history_centuries(target_code);
CREATE INDEX hist_ex_cent ON history_examples(century_id);
CREATE INDEX multimedia_code ON multimedia(target_code);
CREATE INDEX norm_code ON norm_info(target_code);
"""


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- 텍스트 도구
def norm(s):
    if s is None:
        return None
    s = " ".join(s.split())
    return s or None


def tx(e, path):
    n = e.find(path)
    return norm(n.text) if n is not None else None


def to_int(s):
    s = (s or "").strip()
    return int(s) if s.isdigit() else None


def plain_key(word):
    """하이픈(-)과 ^ 를 뺀 표제어(word_plain). 띄어쓰기는 남긴다."""
    return re.sub(r"[-^]", "", word)


def word_key(word):
    """하이픈·^·띄어쓰기를 모두 뺀 키. '감정교육'/'감정 교육' 을 같게 본다."""
    return re.sub(r"[-^\s]", "", word)


def pron_plain(p):
    return re.sub(r"[ː\-^\s]", "", p) if p else p


_TAG = re.compile(r"</?[A-Za-z_]+[^>]*>")
_LINK = re.compile(r"<(sense_no|word_no)>(\d+)</\1>([^<’”」』)\s,.]*)")


def plain(s):
    """표지(<sub>, <FL>, <img> 등)를 떼고 글자 코드(&#x…;)를 풀어 한 줄로 만든다. 줄바꿈 <DR />는 공백으로."""
    if s is None:
        return None
    s = re.sub(r"<DR\s*/>", " ", s)
    s = _TAG.sub("", s)
    return norm(html.unescape(s))


def markup(s):
    """HTML 표지(<sub>, <FL>, <DR /> 등)가 있을 때만 원문을 따로 보관한다(없으면 NULL). 글자 그대로의 꺾쇠(<지대풍>)는 표지가 아니다."""
    return s.strip() if s and _TAG.search(s) else None


# ---------------------------------------------------------------- 파싱
def find_unknown(elem, tree, path, out):
    for child in elem:
        sub = tree.get(child.tag, False)
        p = f"{path}/{child.tag}"
        if sub is False:
            out[p] += 1
        elif sub is None:
            if len(child):
                out[p + "(안에 태그가 있음)"] += 1
        else:
            find_unknown(child, sub, p, out)


def parse_item(item, R, ids):
    code = to_int(item.findtext("target_code"))
    wi, si = item.find("wordInfo"), item.find("senseInfo")
    if code is None or wi is None or si is None:
        raise ValueError("target_code/wordInfo/senseInfo 가 없음")
    word = tx(wi, "word")
    if not word:
        raise ValueError("word 가 비어 있음")
    raw_def = (si.findtext("definition") or "")
    raw_orig = (si.findtext("definition_original") or "")
    origins = [(tx(o, "original_language"), tx(o, "language_type")) for o in wi.findall("original_language_info")]
    original_text = " ".join(o[0] for o in origins if o[0]) or None
    R["entries"].append((
        code, to_int(item.findtext("group_code")) or 0, to_int(item.findtext("group_order")), tx(si, "sense_no"),
        word, plain_key(word), word_key(word), tx(wi, "word_unit"), tx(wi, "word_type"),
        tx(si, "type"), tx(si, "pos"), plain(raw_def), markup(raw_def),
        raw_orig.strip() if _LINK.search(raw_orig) else None,
        tx(si, "scientific_name"), tx(si, "sl_info_link"), plain(wi.findtext("origin")), tx(wi, "allomorph"), original_text))
    for i, (lang, ltype) in enumerate(origins):
        R["origins"].append((code, i, lang, ltype))
    for i, p in enumerate(wi.findall("pronunciation_info/pronunciation")):
        t = norm(p.text)
        R["pronunciations"].append((code, i, t, pron_plain(t)))
    for kind, tag in (("conjugation", "conju_info/conjugation_info"), ("abbreviation", "conju_info/abbreviation_info")):
        for i, c in enumerate(wi.findall(tag)):
            ids["conj"] += 1
            R["conjugations"].append((ids["conj"], code, i, kind, tx(c, kind)))
            for j, p in enumerate(c.findall("pronunciation_info/pronunciation")):
                R["conj_pron"].append((ids["conj"], j, norm(p.text)))
    for i, m in enumerate(_LINK.finditer(raw_orig)):
        R["definition_links"].append((code, i, "sense" if m.group(1) == "sense_no" else "word",
                                      int(m.group(2)), m.group(3) or None))
    for e in si.findall("cat_info/cat"):
        R["sense_cats"].append((code, norm(e.text)))
    for e in si.findall("region_info/region"):
        R["sense_regions"].append((code, norm(e.text)))
    for kind, tag in (("pattern", "pattern_info/pattern"), ("grammar", "grammar_info/grammar")):
        for e in si.findall(tag):
            R["sense_patterns"].append((code, kind, norm(e.text)))
    for i, e in enumerate(si.findall("example_info")):
        ex = e.findtext("example") or ""
        R["examples"].append((code, i, plain(ex), markup(ex), tx(e, "source"), tx(e, "origin"), tx(e, "region"),
                              plain(e.findtext("translation"))))
    for i, e in enumerate(si.findall("translation_info")):
        R["translations"].append((code, i, tx(e, "language_type"), norm(e.findtext("translation"))))
    for i, e in enumerate(si.findall("relation_info")):
        R["relations"].append((code, i, tx(e, "type"), tx(e, "word"), to_int(e.findtext("link_target_code"))))
    for i, e in enumerate(si.findall("proverb_info")):
        R["proverbs"].append((code, i, tx(e, "type"), tx(e, "word"), plain(e.findtext("definition")),
                              to_int(e.findtext("link_target_code"))))
    h = si.find("history_info")
    if h is not None:
        R["history"].append((code, tx(h, "word_form"), tx(h, "allomorph"), norm(h.findtext("desc")), norm(h.findtext("remark"))))
        seq = 0
        for hs in h.findall("history_sense_info"):
            for c in hs.findall("history_century_info"):
                ids["cent"] += 1
                R["history_centuries"].append((ids["cent"], code, seq, tx(c, "century"), tx(c, "mark")))
                seq += 1
                for j, ex in enumerate(c.findall("history_example_info")):
                    R["history_examples"].append((ids["cent"], j, norm(ex.findtext("example")), tx(ex, "source"), tx(ex, "origin")))
    for i, e in enumerate(si.findall("multimedia_info")):
        R["multimedia"].append((code, i, tx(e, "type"), tx(e, "label"), tx(e, "link")))
    for i, e in enumerate(si.findall("norm_info")):
        R["norm_info"].append((code, i, tx(e, "type"), tx(e, "role"), norm(e.findtext("desc"))))
    return word


def flush(db, R):
    for t, rows in R.items():
        if rows:
            db.executemany(f"INSERT INTO {t} VALUES({','.join('?' * len(TABLES[t].split(',')))})", rows)
            rows.clear()


def load(raw, db, limit):
    sql = ("SELECT r.target_code, r.xml FROM raw_xml r JOIN fetch_state f ON f.target_code=r.target_code "
           "WHERE f.status='ok' ORDER BY r.target_code" + (f" LIMIT {int(limit)}" if limit else ""))
    R = {t: [] for t in TABLES if t not in ("meta",)}
    ids = {"conj": 0, "cent": 0}
    unknown, bad, n = Counter(), [], 0
    for code, blob in raw.execute(sql):
        try:
            root = ET.fromstring(zlib.decompress(blob).decode("utf-8"))
            item = root.find("item")
            if item is None:
                raise ValueError("item 이 없음")
            find_unknown(item, TREE, "item", unknown)
            parse_item(item, R, ids)
        except Exception as e:   # 한 건의 문제가 전체를 막지 않게 모아서 보고한다
            bad.append((code, f"{type(e).__name__}: {str(e)[:80]}"))
            continue
        n += 1
        if n % 100000 == 0:
            flush(db, R)
            print(f"  {n:,}건", flush=True)
    flush(db, R)
    db.commit()
    return n, unknown, bad


# ---------------------------------------------------------------- 후처리, 검증, 교체
def finish(db):
    print("인덱스 생성...")
    db.executescript(INDEXES)
    print("FTS5 색인 생성...")
    db.executescript("""
        CREATE VIRTUAL TABLE fts_words USING fts5(word_plain, original_text, pron, conj, tokenize='trigram');
        CREATE VIRTUAL TABLE fts_defs USING fts5(definition, tokenize='trigram');
    """)
    db.execute(
        "INSERT INTO fts_words(rowid, word_plain, original_text, pron, conj) "
        "SELECT e.target_code, e.word_plain, COALESCE(e.original_text,''), "
        " COALESCE((SELECT group_concat(p.pron_plain, ' ') FROM pronunciations p WHERE p.target_code=e.target_code),''), "
        " COALESCE((SELECT group_concat(c.form, ' ') FROM conjugations c WHERE c.target_code=e.target_code),'') "
        "FROM entries e")
    db.execute("INSERT INTO fts_defs(rowid, definition) SELECT target_code, definition FROM entries WHERE definition IS NOT NULL")
    db.commit()


def scalar(db, sql, *a):
    return db.execute(sql, a).fetchone()[0]


def validate(db, n_ok, n_expected, unknown, bad, partial=False):
    errors, warns = [], []
    if unknown:
        errors.append("TREE 에 없는 태그: " + ", ".join(f"{p}×{c}" for p, c in unknown.most_common(8)))
    if bad:
        errors.append(f"파싱 실패 {len(bad)}건: " + "; ".join(f"{c}:{m}" for c, m in bad[:5]))
    n_ent = scalar(db, "SELECT COUNT(*) FROM entries")
    if n_ent != n_expected or n_ent != n_ok:
        errors.append(f"entries {n_ent:,}건 != 원본 ok {n_expected:,}건 (파싱 성공 {n_ok:,})")
    n_fts = scalar(db, "SELECT COUNT(*) FROM fts_words")
    if n_fts != n_ent:
        errors.append(f"fts_words {n_fts:,} != entries {n_ent:,}")
    bad_vals = db.execute("SELECT sense_type, COUNT(*) FROM entries WHERE sense_type IS NULL OR sense_type NOT IN "
                          f"({','.join('?' * len(SENSE_TYPES))}) GROUP BY 1", tuple(SENSE_TYPES)).fetchall()
    if bad_vals:
        errors.append(f"모르는 뜻풀이 종류(type): {bad_vals}")
    bad_vals = db.execute("SELECT word_unit, COUNT(*) FROM entries WHERE word_unit IS NULL OR word_unit NOT IN "
                          f"({','.join('?' * len(WORD_UNITS))}) GROUP BY 1", tuple(WORD_UNITS)).fetchall()
    if bad_vals:
        errors.append(f"모르는 표제어 단위(word_unit): {bad_vals}")
    if scalar(db, "SELECT COUNT(*) FROM entries WHERE group_code=0"):
        errors.append("group_code 가 없는 행이 있음")
    for code, word, stype in GOLDEN:
        row = db.execute("SELECT word, sense_type FROM entries WHERE target_code=?", (code,)).fetchone()
        if row is None:
            if n_expected == scalar(db, "SELECT COUNT(*) FROM entries") and not db.execute(
                    "SELECT 1 FROM entries WHERE target_code>=? LIMIT 1", (code,)).fetchone():
                continue                       # --limit 시험 빌드에는 뒤쪽 코드가 없다
            warns.append(f"기준 표제어 {code}({word}) 가 없음")
        elif (row[0], row[1]) != (word, stype):
            errors.append(f"기준 표제어 {code}: {row} != {(word, stype)}")
    rel = scalar(db, "SELECT COUNT(*) FROM relations WHERE link_code IS NOT NULL")
    if rel:
        miss = scalar(db, "SELECT COUNT(*) FROM relations r WHERE link_code IS NOT NULL AND "
                          "NOT EXISTS (SELECT 1 FROM entries e WHERE e.target_code=r.link_code)")
        rate = miss / rel
        msg = f"관련어 링크 중 대상 코드가 없는 것 {miss:,}/{rel:,} ({rate:.2%})"
        if rate > LINK_BAD_ERROR_RATE and not partial:    # 시험 빌드(--limit)는 대상 코드가 범위 밖이라 당연히 많다
            errors.append(msg)
        elif rate > LINK_BAD_WARN_RATE:
            warns.append(msg + (" (시험 빌드라 예상됨)" if partial else ""))
    dl = scalar(db, "SELECT COUNT(*) FROM definition_links WHERE kind='sense'")
    if dl:
        miss = scalar(db, "SELECT COUNT(*) FROM definition_links d WHERE kind='sense' AND "
                          "NOT EXISTS (SELECT 1 FROM entries e WHERE e.target_code=d.link_no)")
        if miss / dl > LINK_BAD_WARN_RATE:
            warns.append(f"뜻풀이 속 sense 링크 중 대상 코드가 없는 것 {miss:,}/{dl:,} ({miss / dl:.2%})")
    wl = scalar(db, "SELECT COUNT(*) FROM definition_links WHERE kind='word'")
    if wl:
        hit = scalar(db, "SELECT COUNT(*) FROM definition_links d WHERE kind='word' AND "
                         "EXISTS (SELECT 1 FROM entries e WHERE e.group_code=d.link_no)")
        warns.append(f"word_no 링크 {wl:,}개 중 group_code 와 일치하는 것 {hit:,}개 ({hit / wl:.1%}) — 일치 여부로 word_no 의미를 확인할 것")
    return errors, warns


def swap(new, out, prev):
    out, prev, new = Path(out), Path(prev), Path(new)
    if os.stat(new).st_dev != os.stat(out.parent).st_dev:         # 다른 볼륨이면 같은 폴더로 복사해서 이름 바꾸기
        staged = out.with_name(out.name + ".new")
        shutil.copyfile(new, staged)
        new.unlink()
        new = staged
    had = out.exists()
    if had:
        os.replace(out, prev)
    try:
        os.replace(new, out)
    except Exception:
        if had and not out.exists():
            os.replace(prev, out)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(OUT_PATH), help="완성본 위치(기본 data/opendict.sqlite)")
    ap.add_argument("--prev", default=str(PREV_PATH), help="교체 전 버전 보관 위치(기본 data/opendict.prev.sqlite, 1개만 보관)")
    ap.add_argument("--tmp-dir", default=str(CACHE_DIR), help="임시 빌드 폴더(rsync 되는 폴더 밖, 기본 ~/.cache/opendict-db)")
    ap.add_argument("--limit", type=int, help="앞의 N건만 빌드(시험용, 교체하지 않음)")
    ap.add_argument("--keep-failed", action="store_true", help="검증에 실패해도 임시 DB를 남김(원인 조사용)")
    args = ap.parse_args()

    if not RAW_PATH.exists():
        sys.exit("원본 DB(data/raw.sqlite)가 없습니다.")
    try:
        sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
    except sqlite3.Error as e:
        sys.exit(f"이 파이썬의 SQLite 가 FTS5 trigram 을 지원하지 않습니다: {e}")
    tmp_dir = Path(args.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / ("opendict.test.sqlite" if args.limit else "opendict.building.sqlite")
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(tmp) + suffix).unlink(missing_ok=True)

    raw = sqlite3.connect(f"file:{RAW_PATH}?mode=ro", uri=True)
    n_total = raw.execute("SELECT COUNT(*) FROM raw_xml r JOIN fetch_state f ON f.target_code=r.target_code "
                          "WHERE f.status='ok'").fetchone()[0]
    n_expected = min(args.limit, n_total) if args.limit else n_total
    print(f"원본 ok {n_total:,}건" + (f" 중 앞의 {args.limit:,}건만 빌드(시험)" if args.limit else "") + f" → {tmp}")
    db = sqlite3.connect(tmp)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("PRAGMA temp_store=MEMORY")
    db.execute("PRAGMA cache_size=-400000")
    for t, cols in TABLES.items():
        db.execute(f"CREATE TABLE {t}({cols})")
    t0 = time.time()
    warns = []
    try:
        print("원본 파싱...")
        n_ok, unknown, bad = load(raw, db, args.limit)
        finish(db)
        n_groups = scalar(db, "SELECT COUNT(DISTINCT group_code) FROM entries")
        meta = {"schema_version": SCHEMA_VERSION, "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "source_ok_count": str(n_total), "entries": str(n_ok), "groups": str(n_groups),
                "fts_tokenizer": "trigram (3글자 미만 검색은 인덱스 컬럼 word_plain/word_key/pron_plain/form 으로)"}
        db.executemany("INSERT INTO meta VALUES(?,?)", list(meta.items()))
        db.commit()
        print("검증...")
        errors, warns = validate(db, n_ok, n_expected, unknown, bad, partial=bool(args.limit))
        if errors:
            raise BuildError("\n".join(errors))
        db.execute("ANALYZE")
        db.execute("PRAGMA journal_mode=DELETE")
        db.commit()
        ic = db.execute("PRAGMA integrity_check").fetchone()[0]
        if ic != "ok":
            raise BuildError(f"integrity_check: {ic}")
    except BuildError as e:
        db.close()
        print(f"\n빌드 실패 (조회 중인 DB는 그대로입니다):\n{e}")
        if args.keep_failed:
            print(f"임시 DB를 남겼습니다: {tmp}")
        else:
            for suffix in ("", "-journal"):
                Path(str(tmp) + suffix).unlink(missing_ok=True)
        sys.exit(1)
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES if t != "meta"}
    db.close()
    print(f"\n빌드 완료 ({time.time() - t0:.0f}초, {os.path.getsize(tmp) / 1e6:,.0f}MB)")
    print("  " + ", ".join(f"{t} {c:,}" for t, c in counts.items()))
    for w in warns:
        print(f"  ⚠ {w}")
    if args.limit:
        print(f"시험 빌드라 교체하지 않았습니다: {tmp}")
        return
    had_prev = Path(args.out).exists()
    swap(tmp, args.out, args.prev)
    print(f"교체 완료: {args.out}" + (f" (이전 버전: {args.prev})" if had_prev else " (첫 빌드라 이전 버전 없음)"))


if __name__ == "__main__":
    main()
