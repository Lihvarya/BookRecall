import argparse
from pathlib import Path

from .agent import BookRecallAgent
from .chunking import build_chunk_hierarchy
from .config import DEFAULT_CHUNK_SETTINGS
from .entity_index import auto_discover_entities, build_entity_records, load_entity_lexicon
from .parser import parse_chapters
from .storage import BookRecallStore


def build_index(args: argparse.Namespace) -> None:
    source_path = Path(args.input)
    text = source_path.read_text(encoding=args.encoding)
    chapters = parse_chapters(text)
    title = args.title or source_path.stem

    parent_chunks, child_chunks = build_chunk_hierarchy(args.book_id, chapters, DEFAULT_CHUNK_SETTINGS)
    entity_names = load_entity_lexicon(args.entities)
    if not entity_names:
        entity_names = auto_discover_entities(text)
    entity_records = build_entity_records(chapters, entity_names, DEFAULT_CHUNK_SETTINGS)

    store = BookRecallStore(args.db)
    try:
        store.initialize()
        store.replace_book(
            book_id=args.book_id,
            title=title,
            source_path=str(source_path),
            chapters=chapters,
            parent_chunks=parent_chunks,
            child_chunks=child_chunks,
            entity_records=entity_records,
        )
    finally:
        store.close()

    print(
        f"建索引完成：book_id={args.book_id}，章节数={len(chapters)}，"
        f"parent_chunks={len(parent_chunks)}，child_chunks={len(child_chunks)}，"
        f"实体数={len(entity_records)}"
    )


def ask_question(args: argparse.Namespace) -> None:
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        agent = BookRecallAgent(store)
        answer = agent.ask(
            book_id=args.book_id,
            question=args.question,
            user_id=args.user,
            progress_chapter=args.progress,
        )
    finally:
        store.close()
    print(answer)


def set_progress(args: argparse.Namespace) -> None:
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        store.set_progress(args.book_id, args.user, args.chapter)
    finally:
        store.close()
    print(f"已将用户 {args.user} 在 {args.book_id} 的阅读进度设置为第 {args.chapter} 章。")


def show_progress(args: argparse.Namespace) -> None:
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        progress = store.get_progress(args.book_id, args.user)
    finally:
        store.close()
    if progress is None:
        print("当前还没有记录阅读进度。")
        return
    print(f"用户 {args.user} 在 {args.book_id} 的阅读进度：第 {progress} 章。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BookRecall 阅读记忆助手 MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser_cmd = subparsers.add_parser("build", help="为一本书建立本地索引")
    build_parser_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    build_parser_cmd.add_argument("--input", required=True, help="原始文本路径")
    build_parser_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    build_parser_cmd.add_argument("--title", help="书名，默认使用文件名")
    build_parser_cmd.add_argument("--entities", help="实体词表路径，每行一个实体")
    build_parser_cmd.add_argument("--encoding", default="utf-8", help="文本编码，默认 utf-8")
    build_parser_cmd.set_defaults(func=build_index)

    ask_parser_cmd = subparsers.add_parser("ask", help="针对书籍提问")
    ask_parser_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    ask_parser_cmd.add_argument("--question", required=True, help="用户问题")
    ask_parser_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    ask_parser_cmd.add_argument("--user", default="default", help="用户 ID")
    ask_parser_cmd.add_argument("--progress", type=int, help="临时覆盖阅读进度章节号")
    ask_parser_cmd.set_defaults(func=ask_question)

    set_progress_cmd = subparsers.add_parser("set-progress", help="保存阅读进度")
    set_progress_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    set_progress_cmd.add_argument("--user", default="default", help="用户 ID")
    set_progress_cmd.add_argument("--chapter", required=True, type=int, help="已读到第几章")
    set_progress_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    set_progress_cmd.set_defaults(func=set_progress)

    show_progress_cmd = subparsers.add_parser("show-progress", help="查看阅读进度")
    show_progress_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    show_progress_cmd.add_argument("--user", default="default", help="用户 ID")
    show_progress_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    show_progress_cmd.set_defaults(func=show_progress)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

