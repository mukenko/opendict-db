"""조회 결과를 사전 항목처럼 구조를 살려 그리는 HTML(문서형). 표준 라이브러리만 사용.

opendict_view.py 의 링크 서버가 '원본 보기'를 열 때 쓴다. 조회 코어(opendict_lookup.Dictionary)로 DB 에서 읽어(full=True: 뜻풀이·용례·
관련어를 줄이지 않는다) 다음처럼 그린다.

  - 맨 위: 표제어 그룹이 여럿이면 바로가기 목차.
  - 그룹(표제어)마다: 표제어+어깨번호, 원어, 발음, 단어/구·고유어/한자어, 오른쪽 위 "우리말샘 ↗"(누리집의 그 항목).
  - 뜻풀이(번호): 종류 표시(방언·북한어·옛말), 전문 분야, 지역, 품사, 학명, 문형·문법, 번역어, 규범 정보, 용례(출전 ≪…≫),
    관련어(종류별, 이 DB 에 있으면 이 서버의 항목 화면으로 가는 링크), 속담·관용구, 어휘 역사.

글자는 DB 에서 읽은 그대로이고 HTML 로만 이스케이프한다(모델이 다시 쓰지 않는다). 뜻풀이·용례 안의 <sub>, <sup>, <i> 표지만 살리고
나머지 표지는 떼어 낸다.
"""
import html
import os
import re
from pathlib import Path

import opendict_lookup as ol

SITE_VIEW = "https://opendict.korean.go.kr/dictionary/view"
# 옛한글(옛말·어휘 역사의 옛 글자)은 시스템 글꼴로는 깨진다: 나눔바른고딕 옛한글판을 서버가 /f/font.otf 로 한 번만 보내 웹 글꼴로 쓴다
# (Safari 는 설치된 글꼴을 웹 페이지가 못 쓰게 막으므로 설치만으로는 안 된다). 글꼴이 없으면 일반 시스템 글꼴이다.
FONT_FILE = "NanumBarunGothic-YetHangul.otf"
FONT_DIRS = (Path.home() / "Library" / "Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"))
FONT_URL = "/f/font.otf"
FONT_STACK = ('"OpendictYetHangul", -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans CJK KR", '
              '"Noto Sans KR", "Segoe UI", sans-serif')
NAMES = {"definition": "뜻풀이", "hanja": "원어", "pronunciation": "발음", "form": "활용형", "cat": "전문 분야", "region": "방언 지역"}


class Miss(Exception):
    """보여 줄 항목이 없다(조건에 맞는 표제어가 이제 없음)."""


def find_font():
    """바른고딕 옛한글판 글꼴 파일의 경로(없거나 OPENDICT_VIEW_FONT=0 이면 None). 설치된 것을 읽을 뿐이고 저장소에는 글꼴이 없다.
    OPENDICT_FONT_DIR 로 찾는 폴더를 바꿀 수 있다."""
    if os.environ.get("OPENDICT_VIEW_FONT", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    dirs = [Path(os.environ["OPENDICT_FONT_DIR"])] if os.environ.get("OPENDICT_FONT_DIR") else list(FONT_DIRS)
    for d in dirs:
        try:
            if (d / FONT_FILE).is_file():
                return d / FONT_FILE
        except OSError:
            continue
    return None


def original_url(target_code):
    """국립국어원 우리말샘 누리집에서 그 뜻풀이를 여는 주소(sense_no = target_code)."""
    return f"{SITE_VIEW}?sense_no={int(target_code)}"


def esc(text):
    return html.escape(text or "", quote=False)


def _attr(text):
    return html.escape(text or "", quote=True)


# ---------------------------------------------------------------- 표지가 있는 글
_TAG = re.compile(r"<(/?)([A-Za-z_]+)([^>]*)>")
_KEEP = {"sub": "sub", "sup": "sup", "i": "i"}


def rich(plain, raw=None):
    """뜻풀이·용례를 그린다. 표지(raw)가 있으면 <sub>·<sup>·<i> 만 살리고(<DR />는 줄바꿈), 나머지 표지는 떼고, 글자 코드(&#x…;)는 푼다.
    표지가 없으면 평문을 이스케이프한다. 허용한 표지 말고는 어떤 것도 HTML 로 내보내지 않는다."""
    if not raw:
        return esc(plain)
    out, pos = [], 0
    for m in _TAG.finditer(raw):
        out.append(esc(html.unescape(raw[pos:m.start()])))
        closing, name = m.group(1), m.group(2).lower()
        if name == "dr":
            out.append("<br>")
        elif name in _KEEP:
            out.append(f"</{_KEEP[name]}>" if closing else f"<{_KEEP[name]}>")
        pos = m.end()
    out.append(esc(html.unescape(raw[pos:])))
    return "".join(out)


# ---------------------------------------------------------------- 조각
def _word_link(word, code, sup_no, existing):
    label = esc(word) + ol.sup(sup_no)
    return f'<a href="/e/{int(code)}">{label}</a>' if code and code in existing else label


def _rels(rels, existing):
    out = []
    for r in rels:
        words = ", ".join(_word_link(w["word"], w["link_code"], w["sup_no"], existing) for w in r["words"])
        more = f' <span class="meta">외 {r["omitted"]}개</span>' if r["omitted"] else ""
        out.append(f'<span class="rel"><b>{esc(r["type"])}</b> {words}{more}</span>')
    return f'<div class="rels">{"".join(out)}</div>' if out else ""


def _history_html(h):
    if not h:
        return ""
    head = " · ".join(x for x in (h["word_form"], h["allomorph"]) if x)
    parts = [f'<summary>어휘 역사{(" — " + esc(head)) if head else ""}</summary>']
    if h["desc"]:
        parts.append(f'<p class="note">{esc(h["desc"])}</p>')
    for c in h["centuries"]:
        exs = []
        for x in c["examples"]:
            src = f' <cite>≪{esc(x["source"])}≫</cite>' if x["source"] else ""
            org = f' <span class="orig">{esc(x["origin"])}</span>' if x["origin"] else ""
            exs.append(f'<li>{esc(x["text"])}{src}{org}</li>')
        mark = f' <span class="meta">{esc(c["mark"])}</span>' if c["mark"] else ""
        parts.append(f'<div class="cent"><b>{esc(c["century"])}</b>{mark}<ul class="ex">{"".join(exs)}</ul></div>')
    if h["remark"]:
        parts.append(f'<p class="note">{esc(h["remark"])}</p>')
    return f'<details class="hist">{"".join(parts)}</details>'


def _sense_html(i, s, existing, history, focus):
    typ = f'<span class="typ">{esc(s["sense_type"])}</span>' if s["sense_type"] and s["sense_type"] != "일반어" else ""
    pos = f'<span class="pos">〔{esc(s["pos"])}〕</span> ' if s["pos"] and s["pos"] != "품사 없음" else ""
    cats = "".join(f'<span class="cat">{esc(c)}</span>' for c in s["cats"])
    regs = "".join(f'<span class="reg">{esc(r)}</span>' for r in s["regions"])
    sci = f' <span class="sci">(학명: {esc(s["scientific_name"])})</span>' if s["scientific_name"] else ""
    pats = "".join(f'<div class="pat">{"문형" if p["kind"] == "pattern" else "문법"}: {esc(p["text"])}</div>' for p in s["patterns"])
    trans = "".join(f'<div class="pat">번역({esc(t["language"])}): {esc(t["text"])}</div>' for t in s["translations"])
    norm = []
    for n in s["norm_info"]:
        role = f' ({esc(n["role"])})' if n["role"] else ""
        norm.append(f'<div class="pat"><b>{esc(n["type"])}</b> {esc(n["desc"])}{role}</div>')
    norm = "".join(norm)
    exs = []
    for x in s.get("examples", []):
        src = f' <cite>≪{esc(x["source"])}≫</cite>' if x.get("source") else ""
        reg = f' <span class="reg">{esc(x["region"])}</span>' if x.get("region") else ""
        org = f' <span class="orig">{esc(x["origin"])}</span>' if x.get("origin") else ""
        tr = f' <span class="pat">— {esc(x["translation"])}</span>' if x.get("translation") else ""
        exs.append(f'<li>{rich(x["text"], x.get("html"))}{src}{reg}{org}{tr}</li>')
    exs = f'<ul class="ex">{"".join(exs)}</ul>' if exs else ""
    prov = "".join(
        f'<div class="phrase"><span class="tag">{esc(p["type"])}</span> '
        f'{_word_link(p["word"], p["link_code"], None, existing)} — {esc(p["definition"])}</div>' for p in s["proverbs"])
    media = ", ".join(f'{esc(m["type"])} {esc(m["label"])}' for m in s["multimedia"] if m["label"])
    media = f'<div class="pat">멀티미디어: {media}</div>' if media else ""
    cls = ' class="focus"' if focus == s["target_code"] else ""
    site = (f'<a class="code" href="{_attr(original_url(s["target_code"]))}" target="_blank" rel="noopener noreferrer">'
            f'{s["target_code"]} ↗</a>')
    return (f'<li id="s{s["target_code"]}" value="{i}"{cls}>{typ}{cats}{regs} {pos}'
            f'{rich(s["definition"], s.get("definition_html"))}{sci} {site}{pats}{trans}{norm}{media}{exs}'
            f'{_rels(s["relations"], existing)}{prov}{_history_html(history.get(s["target_code"]))}</li>')


def group_html(g, existing, history, focus=None):
    head = f'{esc(g["word"])}{ol.sup(g["sup_no"])}'
    orig = ", ".join(o["text"] + (f' ({o["language"]})' if o["language"] not in (None, "한자") else "")
                     for o in g["origins"] if o["text"])
    orig = f'<span class="orig">({esc(orig)})</span>' if orig else ""
    prons = " / ".join(p["text"] for p in g["pronunciations"] if p["text"])
    pron = f'<span class="pron">[{esc(prons)}]</span>' if prons else ""
    kind = "·".join(x for x in (g["unit"] if g["unit"] != "어휘" else None, g["type"]) if x)
    kind = f'<span class="meta">{esc(kind)}</span>' if kind else ""
    first = g["senses"][0]["target_code"] if g["senses"] else None
    button = (f'<a class="site" href="{_attr(original_url(first))}" target="_blank" rel="noopener noreferrer">우리말샘 ↗</a>'
              if first else "")
    parts = [f'<article id="g{g["group_code"]}"><header><h2>{head}</h2>{orig}{pron}{kind}{button}</header>']
    if len(g["spellings"]) > 1:
        parts.append(f'<p class="note"><b>표기</b> {esc(", ".join(g["spellings"]))}</p>')
    if g["conjugations"]:
        by = {}
        for c in g["conjugations"]:
            by.setdefault("준말" if c["kind"] == "abbreviation" else "활용", []).append(c["form"])
        parts.append('<p class="note">' + " · ".join(f'<b>{k}</b> {esc(", ".join(v))}' for k, v in by.items()) + "</p>")
    senses = "".join(_sense_html(i, s, existing, history, focus) for i, s in enumerate(g["senses"], 1))
    parts.append(f'<ol class="senses">{senses}</ol>')
    if g.get("senses_omitted"):
        parts.append(f'<p class="note">뜻풀이 {g["senses_omitted"]}개 더 있음</p>')
    parts.append("</article>")
    return "".join(parts)


def toc_html(groups):
    if len(groups) < 2:
        return ""
    items = []
    for g in groups:
        orig = ", ".join(o["text"] for o in g["origins"] if o["text"])
        types = sorted({s["sense_type"] for s in g["senses"] if s["sense_type"] != "일반어"})
        tag = f' 〈{esc("·".join(types))}〉' if types else ""
        orig = f" ({esc(orig)})" if orig else ""
        items.append(f'<li><a href="#g{g["group_code"]}">{esc(g["word"])}{ol.sup(g["sup_no"])}{orig}{tag}</a></li>')
    return f'<nav class="toc"><ol>{"".join(items)}</ol></nav>'


STYLE = """
  :root { color-scheme: light dark; --bg:#fff; --fg:#1f2328; --mute:#6a737d; --line:#e1e4e8; --soft:#f6f8fa; --accent:#0b5cad; --hl:#fff3bf; }
  @media (prefers-color-scheme: dark) { :root { --bg:#16181d; --fg:#e6e8eb; --mute:#9aa4af; --line:#2c313a; --soft:#1e2229; --accent:#7db5ff; --hl:#4a3f12; } }
  * { box-sizing: border-box; }
  body { margin: 0 auto; max-width: 46rem; padding: 1.4rem 1.1rem 3rem; background: var(--bg); color: var(--fg);
         font: 17px/1.7 __STACK__; }
  .site-name { color: var(--mute); font-size: 13px; letter-spacing: .04em; }
  h1 { font-size: 1.1rem; margin: .2rem 0 1rem; color: var(--mute); font-weight: 600; }
  article { margin: 1.4rem 0; padding-top: 1rem; border-top: 1px solid var(--line); }
  header { display: flex; flex-wrap: wrap; align-items: baseline; gap: .3rem .7rem; }
  h2 { font-size: 1.7rem; line-height: 1.3; margin: 0; }
  h2 sup { font-size: .55em; color: var(--accent); }
  .orig { color: var(--mute); }
  .pron { font-weight: 600; }
  .meta { border: 1px solid var(--line); border-radius: 999px; padding: 0 .6rem; font-size: 12px; line-height: 1.6; color: var(--mute); font-weight: 400; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  a.site { margin-left: auto; font-size: 12px; color: var(--mute); border: 1px solid var(--line); border-radius: 999px; padding: .05rem .75rem; white-space: nowrap; }
  a.site:hover { color: var(--accent); border-color: var(--accent); text-decoration: none; }
  a.code { font-size: 11px; color: var(--mute); margin-left: .3rem; }
  .note { margin: .4rem 0; color: var(--mute); font-size: 15px; }
  .note b, .rel b { font-size: 12px; border: 1px solid var(--line); border-radius: .3rem; padding: 0 .35rem; margin-right: .3rem; font-weight: 500; }
  ol.senses { margin: .6rem 0; padding-left: 1.6rem; }
  ol.senses > li { margin: .55rem 0; padding-left: .2rem; }
  ol.senses > li.focus { background: var(--hl); border-radius: .3rem; }
  .cat, .reg { font-size: 12px; color: var(--accent); margin-right: .25rem; }
  .typ { font-size: 12px; color: var(--mute); border: 1px solid var(--line); border-radius: .3rem; padding: 0 .35rem; margin-right: .3rem; }
  .pos, .sci, .pat { color: var(--mute); font-size: 14px; }
  cite { font-style: normal; font-size: 13px; }
  ul.ex { margin: .2rem 0; padding-left: 1.2rem; color: var(--mute); font-size: 15px; }
  .rels { margin: .2rem 0; color: var(--mute); font-size: 14px; display: flex; flex-wrap: wrap; gap: .1rem .9rem; }
  nav.toc { margin: 1rem 0; padding: .55rem .9rem; background: var(--soft); border: 1px solid var(--line); border-radius: .6rem; }
  nav.toc ol { list-style: none; margin: 0; padding: 0; }
  nav.toc li { padding: .12rem 0; }
  .phrase { margin: .3rem 0; padding-left: .8rem; border-left: 3px solid var(--line); font-size: 15px; }
  .phrase .tag { font-size: 12px; color: var(--mute); border: 1px solid var(--line); border-radius: .3rem; padding: 0 .4rem; margin-right: .3rem; }
  details.hist { margin: .4rem 0; font-size: 14px; color: var(--mute); }
  details.hist summary { cursor: pointer; }
  .cent { margin: .3rem 0 .3rem .6rem; }
  .lead { color: var(--mute); font-size: 14px; }
  footer { margin-top: 2.2rem; padding-top: .8rem; border-top: 1px solid var(--line); font-size: 13px; color: var(--mute); }
"""


def _page(title, body, subtitle=""):
    face = (f"  @font-face {{ font-family: 'OpendictYetHangul'; src: url({FONT_URL}) format('opentype'); }}\n"
            if find_font() else "")
    css = face + STYLE.replace("__STACK__", FONT_STACK)
    sub = f'<p class="lead">{esc(subtitle)}</p>' if subtitle else ""
    return (f'<!doctype html>\n<html lang="ko">\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{esc(title)} — 우리말샘</title>\n<style>{css}</style>\n'
            f'<div class="site-name">우리말샘</div>\n<h1>{esc(title)}</h1>\n{sub}{body}\n'
            f'<footer>{esc(ol.ATTRIBUTION)}</footer>\n')


def _links(d, groups):
    """그룹들에서 가리키는 관련어·속담 코드 중 이 DB 에 있는 것({코드}), 어휘 역사({코드: 역사})."""
    codes, senses = set(), []
    for g in groups:
        for s in g["senses"]:
            senses.append(s["target_code"])
            codes.update(w["link_code"] for r in s["relations"] for w in r["words"] if w["link_code"])
            codes.update(p["link_code"] for p in s["proverbs"] if p["link_code"])
    existing = set()
    codes = sorted(codes)
    for i in range(0, len(codes), 500):
        chunk = codes[i:i + 500]
        existing.update(r[0] for r in d.q(f"SELECT target_code FROM entries WHERE target_code IN ({','.join('?' * len(chunk))})", chunk))
    history = {}
    for c in senses:
        h = d.history(c)
        if h:
            history[c] = h
    return existing, history


def render_lookup(d, word, kinds=None, include_examples=True):
    """표제어 조회 화면. 그룹·뜻풀이·용례를 줄이지 않는다(kinds·max_headwords 는 채팅 결과의 조건이며 링크 키에만 쓴다).
    표제어가 없으면 Miss."""
    res = d.lookup(word, None, None, True, full=True)
    if not res["groups"]:
        raise Miss(word)
    existing, history = _links(d, res["groups"])
    body = toc_html(res["groups"]) + "".join(group_html(g, existing, history) for g in res["groups"])
    return _page(res["normalized"], body, f"표제어 {res['total_groups']}개")


def render_search(d, query, by, kinds=None, limit=10):
    """역검색 화면: 찾은 뜻풀이가 들어 있는 표제어를 모두 문서형으로(찾은 뜻풀이는 강조). 결과가 없으면 Miss."""
    res = d.search(query, by, kinds, None, None, limit)
    if not res["hits"]:
        raise Miss(query)
    groups, focus, seen = [], {}, set()
    for h in res["hits"]:
        r = d.entry(h["target_code"], True)
        g = r["groups"][0]
        focus[g["group_code"]] = h["target_code"]
        if g["group_code"] not in seen:
            seen.add(g["group_code"])
            groups.append(g)
    existing, history = _links(d, groups)
    body = toc_html(groups) + "".join(group_html(g, existing, history, focus.get(g["group_code"])) for g in groups)
    note = f" ({ol.LONG_NOTE})" if by == "pronunciation" else ""
    return _page(f"{NAMES[by]} 검색 「{ol.normalize(query)}」", body, f"{len(res['hits'])}개{' (상한)' if res['capped'] else ''}{note}")


def render_entry(d, code):
    """뜻풀이 하나(target_code)가 속한 표제어의 화면(그 뜻풀이를 강조). 없으면 Miss."""
    try:
        res = d.entry(code, True)
    except ol.OpendictError:
        raise Miss(code)
    g = res["groups"][0]
    existing, history = _links(d, [g])
    return _page(g["word"] + ol.sup(g["sup_no"]), group_html(g, existing, history, code))


def render_spec(d, spec):
    """링크에 등록된 검색 조건(spec)으로 화면을 그린다."""
    if spec["kind"] == "entry":
        return render_entry(d, spec["code"])
    if spec["kind"] == "lookup":
        return render_lookup(d, spec["q"], spec.get("kinds"), spec.get("examples", True))
    return render_search(d, spec["q"], spec["by"], spec.get("kinds"), spec["limit"])
