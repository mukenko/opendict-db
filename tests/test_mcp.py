"""opendict_mcp 도구 테스트(SDK 없이 도구 함수를 직접 호출). 실제 data/opendict.sqlite 를 읽기 전용으로 쓴다.

실행: python3 -m unittest tests.test_mcp -v   (저장소 루트에서)
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import opendict_lookup as ol  # noqa: E402
import opendict_mcp as om  # noqa: E402

real = unittest.skipUnless(ol.DEFAULT_DB.exists(), "조회용 DB(data/opendict.sqlite)가 없습니다")


class Static(unittest.TestCase):
    def test_tools_listed(self):
        self.assertEqual([t.__name__ for t in om.TOOLS], ["lookup_word", "search_dictionary"])
        for t in om.TOOLS:
            self.assertTrue(t.__doc__ and "우리말샘" in t.__doc__)

    def test_notice_switch(self):
        old = os.environ.get("OPENDICT_MCP_NOTICE")
        try:
            os.environ["OPENDICT_MCP_NOTICE"] = "0"
            self.assertEqual(om._notice_tail(), "")
            os.environ["OPENDICT_MCP_NOTICE"] = "1"
            self.assertIn("Note to assistant", om._notice_tail())
        finally:
            if old is None:
                os.environ.pop("OPENDICT_MCP_NOTICE", None)
            else:
                os.environ["OPENDICT_MCP_NOTICE"] = old

    def test_port_distinct(self):
        self.assertEqual(om.HTTP_DEFAULT_PORT, 8768)      # stdict 8766, urimal 8767 과 겹치지 않는다


@real
class Tools(unittest.TestCase):
    def test_lookup_word(self):
        out = om.lookup_word("나머지모임")
        self.assertIn("〈북한어〉", out)
        self.assertIn("우리말샘", out)

    def test_errors_become_sentences(self):
        self.assertIn("사투리", om.lookup_word("고드름", kinds="사투리"))      # 서버가 죽지 않고 문장으로 돌려준다
        self.assertIn("검색어", om.search_dictionary("", "definition"))

    def test_search(self):
        self.assertIn("450817", om.search_dictionary("길게 얼어붙은 얼음", "definition", limit=5))

    def test_stdout_not_polluted(self):
        # 도구가 print 를 해도 stdio 프로토콜(stdout)을 깨지 않는다: _safe 가 stderr 로 돌린다
        @om._safe
        def noisy():
            print("잡음")
            return "ok"
        import io
        buf, old = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            self.assertEqual(noisy(), "ok")
        finally:
            sys.stdout = old
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
