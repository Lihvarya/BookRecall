import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.dynamic_index import build_dynamic_index_records
from bookrecall.models import EntityMention, EntityRecord, EventRecord
from bookrecall.storage import BookRecallStore


class FakeDynamicClient:
    def complete_json(self, prompt: str) -> dict:
        return {
            "entities": [
                {
                    "name": "李四",
                    "aliases": [],
                    "evidence": "李四被王五刺中后倒在雨里",
                    "confidence": 0.9,
                }
            ],
            "relations": [
                {
                    "source": "王五",
                    "target": "李四",
                    "type": "冲突",
                    "evidence": "李四被王五刺中后倒在雨里",
                    "confidence": 0.9,
                }
            ],
            "events": [
                {
                    "type": "冲突/危机",
                    "summary": "王五刺伤李四",
                    "evidence": "李四被王五刺中后倒在雨里",
                    "entities": ["王五", "李四"],
                    "confidence": 0.9,
                }
            ],
        }


class HallucinatingDynamicClient:
    def complete_json(self, prompt: str) -> dict:
        return {
            "entities": [
                {
                    "name": "太白云生",
                    "aliases": ["老白"],
                    "evidence": "太白云生正处于心境崩溃",
                    "confidence": 0.95,
                },
                {
                    "name": "方源",
                    "aliases": [],
                    "evidence": "方源暗中催动蛊虫，导致太白云生心神崩溃",
                    "confidence": 0.95,
                },
                {
                    "name": "心神崩溃",
                    "aliases": [],
                    "evidence": "太白云生正处于心境崩溃",
                    "confidence": 0.99,
                },
            ],
            "relations": [
                {
                    "source": "太白云生",
                    "target": "方源",
                    "type": "共现/关联",
                    "evidence": "方源暗中催动蛊虫，导致太白云生心神崩溃",
                    "confidence": 0.99,
                },
                {
                    "source": "太白云生",
                    "target": "方源",
                    "type": "交易/利用",
                    "evidence": "方源利用蛊虫影响太白云生",
                    "confidence": 0.9,
                },
            ],
            "events": [
                {
                    "type": "转折/后果",
                    "summary": "太白云生被方源杀害死亡",
                    "evidence": "黑棺气运带来死气，太白云生面对死亡心神动荡",
                    "entities": ["太白云生", "方源"],
                    "confidence": 0.99,
                },
                {
                    "type": "因果链",
                    "summary": "方源催动蛊虫导致太白云生心神崩溃",
                    "evidence": "方源暗中催动蛊虫，导致太白云生心神崩溃",
                    "entities": ["方源", "太白云生"],
                    "confidence": 0.9,
                },
            ],
        }


class DynamicIndexTest(unittest.TestCase):
    def test_build_dynamic_index_records_validates_evidence_from_hits(self) -> None:
        hits = [
            {
                "chapter_number": 8,
                "chapter_title": "雨夜",
                "child_text": "李四被王五刺中后倒在雨里，众人这才明白旧案真相。",
            }
        ]

        entities, relations, events, report = build_dynamic_index_records(
            question="李四是怎么死的？",
            hits=hits,
            client=FakeDynamicClient(),
            known_entities=["王五"],
        )

        self.assertTrue(report["used"])
        self.assertEqual(entities[0].name, "李四")
        self.assertEqual(entities[0].confidence, 0.9)
        self.assertEqual(relations[0].relation_type, "冲突")
        self.assertEqual(relations[0].confidence, 0.9)
        self.assertEqual(events[0].summary, "王五刺伤李四")
        self.assertEqual(events[0].confidence, 0.9)
        self.assertEqual(report["source_model"], "FakeDynamicClient")

    def test_dynamic_index_rejects_unsupported_death_and_low_value_relations(self) -> None:
        hits = [
            {
                "chapter_number": 609,
                "chapter_title": "可怜人",
                "child_text": (
                    "黑棺气运带来死气，太白云生面对死亡心神动荡。"
                    "方源暗中催动蛊虫，导致太白云生心神崩溃。"
                    "方源利用蛊虫影响太白云生。"
                ),
            }
        ]

        entities, relations, events, report = build_dynamic_index_records(
            question="太白云生怎么死的？",
            hits=hits,
            client=HallucinatingDynamicClient(),
            known_entities=["太白云生", "方源"],
        )

        self.assertEqual([record.name for record in entities], ["太白云生", "方源"])
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].relation_type, "交易/利用")
        self.assertEqual(len(events), 1)
        self.assertIn("心神崩溃", events[0].summary)
        self.assertNotIn("死亡", events[0].summary)
        self.assertNotIn("老白", entities[0].aliases)
        self.assertEqual(report["quality_gate"], "grounded_v2")
        self.assertGreaterEqual(report["rejected_entities"], 1)
        self.assertGreaterEqual(report["rejected_relations"], 1)
        self.assertGreaterEqual(report["rejected_events"], 1)

    def test_dynamic_event_writeback_merges_near_duplicates_and_keeps_richer_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = BookRecallStore(str(Path(tempdir) / "dynamic.db"))
            store.initialize()
            store.connection.execute(
                "INSERT INTO books(book_id, title, source_path) VALUES (?, ?, ?)",
                ("book", "测试书", "memory"),
            )
            store.connection.commit()
            first = EventRecord(
                chapter_number=657,
                chapter_title="再见智慧蛊",
                event_type="因果链",
                summary="太白云生因愧疚和方源陷害而寻死",
                excerpt="太白云生因愧疚和方源陷害而寻死。",
                entities=["太白云生", "方源"],
            )
            richer = EventRecord(
                chapter_number=657,
                chapter_title="再见智慧蛊",
                event_type="因果链",
                summary="因愧疚和方源陷害，太白云生几近入魔并冲动寻死",
                excerpt="在方源陷害之下，太白云生几近入魔；他因愧疚而寻死觅活。",
                entities=["太白云生", "方源", "巨阳意志"],
            )

            first_result = store.upsert_dynamic_index_records(book_id="book", event_records=[first])
            second_result = store.upsert_dynamic_index_records(book_id="book", event_records=[richer])
            rows = store.connection.execute(
                "SELECT event_id, excerpt FROM events WHERE book_id = ? AND chapter_number = ?",
                ("book", 657),
            ).fetchall()
            event_entities = store.connection.execute(
                "SELECT entity_name FROM event_entities WHERE book_id = ? ORDER BY entity_name",
                ("book",),
            ).fetchall()
            store.close()

        self.assertEqual(first_result["events"], 1)
        self.assertEqual(second_result["events"], 0)
        self.assertEqual(len(rows), 1)
        self.assertIn("几近入魔", rows[0]["excerpt"])
        self.assertEqual([row["entity_name"] for row in event_entities], ["太白云生", "巨阳意志", "方源"])

    def test_dynamic_writeback_persists_audit_metadata_and_keeps_legacy_visible(self) -> None:
        hits = [
            {
                "chapter_number": 8,
                "chapter_title": "雨夜",
                "child_text": "李四被王五刺中后倒在雨里，众人这才明白旧案真相。",
            }
        ]
        entities, relations, events, report = build_dynamic_index_records(
            question="李四怎么受伤的？",
            hits=hits,
            client=FakeDynamicClient(),
            known_entities=["王五"],
        )
        with tempfile.TemporaryDirectory() as tempdir:
            store = BookRecallStore(str(Path(tempdir) / "dynamic.db"))
            store.initialize()
            store.connection.execute(
                "INSERT INTO books(book_id, title, source_path) VALUES (?, ?, ?)",
                ("book", "测试书", "memory"),
            )
            store.connection.execute(
                """
                INSERT INTO events(event_id, book_id, chapter_number, chapter_title, event_type, summary, excerpt)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("book:event:dynamic:2:legacy", "book", 2, "旧章", "因果链", "旧记录", "旧记录导致后果"),
            )
            store.connection.commit()

            writes = store.upsert_dynamic_index_records(
                book_id="book",
                entity_records=entities,
                relation_records=relations,
                event_records=events,
                source_query="李四怎么受伤的？",
                source_model=report["source_model"],
                quality_gate=report["quality_gate"],
            )
            audit = store.list_dynamic_index_audit("book")
            stats = store.get_dynamic_index_audit_stats("book")
            store.close()

        self.assertEqual(writes["audit_records"], 3)
        self.assertEqual({row["record_kind"] for row in audit}, {"entity_mention", "relation_mention", "event"})
        self.assertTrue(all(row["confidence"] == 0.9 for row in audit))
        self.assertTrue(all(row["quality_gate"] == "grounded_v2" for row in audit))
        self.assertTrue(all(row["source_model"] == "FakeDynamicClient" for row in audit))
        self.assertEqual(stats["tracked_total"], 3)
        self.assertEqual(stats["legacy_untracked"]["event"], 1)

    def test_dynamic_audit_review_updates_index_and_blocks_rejected_resurrection(self) -> None:
        hits = [
            {
                "chapter_number": 8,
                "chapter_title": "雨夜",
                "child_text": "李四被王五刺中后倒在雨里，众人这才明白旧案真相。",
            }
        ]
        entities, relations, events, report = build_dynamic_index_records(
            question="李四怎么受伤的？",
            hits=hits,
            client=FakeDynamicClient(),
            known_entities=["王五"],
        )
        with tempfile.TemporaryDirectory() as tempdir:
            store = BookRecallStore(str(Path(tempdir) / "dynamic.db"))
            store.initialize()
            store.connection.execute(
                "INSERT INTO books(book_id, title, source_path) VALUES (?, ?, ?)",
                ("book", "测试书", "memory"),
            )
            store.connection.commit()
            store.upsert_dynamic_index_records(
                book_id="book",
                entity_records=entities,
                relation_records=relations,
                event_records=events,
                source_query="李四怎么受伤的？",
                source_model=report["source_model"],
                quality_gate=report["quality_gate"],
            )
            audit = store.list_dynamic_index_audit("book")
            by_kind = {str(row["record_kind"]): row for row in audit}

            confirmed = store.review_dynamic_index_audit(
                book_id="book",
                audit_id=str(by_kind["entity_mention"]["audit_id"]),
                action="confirm",
                note="已核对原文",
                expected_review_version=0,
            )
            corrected = store.review_dynamic_index_audit(
                book_id="book",
                audit_id=str(by_kind["event"]["audit_id"]),
                action="correct",
                evidence="李四被王五刺中后倒在雨里，旧案由此揭开。",
                summary="王五刺伤李四并揭开旧案",
                confidence=0.97,
                note="补足因果结果",
                expected_review_version=0,
            )
            rejected = store.review_dynamic_index_audit(
                book_id="book",
                audit_id=str(by_kind["relation_mention"]["audit_id"]),
                action="reject",
                note="仅凭这一句不足以建立长期关系",
                expected_review_version=0,
            )

            event_row = store.connection.execute(
                "SELECT summary, excerpt FROM events WHERE event_id = ?",
                (by_kind["event"]["record_id"],),
            ).fetchone()
            relation_count = int(
                store.connection.execute("SELECT COUNT(*) FROM relations WHERE book_id = ?", ("book",)).fetchone()[0]
            )
            relation_mention_count = int(
                store.connection.execute(
                    "SELECT COUNT(*) FROM relation_mentions WHERE book_id = ?",
                    ("book",),
                ).fetchone()[0]
            )
            store.upsert_dynamic_index_records(
                book_id="book",
                relation_records=relations,
                source_query="李四怎么受伤的？",
                source_model=report["source_model"],
                quality_gate=report["quality_gate"],
            )
            resurrected_count = int(
                store.connection.execute(
                    "SELECT COUNT(*) FROM relation_mentions WHERE book_id = ?",
                    ("book",),
                ).fetchone()[0]
            )
            stats = store.get_dynamic_index_audit_stats("book")
            with self.assertRaisesRegex(ValueError, "已被其他操作更新"):
                store.review_dynamic_index_audit(
                    book_id="book",
                    audit_id=str(by_kind["entity_mention"]["audit_id"]),
                    action="confirm",
                    expected_review_version=0,
                )
            store.close()

        self.assertEqual(confirmed["record"]["status"], "confirmed")
        self.assertEqual(confirmed["record"]["review_note"], "已核对原文")
        self.assertEqual(corrected["record"]["confidence"], 0.97)
        self.assertIn("李四被王五刺中", corrected["record"]["original_evidence"])
        self.assertEqual(event_row["summary"], "王五刺伤李四并揭开旧案")
        self.assertIn("旧案由此揭开", event_row["excerpt"])
        self.assertTrue(rejected["cleanup"]["deleted_record"])
        self.assertTrue(rejected["cleanup"]["deleted_parent"])
        self.assertEqual(relation_count, 0)
        self.assertEqual(relation_mention_count, 0)
        self.assertEqual(resurrected_count, 0)
        self.assertEqual(stats["pending_total"], 1)
        self.assertEqual(stats["confirmed_total"], 1)
        self.assertEqual(stats["rejected_total"], 1)

    def test_rejecting_audit_for_existing_static_event_does_not_delete_static_index(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = BookRecallStore(str(Path(tempdir) / "dynamic.db"))
            store.initialize()
            store.connection.execute(
                "INSERT INTO books(book_id, title, source_path) VALUES (?, ?, ?)",
                ("book", "测试书", "memory"),
            )
            store.connection.execute(
                """
                INSERT INTO events(event_id, book_id, chapter_number, chapter_title, event_type, summary, excerpt)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("book:event:static", "book", 8, "雨夜", "因果链", "王五刺伤李四", "李四被王五刺中后倒下。"),
            )
            store.connection.commit()
            store.upsert_dynamic_index_records(
                book_id="book",
                event_records=[
                    EventRecord(
                        chapter_number=8,
                        chapter_title="雨夜",
                        event_type="因果链",
                        summary="王五刺伤李四",
                        excerpt="李四被王五刺中后倒下。",
                        entities=["李四", "王五"],
                        confidence=0.9,
                    )
                ],
                source_query="李四怎么受伤的？",
                source_model="test-model",
                quality_gate="grounded_v2",
            )
            audit = store.list_dynamic_index_audit("book")[0]

            reviewed = store.review_dynamic_index_audit(
                book_id="book",
                audit_id=str(audit["audit_id"]),
                action="reject",
                note="拒绝模型写回，不删除静态事件",
                expected_review_version=0,
            )
            event_exists = int(
                store.connection.execute(
                    "SELECT COUNT(*) FROM events WHERE event_id = ?",
                    ("book:event:static",),
                ).fetchone()[0]
            )
            store.close()

        self.assertTrue(reviewed["cleanup"]["protected_existing_record"])
        self.assertFalse(reviewed["cleanup"]["deleted_record"])
        self.assertEqual(event_exists, 1)

    def test_rejecting_orphan_entity_and_dynamic_event_cleans_dependent_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = BookRecallStore(str(Path(tempdir) / "dynamic.db"))
            store.initialize()
            store.connection.execute(
                "INSERT INTO books(book_id, title, source_path) VALUES (?, ?, ?)",
                ("book", "测试书", "memory"),
            )
            store.connection.commit()
            store.upsert_dynamic_index_records(
                book_id="book",
                entity_records=[
                    EntityRecord(
                        name="孤立道具",
                        first_chapter_number=4,
                        aliases=["旧称"],
                        mentions=[
                            EntityMention(
                                entity_name="孤立道具",
                                chapter_number=4,
                                excerpt="孤立道具在石室中一闪而过。",
                                position_in_chapter=10,
                            )
                        ],
                        confidence=0.88,
                    )
                ],
                event_records=[
                    EventRecord(
                        chapter_number=5,
                        chapter_title="石室",
                        event_type="伏笔/回收",
                        summary="孤立道具消失",
                        excerpt="孤立道具随后消失在石室深处。",
                        entities=["孤立道具"],
                        confidence=0.86,
                    )
                ],
                source_query="孤立道具后来怎样？",
                source_model="test-model",
                quality_gate="grounded_v2",
            )
            audits = {row["record_kind"]: row for row in store.list_dynamic_index_audit("book")}
            event_review = store.review_dynamic_index_audit(
                book_id="book",
                audit_id=str(audits["event"]["audit_id"]),
                action="reject",
                note="事件判断不成立",
                expected_review_version=0,
            )
            entity_review = store.review_dynamic_index_audit(
                book_id="book",
                audit_id=str(audits["entity_mention"]["audit_id"]),
                action="reject",
                note="不是值得保留的实体",
                expected_review_version=0,
            )
            remaining = {
                "events": int(store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
                "event_entities": int(store.connection.execute("SELECT COUNT(*) FROM event_entities").fetchone()[0]),
                "entities": int(store.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0]),
                "aliases": int(store.connection.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0]),
            }
            store.close()

        self.assertTrue(event_review["cleanup"]["deleted_record"])
        self.assertTrue(entity_review["cleanup"]["deleted_record"])
        self.assertTrue(entity_review["cleanup"]["deleted_parent"])
        self.assertEqual(remaining, {"events": 0, "event_entities": 0, "entities": 0, "aliases": 0})

    def test_initialize_migrates_legacy_dynamic_audit_review_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = str(Path(tempdir) / "legacy.db")
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE dynamic_index_audit (
                    audit_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    record_kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    source_type TEXT NOT NULL DEFAULT 'dynamic_llm',
                    source_query TEXT NOT NULL DEFAULT '',
                    source_model TEXT NOT NULL DEFAULT '',
                    quality_gate TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()
            connection.close()

            store = BookRecallStore(db_path)
            store.initialize()
            columns = {
                str(row["name"])
                for row in store.connection.execute("PRAGMA table_info(dynamic_index_audit)").fetchall()
            }
            store.close()

        self.assertTrue(
            {"original_evidence", "record_snapshot_json", "review_note", "reviewed_at", "review_version"}.issubset(columns)
        )


if __name__ == "__main__":
    unittest.main()
