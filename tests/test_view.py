"""'원본 보기' 링크: 결과 끝줄 규칙(opendict_mcp._with_footer), 링크 서버(opendict_view), 문서형 페이지(opendict_page).
실제 data/opendict.sqlite 를 읽기 전용으로 쓴다. MCP 프로토콜 시험은 mcp 패키지가 있는 파이썬(.venv)에서만 돈다.

실행: .venv/bin/python -m unittest tests.test_view -v   (저장소 루트에서)
"""
import asyncio
import http.client
import os
import re
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import opendict_lookup as ol  # noqa: E402
import opendict_mcp as om  # noqa: E402
import opendict_page as op  # noqa: E402
import opendict_view as ov  # noqa: E402

DB_OK = ol.DEFAULT_DB.exists()
needs_db = unittest.skipUnless(DB_OK, "조회용 DB(data/opendict.sqlite)가 없습니다")
LOOKUP = {"kind": "lookup", "q": "고드름", "kinds": None, "max_headwords": 12, "examples": False}
LINK = re.compile(r"^\[원본 보기\]\((http://127\.0\.0\.1:(\d+)/v/([0-9a-f]{32}))\)$", re.M)


def get(url, host=None, method="GET"):
    m = re.match(r"http://([^/]+)(/.*)$", url)
    conn = http.client.HTTPConnection(m.group(1), timeout=5)
    conn.request(method, m.group(2), headers={"Host": host or m.group(1)})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, body


class Footer(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, om, "HTTP_MODE", om.HTTP_MODE)
        self.addCleanup(os.environ.pop, "OPENDICT_VIEW_LINK", None)
        om.HTTP_MODE = False

    def text(self):
        return "결과 글\n" + ol.ATTRIBUTION

    def test_link_line_is_a_ready_markdown_link_before_the_source_line(self):
        lines = om._with_footer(self.text(), "고드름", LOOKUP).split("\n")
        self.assertEqual(lines[0], "결과 글")
        self.assertTrue(LINK.match(lines[1]), lines[1])
        self.assertEqual(lines[2], ol.ATTRIBUTION)
        self.assertEqual(len(lines), 3)

    def test_no_link_rules(self):
        self.assertEqual(om._with_footer(self.text(), "고드름", LOOKUP, found=False), self.text())   # 못 찾았으면 없음
        self.assertEqual(om._with_footer(self.text(), "고드름"), self.text())                        # 검색 조건이 없으면 없음
        om.HTTP_MODE = True                                                                        # 원격(파이) 서버: 없음
        self.assertEqual(om._with_footer(self.text(), "고드름", LOOKUP), self.text())
        om.HTTP_MODE = False
        for value in ("0", "off", "false", "no"):                                                  # 환경변수로 끄기
            os.environ["OPENDICT_VIEW_LINK"] = value
            self.assertEqual(om._with_footer(self.text(), "고드름", LOOKUP), self.text(), value)

    def test_server_failure_still_returns_the_result(self):
        with mock.patch.object(ov, "get_server", side_effect=OSError("port")):
            self.assertEqual(om._with_footer(self.text(), "고드름", LOOKUP), self.text())

    def test_link_rule_is_in_instructions_and_tool_descriptions_not_in_results(self):
        self.assertIn("MUST copy that exact line", om.INSTRUCTIONS)
        self.assertIn("[원본 보기](URL)", om.INSTRUCTIONS)
        for fn in om.TOOLS:
            self.assertIn("[원본 보기](URL)", fn.__doc__, fn.__name__)
        self.assertNotIn("MUST", om._with_footer(self.text(), "고드름", LOOKUP))              # 결과에는 사실과 주소만


class Rich(unittest.TestCase):
    def test_keeps_only_sub_sup_i_and_escapes_the_rest(self):
        out = op.rich("x", "H<sub>2</sub>O <FL>외국</FL> &#x2636; <script>alert(1)</script> 10<sup>-72</sup> <I>x</I> a<DR />b")
        self.assertIn("H<sub>2</sub>O", out)
        self.assertIn("☶", out)                                  # 글자 코드는 풀어서
        self.assertIn("10<sup>-72</sup>", out)
        self.assertIn("<i>x</i>", out)
        self.assertIn("a<br>b", out)
        self.assertNotIn("<script", out)
        self.assertNotIn("<FL", out)
        self.assertEqual(op.rich("<b>&", None), "&lt;b&gt;&amp;")      # 표지가 없으면 평문 이스케이프

    def test_stray_angle_brackets_in_plain_text(self):
        self.assertEqual(op.rich("<지대풍>이란", None), "&lt;지대풍&gt;이란")


@needs_db
class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ov.ViewServer()
        cls.server.start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_lookup_link_opens_a_document(self):
        url = self.server.add_query("고드름", **LOOKUP)
        status, body = get(url)
        page = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('lang="ko"', page)
        self.assertIn("고드름", page)
        self.assertIn("우리말샘 ↗", page)
        self.assertIn("/e/", page)                               # 관련어가 이 서버의 항목 화면으로 이어진다
        self.assertEqual(url, self.server.add_query("고드름", **LOOKUP))   # 같은 조건은 같은 링크

    def test_kinds_and_full_display(self):
        url = self.server.add_query("나머지모임", kind="lookup", q="나머지모임", kinds=None, max_headwords=1, examples=False)
        page = get(url)[1].decode("utf-8")
        self.assertIn("북한어", page)
        page = get(self.server.add_query("고드름", **LOOKUP))[1].decode("utf-8")
        chat = om.lookup_word("고드름")
        self.assertIn("외 ", chat)                               # 채팅 결과는 방언 관련어를 줄여 보여 주고
        self.assertNotIn("고지름", chat)
        self.assertIn(">고지름", page)                           # 화면에는 줄이지 않고 전부(링크로) 보여 준다
        self.assertNotIn("외 ", page)
        self.assertIn("어휘 역사", page)                         # 어휘 역사가 있는 뜻풀이는 접힌 칸으로

    def test_search_link_marks_the_found_sense(self):
        url = self.server.add_query("氷柱", kind="search", q="氷柱", by="hanja", kinds=None, limit=5)
        page = get(url)[1].decode("utf-8")
        self.assertIn("원어 검색", page)
        self.assertIn('class="focus"', page)

    def test_entry_page(self):
        status, body = get(f"{self.base}/e/1775001")
        self.assertEqual(status, 200)
        self.assertIn("나머지모임", body.decode("utf-8"))
        self.assertEqual(get(f"{self.base}/e/999999999")[0], 404)
        self.assertEqual(get(f"{self.base}/e/abc")[0], 400)

    def test_security_rules(self):
        url = self.server.add_query("고드름", **LOOKUP)
        self.assertEqual(get(url, host="evil.example.com")[0], 421)           # DNS 재바인딩 방지
        self.assertEqual(get(url, method="POST")[0], 405)                     # GET 만 허용
        self.assertEqual(get(f"{self.base}/etc/passwd")[0], 404)
        self.assertEqual(get(f"{self.base}/v/zzzz")[0], 400)                  # 잘못된 키
        self.assertEqual(get(f"{self.base}/v/" + "0" * 32)[0], 404)           # 등록 안 된 키
        r = urllib.request.urlopen(url, timeout=5)
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_page_has_no_raw_markup_leak(self):
        page = get(self.server.add_query("연기", kind="lookup", q="연기", kinds=None, max_headwords=12, examples=True))[1].decode("utf-8")
        self.assertNotIn("<FL>", page)
        self.assertNotIn("<DR", page)

    def test_not_found_renders_nothing(self):
        self.assertIsNone(self.server.render_spec("x", {"kind": "lookup", "q": "없는말쀏쀏쀏쀏"}))


@needs_db
class Always(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, om, "HTTP_MODE", om.HTTP_MODE)
        om.HTTP_MODE = False

    def test_link_always_present_for_found_entries(self):
        for word in ("고드름", "연기", "가다", "나머지모임"):
            out = om.lookup_word(word)
            self.assertRegex(out, LINK, word)
            lines = out.split("\n")
            self.assertTrue(lines[-3].startswith("[원본 보기](http"), word)          # 링크 → 출처 → 알림
            self.assertEqual(lines[-2], ol.ATTRIBUTION)
        for by, q in (("definition", "얼어붙은 얼음"), ("hanja", "氷柱"), ("pronunciation", "연:기"), ("form", "가계해"),
                      ("cat", "의학"), ("region", "제주")):
            self.assertRegex(om.search_dictionary(q, by), LINK, by)

    def test_long_vowel_note_stays_before_the_link(self):
        out = om.lookup_word("연기")                                              # 장음(ː) 안내가 있는 결과
        lines = out.split("\n")
        self.assertTrue(any(ol.LONG_NOTE in ln for ln in lines))
        self.assertLess([i for i, ln in enumerate(lines) if ol.LONG_NOTE in ln][0], [i for i, ln in enumerate(lines) if ln.startswith("[원본 보기]")][0])

    def test_no_link_when_nothing_found(self):
        self.assertNotIn("원본 보기", om.lookup_word("없는말쀏쀏쀏쀏"))
        self.assertNotIn("원본 보기", om.search_dictionary("없는말쀏쀏쀏쀏", "hanja"))

    def test_remote_mode_returns_the_same_result_without_link(self):
        local = om.lookup_word("고드름")
        om.HTTP_MODE = True
        remote = om.lookup_word("고드름")
        self.assertNotIn("원본 보기", remote)
        self.assertEqual(remote, "\n".join(ln for ln in local.split("\n") if not ln.startswith("[원본 보기](")))


try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False


@needs_db
@unittest.skipUnless(HAVE_MCP, "mcp 패키지가 없는 파이썬입니다(.venv 로 실행)")
class Protocol(unittest.TestCase):
    def test_link_opens_while_the_mcp_process_is_alive(self):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(Path(om.__file__))], env={**os.environ, "OPENDICT_MCP_NOTICE": "0"})
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    init = await s.initialize()
                    self.assertIn("[원본 보기](URL)", init.instructions)
                    out = (await s.call_tool("lookup_word", {"word": "고드름"})).content[0].text
                    lines = out.split("\n")
                    m = LINK.match(lines[-2])
                    self.assertTrue(m, lines[-2])
                    self.assertEqual(lines[-1], ol.ATTRIBUTION)
                    with urllib.request.urlopen(m.group(1), timeout=5) as resp:       # MCP 서버 프로세스가 링크 서버를 띄운다
                        self.assertEqual(resp.status, 200)
                        self.assertIn("고드름", resp.read().decode("utf-8"))
                    miss = (await s.call_tool("lookup_word", {"word": "없는말쀏쀏쀏쀏"})).content[0].text
                    self.assertNotIn("원본 보기", miss)
        asyncio.run(run())

    def test_link_can_be_switched_off(self):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(Path(om.__file__))],
                                           env={**os.environ, "OPENDICT_VIEW_LINK": "0", "OPENDICT_MCP_NOTICE": "0"})
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    out = (await s.call_tool("lookup_word", {"word": "고드름"})).content[0].text
                    self.assertNotIn("원본 보기", out)
                    self.assertIn(ol.ATTRIBUTION, out)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
