#!/usr/bin/env python3
"""우리말샘 조회 코어 (data/opendict.sqlite 를 읽기 전용으로만 연다). 표준 라이브러리만 사용, Python 3.9 이상.

MCP/REST 어댑터는 이 모듈의 Dictionary 와 format_* 함수만 호출한다(어댑터에 조회 로직을 두지 않는다).

  python3 opendict_lookup.py 고드름                      # 표제어로 찾기(같은 표제어의 뜻풀이를 그룹별로)
  python3 opendict_lookup.py 고드름 --examples --json    # 용례 포함, JSON 출력
  python3 opendict_lookup.py 고드름 --type 방언          # 뜻풀이 종류로 거르기(일반어·방언·북한어·옛말, 쉼표로 여러 개)
  python3 opendict_lookup.py --code 450817               # 코드(target_code)로 읽기
  python3 opendict_lookup.py 얼어붙은 --by definition    # 뜻풀이에서 찾기
  python3 opendict_lookup.py 氷柱 --by hanja             # 원어(한자 등)로 찾기
  python3 opendict_lookup.py 고드름 --by pronunciation   # 발음으로 찾기
  python3 opendict_lookup.py 먹었다 --by form            # 활용형·준말로 표제어 찾기
  python3 opendict_lookup.py 의학 --by cat               # 전문 분야로 찾기(--q 로 표제어 일부를 더 좁힘)

우리말샘은 일반어 외에 방언·북한어·옛말을 같은 사전에 싣는다. 결과마다 뜻풀이 종류를 표시한다.
DB 가 빌드로 교체(os.replace)되면 다음 요청에서 자동으로 다시 연결한다. 질의에는 시간 제한이 있다.
출전이 있는 용례도 그대로 보여 준다(출전은 ≪…≫ 로 덧붙인다).
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.environ.get("OPENDICT_LOOKUP_DB") or HERE / "data" / "opendict.sqlite")
DEFAULT_TIMEOUT = 3.0                  # 한 질의가 이 시간(초)을 넘으면 중단
ATTRIBUTION = "출처: 국립국어원 우리말샘 (CC BY-SA 2.0 KR, 출전이 있는 용례는 원저작권자 권리)"
LONG_NOTE = "발음의 ː 는 장음(길게 소리 냄)을 뜻함"
SENSE_TYPES = ("일반어", "방언", "북한어", "옛말")
SEARCH_KINDS = ("definition", "hanja", "pronunciation", "form", "cat", "region")
UNITS = ("어휘", "구", "속담", "관용구")
MAX_GROUPS = 20                        # 한 번에 보여 줄 표제어 그룹 수
MAX_SENSES_SHOWN = 12                  # 그룹마다 보여 줄 뜻풀이 수
MAX_REL_PER_TYPE = 8                   # 관련어 종류마다 보여 줄 개수(방언 관련어는 수십 개일 수 있다)

_SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


class OpendictError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 오류 문장을 담는다."""


class DictUnavailable(OpendictError):
    pass


class QueryTimeout(OpendictError):
    pass


# ---------------------------------------------------------------- 입력 정규화
def normalize(text):
    """macOS 의 자모 분리(NFD) 입력을 NFC 로 맞추고, 표제어의 하이픈(-)·^ 와 겹공백을 없앤다."""
    text = unicodedata.normalize("NFC", text or "")
    return " ".join(re.sub(r"[-^]", "", text).split())


def word_key(text):
    """표제어 조회 키: 띄어쓰기까지 뺀다."""
    return normalize(text).replace(" ", "")


def pron_key(text):
    """발음 검색용: 장음 ː 와 띄어쓰기·하이픈을 뺀다(장단 구별 없이 찾는다)."""
    return re.sub(r"[ː:\-^\s]", "", unicodedata.normalize("NFC", text or ""))


def sup(n):
    return str(n).translate(_SUP) if n else ""


def _like(text):
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _fts_phrase(text):
    return '"' + text.replace('"', '""') + '"'


def _cut(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _split_sup(word):
    """관련어 표제어 끝의 어깨번호('빙주002')를 (표제어, 번호)로 나눈다. 번호가 없으면 (표제어, None)."""
    m = re.match(r"^(.*?)(\d{3})$", word or "")
    if m and m.group(1):
        return m.group(1), int(m.group(2))
    return word or "", None


def _types(types):
    """--type 값(쉼표로 구분 문자열 또는 목록)을 검증해 목록으로."""
    if not types:
        return []
    items = [t.strip() for t in (types.split(",") if isinstance(types, str) else types) if t and t.strip()]
    bad = [t for t in items if t not in SENSE_TYPES]
    if bad:
        raise OpendictError(f"뜻풀이 종류는 {', '.join(SENSE_TYPES)} 중에서 고르세요: {', '.join(bad)}")
    return items


# ---------------------------------------------------------------- 사전
class Dictionary:
    def __init__(self, path=None, timeout=DEFAULT_TIMEOUT):
        self.path = Path(path) if path else DEFAULT_DB
        self.timeout = timeout
        self._local = threading.local()
        self._all, self._lock = [], threading.Lock()      # 모든 스레드의 연결(close 용)

    def close(self):
        with self._lock:
            conns, self._all = self._all, []
        for c in conns:
            try:
                c.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    def _signature(self):
        try:
            st = os.stat(self.path)
        except OSError:
            raise DictUnavailable("사전 DB를 열 수 없습니다. 서버 관리자에게 알려 주세요.")
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    def conn(self):
        sig = self._signature()
        c = getattr(self._local, "conn", None)
        if c is None or self._local.sig != sig:
            if c is not None:
                with self._lock:
                    if c in self._all:
                        self._all.remove(c)
                c.close()
            uri = "file:" + urllib.parse.quote(str(self.path)) + "?mode=ro"
            c = None
            try:
                c = sqlite3.connect(uri, uri=True, check_same_thread=False)
                c.row_factory = sqlite3.Row
                c.execute("PRAGMA query_only=ON")
                c.execute("SELECT 1 FROM meta LIMIT 1")
            except sqlite3.Error:
                if c is not None:
                    c.close()
                raise DictUnavailable("사전 DB를 열 수 없습니다. 서버 관리자에게 알려 주세요.")
            self._local.conn, self._local.sig = c, sig
            with self._lock:
                self._all.append(c)
        return c

    def q(self, sql, params=(), timeout=None):
        c = self.conn()
        deadline = time.monotonic() + (timeout or self.timeout)
        c.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
        try:
            return c.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                raise QueryTimeout("검색이 너무 오래 걸려 중단했습니다. 검색어를 더 구체적으로 줄여 주세요.")
            raise
        finally:
            c.set_progress_handler(None, 0)

    def meta(self):
        return {r["key"]: r["value"] for r in self.q("SELECT key, value FROM meta")}

    # -- 표제어 조회
    def lookup(self, word, types=None, unit=None, include_examples=False, max_groups=MAX_GROUPS, full=False):
        """표제어(하이픈·^·띄어쓰기 무시)가 같은 뜻풀이를 찾아 group_code 별로 묶어 돌려준다.
        types 는 뜻풀이 종류(일반어·방언·북한어·옛말) 필터. full=True 는 개수 제한이 없다."""
        types = _types(types)
        norm = normalize(word)
        if not norm:
            raise OpendictError("조회할 단어를 입력해 주세요.")
        if unit and unit not in UNITS:
            raise OpendictError(f"단위는 {', '.join(UNITS)} 중에서 고르세요.")
        key = word_key(norm)
        sql, args = "SELECT group_code FROM entries WHERE word_key=?", [key]
        if types:
            sql += f" AND sense_type IN ({','.join('?' * len(types))})"
            args += types
        if unit:
            sql += " AND word_unit=?"
            args.append(unit)
        # 일반어가 있는 그룹을 먼저, 그다음 그룹 번호 순
        sql += " GROUP BY group_code ORDER BY MIN(sense_type<>'일반어'), group_code"
        groups = [r[0] for r in self.q(sql, args)]
        res = {"query": word, "normalized": norm, "types": types, "total_groups": len(groups), "shown": 0,
               "groups": [], "base_forms": [], "suggestions": []}
        limit = 10 ** 6 if full else max(1, min(int(max_groups), 50))
        for g in groups[:limit]:
            res["groups"].append(self._group(g, types, include_examples, full))
        res["shown"] = len(res["groups"])
        if not groups and types:     # 필터 때문에 없는 것이면 다른 종류로 있는지 알려 준다
            res["other_types"] = {r[0]: r[1] for r in self.q(
                "SELECT sense_type, COUNT(*) FROM entries WHERE word_key=? GROUP BY sense_type", (key,))}
        if not groups:
            res["base_forms"] = self._by_form(norm, 8)
            res["suggestions"] = self.suggest(norm)
            res["stem_guess"] = self.stem_guess(norm)
        return res

    def entry(self, code, include_examples=True):
        """코드(target_code) 하나의 뜻풀이를 그룹 전체 문맥과 함께 읽는다."""
        r = self.q("SELECT group_code FROM entries WHERE target_code=?", (code,))
        if not r:
            raise OpendictError(f"코드 {code} 에 해당하는 표제어가 없습니다.")
        g = self._group(r[0][0], [], include_examples, True)
        g["focus"] = code
        return {"query": str(code), "normalized": str(code), "types": [], "total_groups": 1, "shown": 1,
                "groups": [g], "base_forms": [], "suggestions": []}

    def _group(self, group_code, types, include_examples, full):
        rows = self.q("SELECT * FROM entries WHERE group_code=? ORDER BY group_order, target_code", (group_code,))
        if types:   # 필터가 있으면 그 종류의 뜻풀이만(그룹 구성은 그대로 알려 준다)
            shown_rows = [r for r in rows if r["sense_type"] in types]
        else:
            shown_rows = rows
        first = rows[0]
        out = {"group_code": group_code, "word": first["word_plain"], "headword": first["word"],
               "sup_no": self._sup_no([r["target_code"] for r in rows]), "unit": first["word_unit"], "type": first["word_type"],
               "total_senses": len(rows), "senses": []}
        # 표제어 정보(원어·발음·활용)는 코드마다 있으나 그룹에서 중복을 걷어 한 번만 보여 준다
        for field, sql in (
                ("origins", "SELECT DISTINCT original_language AS text, language_type AS language FROM origins "
                            "WHERE target_code IN ({m}) ORDER BY target_code, seq"),
                ("pronunciations", "SELECT DISTINCT pronunciation AS text FROM pronunciations WHERE target_code IN ({m}) "
                                   "ORDER BY target_code, seq")):
            m = ",".join("?" * len(rows))
            out[field] = [dict(r) for r in self.q(sql.format(m=m), [r["target_code"] for r in rows])]
        out["spellings"] = sorted({r["word"].replace("^", " ") for r in rows})
        out["conjugations"] = self._conjugations([r["target_code"] for r in rows])
        cap = 10 ** 6 if full else MAX_SENSES_SHOWN
        for r in shown_rows[:cap]:
            out["senses"].append(self._sense(r, include_examples, full))
        out["senses_omitted"] = max(0, len(shown_rows) - cap)
        out["filtered_out"] = len(rows) - len(shown_rows)
        return out

    def _sup_no(self, codes):
        """다른 표제어의 관련어에 '빙주002' 처럼 실린 어깨번호(동음이의어 번호)를 찾는다(그룹의 모든 코드를 본다).
        어디에서도 가리키지 않는 그룹은 번호를 알 수 없어 None 이다."""
        nums = {}
        for r in self.q(f"SELECT word FROM relations WHERE link_code IN ({','.join('?' * len(codes))}) LIMIT 20", codes):
            _, n = _split_sup(r["word"])
            if n:
                nums[n] = nums.get(n, 0) + 1
        return max(nums, key=nums.get) if nums else None

    def _conjugations(self, codes):
        rows = self.q(f"SELECT DISTINCT c.kind, c.form FROM conjugations c WHERE c.target_code IN ({','.join('?' * len(codes))}) "
                      "AND c.form IS NOT NULL ORDER BY c.target_code, c.seq", codes)
        return [{"kind": r["kind"], "form": r["form"]} for r in rows]

    def _sense(self, e, include_examples, full):
        code = e["target_code"]
        s = {"target_code": code, "group_order": e["group_order"], "word": e["word"], "sense_type": e["sense_type"],
             "pos": e["pos"], "definition": e["definition"], "definition_html": e["definition_html"], "scientific_name": e["scientific_name"],
             "origin": e["origin"], "allomorph": e["allomorph"],
             "cats": [r[0] for r in self.q("SELECT cat FROM sense_cats WHERE target_code=?", (code,))],
             "regions": [r[0] for r in self.q("SELECT region FROM sense_regions WHERE target_code=?", (code,))],
             "patterns": [{"kind": r["kind"], "text": r["text"]} for r in self.q(
                 "SELECT kind, text FROM sense_patterns WHERE target_code=?", (code,))],
             "translations": [{"language": r["language_type"], "text": r["translation"]} for r in self.q(
                 "SELECT language_type, translation FROM translations WHERE target_code=? ORDER BY seq", (code,))],
             "norm_info": [{"type": r["type"], "role": r["role"], "desc": r["desc"]} for r in self.q(
                 "SELECT type, role, desc FROM norm_info WHERE target_code=? ORDER BY seq", (code,))],
             "multimedia": [{"type": r["type"], "label": r["label"]} for r in self.q(
                 "SELECT type, label FROM multimedia WHERE target_code=? ORDER BY seq", (code,))],
             "proverbs": [{"type": r["type"], "word": r["word"], "definition": r["definition"], "link_code": r["link_code"]}
                          for r in self.q("SELECT type, word, definition, link_code FROM proverbs WHERE target_code=? ORDER BY seq",
                                          (code,))],
             "relations": self._relations(code, full)}
        if include_examples:
            lim = "" if full else " LIMIT 8"
            s["examples"] = [{"text": r["example"], "html": r["example_html"], "source": r["source"], "origin": r["origin"],
                              "region": r["region"], "translation": r["translation"]} for r in self.q(
                "SELECT example, example_html, source, origin, region, translation FROM examples WHERE target_code=? "
                "ORDER BY seq" + lim, (code,))]
        return s

    def _relations(self, code, full):
        by = {}
        for r in self.q("SELECT rel_type, word, link_code FROM relations WHERE target_code=? ORDER BY seq", (code,)):
            w, n = _split_sup(r["word"])
            by.setdefault(r["rel_type"] or "관련어", []).append({"word": w, "sup_no": n, "link_code": r["link_code"]})
        cap = 10 ** 6 if full else MAX_REL_PER_TYPE
        return [{"type": t, "words": v[:cap], "omitted": max(0, len(v) - cap)} for t, v in by.items()]

    def history(self, code):
        """어휘 역사(옛 표기·세기별 용례)가 있는 뜻풀이의 내용을 읽는다."""
        h = self.q("SELECT * FROM history WHERE target_code=?", (code,))
        if not h:
            return None
        cents = []
        for c in self.q("SELECT id, century, mark FROM history_centuries WHERE target_code=? ORDER BY seq", (code,)):
            ex = [{"text": r["example"], "source": r["source"], "origin": r["origin"]} for r in self.q(
                "SELECT example, source, origin FROM history_examples WHERE century_id=? ORDER BY seq", (c["id"],))]
            cents.append({"century": c["century"], "mark": c["mark"], "examples": ex})
        return {"target_code": code, "word_form": h[0]["word_form"], "allomorph": h[0]["allomorph"], "desc": h[0]["desc"],
                "remark": h[0]["remark"], "centuries": cents}

    # -- 찾기
    def _by_form(self, norm, limit):
        """활용형·준말('먹었다' 의 기본형이 아니라 사전에 활용형으로 실린 형태)로 표제어를 찾는다."""
        return [{"target_code": r["target_code"], "word": r["word"], "form": r["form"], "kind": r["kind"],
                 "sense_type": r["sense_type"]} for r in self.q(
            "SELECT e.target_code, e.word, e.sense_type, c.form, c.kind FROM conjugations c "
            "JOIN entries e ON e.target_code=c.target_code WHERE c.form=? ORDER BY e.sense_type<>'일반어', e.target_code LIMIT ?",
            (norm, limit))]

    def stem_guess(self, norm):
        """'먹었다' 처럼 활용한 말을 입력했을 때 앞 글자를 줄여 '먹다' 같은 기본형이 있는지 본다(추정이라고 표시해 보여 준다)."""
        key = word_key(norm)
        if len(key) < 3 or not key.endswith("다"):
            return []
        for i in range(len(key) - 1, 0, -1):
            cand = key[:i] + "다"
            if cand == key:
                continue
            rows = self.q("SELECT DISTINCT word FROM entries WHERE word_key=? AND sense_type='일반어' AND word_unit='어휘' LIMIT 3", (cand,))
            if rows:
                return [r["word"] for r in rows]
        return []

    def suggest(self, norm, limit=10):
        """없는 표제어에 대해 '이 글자로 시작하는' 표제어를 제안한다(없으면 한 글자 줄여 다시)."""
        key = word_key(norm)
        out, seen = [], set()
        for k in (key, key[:-1] if len(key) >= 3 else None):
            if not k:
                continue
            for r in self.q("SELECT word, sense_type FROM entries WHERE word_key>=? AND word_key<? "
                            "ORDER BY sense_type<>'일반어', length(word_key), word_key LIMIT ?", (k, k + "\uffff", limit * 3)):
                if r["word"] not in seen:
                    seen.add(r["word"])
                    out.append({"word": r["word"], "sense_type": r["sense_type"]})
                if len(out) >= limit:
                    break
            if out:
                break
        return out

    def search(self, query, by="definition", types=None, unit=None, q_word=None, limit=20):
        """표제어 말고 다른 열로 찾는다. by: definition(뜻풀이) | hanja(원어) | pronunciation(발음) | form(활용형) |
        cat(전문 분야) | region(방언 지역). 결과 한 줄은 뜻풀이(코드) 하나이다."""
        types = _types(types)
        if by not in SEARCH_KINDS:
            raise OpendictError(f"찾는 방식은 {', '.join(SEARCH_KINDS)} 중에서 고르세요.")
        qn = normalize(query)
        if not qn:
            raise OpendictError("검색어를 입력해 주세요.")
        limit = max(1, min(int(limit), 100))
        cond, cargs = "", []
        if types:
            cond += f" AND e.sense_type IN ({','.join('?' * len(types))})"
            cargs += types
        if unit:
            if unit not in UNITS:
                raise OpendictError(f"단위는 {', '.join(UNITS)} 중에서 고르세요.")
            cond += " AND e.word_unit=?"
            cargs.append(unit)
        if q_word:
            cond += " AND e.word_plain LIKE ? ESCAPE '\\'"
            cargs.append(_like(normalize(q_word)))
        base = ("SELECT DISTINCT e.target_code, e.word, e.sense_type, e.pos, e.definition FROM entries e ")
        if by == "definition":
            if len(qn) >= 3:
                rows = self.q(base + "JOIN fts_defs f ON f.rowid=e.target_code WHERE fts_defs MATCH ?" + cond +
                              " ORDER BY f.rank LIMIT ?", [_fts_phrase(qn)] + cargs + [limit])
            else:
                rows = self.q(base + "WHERE e.definition LIKE ? ESCAPE '\\'" + cond + " LIMIT ?", [_like(qn)] + cargs + [limit])
        elif by == "hanja":
            if len(qn) >= 3:
                rows = self.q(base + "JOIN fts_words f ON f.rowid=e.target_code WHERE fts_words MATCH ?" + cond + " LIMIT ?",
                              ["original_text : " + _fts_phrase(qn)] + cargs + [limit])
            else:   # 글자 수가 적으면 trigram 이 못 찾으므로 원어 열로 직접(앞부분 일치)
                rows = self.q(base + "JOIN origins o ON o.target_code=e.target_code WHERE o.original_language>=? AND "
                              "o.original_language<?" + cond + " LIMIT ?", [qn, qn + "￿"] + cargs + [limit])
        elif by == "pronunciation":
            pk = pron_key(qn)
            rows = self.q(base + "JOIN pronunciations p ON p.target_code=e.target_code WHERE p.pron_plain=?" + cond +
                          " ORDER BY e.sense_type<>'일반어' LIMIT ?", [pk] + cargs + [limit])
        elif by == "form":
            rows = self.q(base + "JOIN conjugations c ON c.target_code=e.target_code WHERE c.form=?" + cond +
                          " ORDER BY e.sense_type<>'일반어' LIMIT ?", [qn] + cargs + [limit])
        elif by == "cat":
            rows = self.q(base + "JOIN sense_cats c ON c.target_code=e.target_code WHERE c.cat=?" + cond +
                          " ORDER BY e.word_key LIMIT ?", [qn] + cargs + [limit])
        else:  # region
            rows = self.q(base + "JOIN sense_regions c ON c.target_code=e.target_code WHERE c.region=?" + cond +
                          " ORDER BY e.word_key LIMIT ?", [qn] + cargs + [limit])
        hits = [{"target_code": r["target_code"], "word": r["word"].replace("^", " "), "sense_type": r["sense_type"], "pos": r["pos"],
                 "definition": _cut(r["definition"], 90)} for r in rows]
        return {"query": query, "by": by, "types": types, "hits": hits, "limit": limit, "capped": len(hits) >= limit}

    def categories(self, limit=60):
        """전문 분야(cat) 목록과 뜻풀이 수."""
        return [{"cat": r[0], "count": r[1]} for r in self.q(
            "SELECT cat, COUNT(*) FROM sense_cats GROUP BY cat ORDER BY COUNT(*) DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------- 보기 좋게 출력
def _head(g):
    head = g["word"] + sup(g["sup_no"])
    bits = [b for b in (g["unit"] if g["unit"] != "어휘" else None, g["type"]) if b]
    return head + (f" ({', '.join(bits)})" if bits else "")


def _rel_line(r):
    words = "·".join(w["word"] + sup(w["sup_no"]) for w in r["words"])
    return f"{r['type']}: {words}" + (f" 외 {r['omitted']}개" if r["omitted"] else "")


def format_lookup(res):
    lines = []
    if not res["groups"]:
        if res.get("other_types"):
            lines.append(f"‘{res['normalized']}’ 은(는) 뜻풀이 종류 {', '.join(res['types'])} 에는 없고, 다른 종류로 실려 있습니다: "
                         + ", ".join(f"{t} {n}개" for t, n in res["other_types"].items()))
            return "\n".join(lines)
        lines.append(f"‘{res['normalized']}’ 은(는) 우리말샘에서 찾지 못했습니다."
                     + (f" (뜻풀이 종류: {', '.join(res['types'])})" if res["types"] else ""))
        if res["base_forms"]:
            lines.append("활용형·준말로 실린 표제어:")
            for b in res["base_forms"]:
                lines.append(f"  - {b['word']} ({b['kind'] == 'abbreviation' and '준말' or '활용'}: {b['form']}) 〈{b['sense_type']}〉 [{b['target_code']}]")
        if res.get("stem_guess"):
            lines.append("기본형 추정(활용형일 수 있음): " + ", ".join(res["stem_guess"]))
        if res["suggestions"]:
            lines.append("비슷한 표제어: " + ", ".join(f"{s['word']}" + (f"〈{s['sense_type']}〉" if s["sense_type"] != "일반어" else "")
                                                    for s in res["suggestions"]))
        return "\n".join(lines)
    for g in res["groups"]:
        lines.append(f"■ {_head(g)}  [그룹 {g['group_code']}]")
        sub = []
        if g["origins"]:
            sub.append("원어: " + ", ".join(o["text"] + (f"({o['language']})" if o["language"] not in (None, "한자") else "")
                                           for o in g["origins"] if o["text"]))
        if g["pronunciations"]:
            sub.append("발음: " + ", ".join(p["text"] for p in g["pronunciations"] if p["text"]))
        if len(g["spellings"]) > 1:
            sub.append("표기: " + ", ".join(g["spellings"]))
        if g["conjugations"]:
            sub.append("활용·준말: " + ", ".join(c["form"] for c in g["conjugations"][:8]))
        for s in sub:
            lines.append("  " + s)
        for i, s in enumerate(g["senses"], 1):
            tag = f"〈{s['sense_type']}〉" if s["sense_type"] != "일반어" else ""
            pos = f"[{s['pos']}] " if s["pos"] and s["pos"] != "품사 없음" else ""
            extra = []
            if s["cats"]:
                extra.append("분야: " + ", ".join(s["cats"]))
            if s["regions"]:
                extra.append("지역: " + ", ".join(s["regions"]))
            lines.append(f"  {i}. {tag}{pos}{s['definition'] or '(뜻풀이 없음)'}" + (f"  ({'; '.join(extra)})" if extra else "")
                         + f"  [{s['target_code']}]")
            if s["scientific_name"]:
                lines.append(f"     학명: {s['scientific_name']}")
            for t in s["translations"]:
                lines.append(f"     번역({t['language']}): {t['text']}")
            for n in s["norm_info"]:
                lines.append(f"     {n['type']}: {n['desc']}" + (f" ({n['role']})" if n["role"] else ""))
            for p in s["patterns"]:
                lines.append(f"     {'문형' if p['kind'] == 'pattern' else '문법'}: {p['text']}")
            for ex in s.get("examples", []):
                src = f" ≪{ex['source']}≫" if ex.get("source") else ""
                reg = f" ({ex['region']})" if ex.get("region") else ""
                lines.append(f"     - {ex['text']}{src}{reg}")
            for r in s["relations"]:
                lines.append("     " + _rel_line(r))
            for pv in s["proverbs"]:
                lines.append(f"     {pv['type']}: {pv['word']} — {_cut(pv['definition'], 60)}")
        if g["senses_omitted"]:
            lines.append(f"  … 뜻풀이 {g['senses_omitted']}개 더 있음")
        if g["filtered_out"]:
            lines.append(f"  (종류 필터로 {g['filtered_out']}개 제외)")
        lines.append("")
    if res["total_groups"] > res["shown"]:
        lines.append(f"표제어 그룹 {res['total_groups']}개 중 {res['shown']}개만 보여 줍니다.")
    if any("ː" in (p["text"] or "") for g in res["groups"] for p in g["pronunciations"]):
        lines.append(LONG_NOTE)
    lines.append(ATTRIBUTION)          # 출처 줄이 항상 맨 끝(MCP 가 그 앞에 원본 보기 링크 줄을 끼운다)
    return "\n".join(lines).rstrip()


def format_search(res):
    if not res["hits"]:
        return f"‘{res['query']}’ ({res['by']}) 검색 결과가 없습니다."
    lines = [f"‘{res['query']}’ ({res['by']}) 검색 결과 {len(res['hits'])}건" + (" (상한)" if res["capped"] else "")]
    for h in res["hits"]:
        tag = f"〈{h['sense_type']}〉" if h["sense_type"] != "일반어" else ""
        pos = f"[{h['pos']}] " if h["pos"] and h["pos"] != "품사 없음" else ""
        lines.append(f"  - {h['word']} {tag}{pos}{h['definition'] or ''}  [{h['target_code']}]")
    lines.append(ATTRIBUTION)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("word", nargs="?", help="찾을 표제어(또는 --by 에 따른 검색어)")
    ap.add_argument("--by", choices=SEARCH_KINDS, help="표제어 말고 다른 방식으로 찾기")
    ap.add_argument("--code", type=int, help="코드(target_code)로 읽기")
    ap.add_argument("--type", dest="types", help="뜻풀이 종류 필터: 일반어, 방언, 북한어, 옛말(쉼표로 여러 개)")
    ap.add_argument("--unit", choices=UNITS, help="표제어 단위 필터")
    ap.add_argument("--q", dest="q_word", help="--by cat/region 에서 표제어 일부로 더 좁히기")
    ap.add_argument("--examples", action="store_true", help="용례 포함")
    ap.add_argument("--history", action="store_true", help="--code 와 함께: 어휘 역사 출력")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", help="DB 경로(기본 data/opendict.sqlite)")
    args = ap.parse_args()
    d = Dictionary(args.db)
    try:
        if args.code:
            if args.history:
                out = d.history(args.code)
                print(json.dumps(out, ensure_ascii=False, indent=1) if out else f"코드 {args.code} 에는 어휘 역사가 없습니다.")
                return
            res = d.entry(args.code)
            text = format_lookup(res)
        elif args.word is None:
            ap.error("찾을 단어나 --code 가 필요합니다.")
        elif args.by:
            res = d.search(args.word, args.by, args.types, args.unit, args.q_word, args.limit)
            text = format_search(res)
        else:
            res = d.lookup(args.word, args.types, args.unit, args.examples, args.limit)
            text = format_lookup(res)
    except OpendictError as e:
        sys.exit(str(e))
    print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else text)


if __name__ == "__main__":
    main()
