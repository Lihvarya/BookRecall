import argparse
from pathlib import Path

from .agent import BookRecallAgent
from .chunking import build_chunk_hierarchy
from .config import DEFAULT_CHUNK_SETTINGS
from .entity_index import auto_discover_entities, build_entity_records, load_entity_lexicon
from .parser import parse_chapters
from .storage import BookRecallStore
from .web import run_server


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
        card = agent.ask_card(
            book_id=args.book_id,
            question=args.question,
            user_id=args.user,
            progress_chapter=args.progress,
        )
    finally:
        store.close()
    if args.format == "json":
        print(agent.render_json(card))
    else:
        print(agent.render_text(card))


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


def list_books(args: argparse.Namespace) -> None:
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        books = store.list_books()
    finally:
        store.close()
    if not books:
        print("当前还没有任何已建索引的书籍。")
        return
    for book in books:
        print(
            f"- {book.book_id} | {book.title} | 章节 {book.chapter_count} | "
            f"实体 {book.entity_count} | {book.source_path}"
        )


def list_entities(args: argparse.Namespace) -> None:
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        rows = store.list_entities_with_aliases(args.book_id)
    finally:
        store.close()
    if not rows:
        print("当前这本书还没有实体索引。")
        return
    for row in rows:
        alias_text = f" | 别名：{row['aliases']}" if row["aliases"] else ""
        print(
            f"- {row['name']} | 首次出现：第 {row['first_chapter_number']} 章 | "
            f"提及次数：{row['mention_count']}{alias_text}"
        )


def serve_web(args: argparse.Namespace) -> None:
    run_server(args.host, args.port, args.db)


def show_stats(args: argparse.Namespace) -> None:
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        if store.get_book(args.book_id) is None:
            print(f"没有找到 book_id={args.book_id}，可用 list-books 查看已有书籍。")
            return
        stats = store.get_stats(args.book_id)
        max_chapter = store.get_max_chapter(args.book_id)
    finally:
        store.close()
    print(f"book_id={args.book_id} 索引规模：")
    print(f"- 章节：{stats['chapters']}（最大章节号 {max_chapter}）")
    print(f"- parent chunks：{stats['parent_chunks']}")
    print(f"- child chunks：{stats['child_chunks']}")
    print(f"- 实体：{stats['entities']}")
    print(f"- 实体出现记录：{stats['entity_mentions']}")


def show_chapters(args: argparse.Namespace) -> None:
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        if store.get_book(args.book_id) is None:
            print(f"没有找到 book_id={args.book_id}，可用 list-books 查看已有书籍。")
            return
        rows = store.get_chapter_titles(args.book_id, limit=args.limit)
    finally:
        store.close()
    if not rows:
        print("这本书还没有章节索引。")
        return
    for row in rows:
        print(f"- 第 {int(row['chapter_number'])} 章 {row['title']}")
    if args.limit and len(rows) >= args.limit:
        print(f"（仅显示前 {args.limit} 章，省略后续）")


def clear_book(args: argparse.Namespace) -> None:
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        info = store.get_book(args.book_id)
        if info is None:
            print(f"没有找到 book_id={args.book_id}，无需清理。")
            return
        if not args.yes:
            print(f"将删除书籍：{info.title}（book_id={info.book_id}，{info.chapter_count} 章，{info.entity_count} 实体）。")
            print("为防止误删，请加 --yes 确认后再执行。")
            return
        removed = store.delete_book(args.book_id)
    finally:
        store.close()
    print(f"已删除 book_id={args.book_id} 的全部索引数据（约 {removed} 条 chunk 记录）。")


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
    ask_parser_cmd.add_argument("--format", choices=("text", "json"), default="text", help="回答输出格式")
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

    list_books_cmd = subparsers.add_parser("list-books", help="列出已建索引的书籍")
    list_books_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    list_books_cmd.set_defaults(func=list_books)

    list_entities_cmd = subparsers.add_parser("list-entities", help="列出一本书的实体索引")
    list_entities_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    list_entities_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    list_entities_cmd.set_defaults(func=list_entities)

    serve_cmd = subparsers.add_parser("serve", help="启动本地 Web 界面")
    serve_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    serve_cmd.add_argument("--host", default="127.0.0.1", help="监听地址")
    serve_cmd.add_argument("--port", default=8000, type=int, help="监听端口")
    serve_cmd.set_defaults(func=serve_web)

    stats_cmd = subparsers.add_parser("stats", help="查看某本书的索引规模")
    stats_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    stats_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    stats_cmd.set_defaults(func=show_stats)

    chapters_cmd = subparsers.add_parser("chapters", help="列出某本书的章节标题，便于核对章节解析")
    chapters_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    chapters_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    chapters_cmd.add_argument("--limit", type=int, default=20, help="只显示前 N 章，默认 20，0 表示全部")
    chapters_cmd.set_defaults(func=show_chapters)

    clear_cmd = subparsers.add_parser("clear", help="删除某本书的全部本地索引（不删数据库本身）")
    clear_cmd.add_argument("--book-id", required=True, help="要清理的书籍唯一 ID")
    clear_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    clear_cmd.add_argument("--yes", action="store_true", help="确认删除（必需，否则只预览）")
    clear_cmd.set_defaults(func=clear_book)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
