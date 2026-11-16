"""opendict_lookup 테스트. 실제 data/opendict.sqlite(공개 사전 데이터)를 읽기 전용으로 쓴다.

실행: python3 -m unittest tests.test_lookup -v   (저장소 루트에서)
다른 DB 로 시험하려면 환경변수 OPENDICT_LOOKUP_DB 를 지정한다.
수집·빌드가 진행되면 건수는 변하므로 개수가 아니라 구조와 기준 표제어만 확인한다.
"""
import os
import sqlite3
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import opendict_lookup as ol  # noqa: E402

DB_OK = ol.DEFAULT_DB.exists()
real = unittest.skipUnless(DB_OK, "조회용 DB(data/opendict.sqlite)가 없습니다")


class Helpers(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(ol.normalize("고-드^름  "), "고드름")
        self.assertEqual(ol.normalize(unicodedata.normalize("NFD", "고드름")), "고드름")      # macOS 자모 분리 입력

    def test_split_sup(self):
        self.assertEqual(ol._split_sup("빙주002"), ("빙주", 2))
        self.assertEqual(ol._split_sup("얼-음001"), ("얼-음", 1))
        self.assertEqual(ol._split_sup("고드름"), ("고드름", None))

    def test_types(self):
        self.assertEqual(ol._types("방언, 북한어"), ["방언", "북한어"])
        self.assertEqual(ol._types(None), [])
        with self.assertRaises(ol.OpendictError):
            ol._types("사투리")

    def test_pron_key(self):
        self.assertEqual(ol.pron_key("연ː기"), "연기")
        self.assertEqual(ol.pron_key("연:기"), "연기")

    def test_missing_db(self):
        d = ol.Dictionary(Path(tempfile.gettempdir()) / "없는-사전.sqlite")
        with self.assertRaises(ol.DictUnavailable):
            d.lookup("고드름")


@real
class Lookup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = ol.Dictionary()

    @classmethod
    def tearDownClass(cls):
        cls.d.close()

    def test_headword_golden(self):
        r = self.d.lookup("고드름")
        g = next(g for g in r["groups"] if g["group_code"] == 64845)
        self.assertEqual(g["word"], "고드름")
        s = g["senses"][0]
        self.assertEqual((s["target_code"], s["sense_type"]), (450817, "일반어"))
        self.assertTrue(any(rel["type"] == "비슷한말" for rel in s["relations"]))

    def test_north_korean_marked(self):
        r = self.d.lookup("나머지모임")
        self.assertEqual(r["groups"][0]["senses"][0]["sense_type"], "북한어")
        self.assertIn("〈북한어〉", ol.format_lookup(r))

    def test_type_filter_explains(self):
        r = self.d.lookup("고드름", types="방언")
        self.assertEqual(r["groups"], [])
        self.assertIn("일반어", r["other_types"])
        self.assertIn("다른 종류로", ol.format_lookup(r))

    def test_homonyms_grouped(self):
        r = self.d.lookup("연기")
        self.assertGreater(r["total_groups"], 5)
        self.assertEqual(len({g["group_code"] for g in r["groups"]}), r["shown"])

    def test_nfd_input(self):
        r = self.d.lookup(unicodedata.normalize("NFD", "고드름"))
        self.assertGreater(r["total_groups"], 0)

    def test_not_found_suggests(self):
        r = self.d.lookup("고드릠")
        self.assertEqual(r["groups"], [])
        self.assertTrue(r["suggestions"])
        self.assertEqual(self.d.lookup("먹었다")["stem_guess"], ["먹다"])

    def test_entry_by_code(self):
        r = self.d.entry(1775001)
        self.assertEqual(r["groups"][0]["focus"], 1775001)
        with self.assertRaises(ol.OpendictError):
            self.d.entry(999999999)

    def test_search_kinds(self):
        r = self.d.search("길게 얼어붙은 얼음", "definition", limit=5)
        self.assertIn(450817, [h["target_code"] for h in r["hits"]])
        self.assertIn(12560, [h["target_code"] for h in self.d.search("氷柱", "hanja", limit=10)["hits"]])   # 2글자: trigram 밖
        self.assertTrue(self.d.search("萬死一生", "hanja")["hits"])                                          # 3글자 이상: FTS
        self.assertTrue(self.d.search("연:기", "pronunciation")["hits"])
        self.assertTrue(self.d.search("가계해", "form")["hits"])
        self.assertTrue(self.d.search("의학", "cat", q_word="염")["hits"])
        self.assertTrue(self.d.search("제주", "region", types="방언")["hits"])

    def test_caret_shown_as_space(self):
        for h in self.d.search("의학", "cat", q_word="골수염")["hits"]:
            self.assertNotIn("^", h["word"])

    def test_bad_args(self):
        for bad in (lambda: self.d.search("x", "abc"), lambda: self.d.lookup(""), lambda: self.d.lookup("고드름", unit="없음")):
            with self.assertRaises(ol.OpendictError):
                bad()

    def test_attribution_in_output(self):
        self.assertIn("우리말샘", ol.format_lookup(self.d.lookup("고드름")))
        self.assertIn("우리말샘", ol.format_search(self.d.search("얼음", "definition")))


if __name__ == "__main__":
    unittest.main()
