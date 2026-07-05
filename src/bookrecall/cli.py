import argparse
from pathlib import Path

from .agent import BookRecallAgent
from .chunking import build_chunk_hierarchy
from .config import DEFAULT_CHUNK_SETTINGS, DEFAULT_EMBEDDING_SETTINGS, DEFAULT_SEARCH_SETTINGS
from .embeddings import (
    EmbeddingRetriever,
    LocalModelError,
    SentenceTransformerEmbedder,
    build_embedding_index,
    configure_local_model_cache,
    default_cache_root,
    default_sentence_transformers_cache_dir,
    default_vector_dir,
    dependency_report,
    get_vector_index_info,
)
from .entity_index import (
    auto_discover_entities,
    auto_discover_themes,
    build_entity_records,
    build_event_records,
    build_relation_records,
    build_theme_records,
    load_entity_lexicon,
    load_theme_lexicon,
)
from .local_llm import LocalChatClient, LocalLLMSettings
from .parser import parse_chapters
from .retrieval import LocalRetriever
from .smart_index import build_smart_relation_event_records, discover_entities_with_llm
from .storage import BookRecallStore
from .web import run_server


def build_index(args: argparse.Namespace) -> None:
    source_path = Path(args.input)
    text = source_path.read_text(encoding=args.encoding)
    chapters = parse_chapters(text)
    title = args.title or source_path.stem

    parent_chunks, child_chunks = build_chunk_hierarchy(args.book_id, chapters, DEFAULT_CHUNK_SETTINGS)
    smart_client = _make_smart_index_client(args)
    entity_names = load_entity_lexicon(args.entities)
    if not entity_names:
        entity_names = auto_discover_entities(text)
    if smart_client is not None:
        entity_names = discover_entities_with_llm(
            chapters,
            smart_client,
            seed_entities=entity_names,
            max_chapters=args.smart_index_max_chapters or 0,
        )
    entity_records = build_entity_records(chapters, entity_names, DEFAULT_CHUNK_SETTINGS)
    theme_names = auto_discover_themes(text, extra_terms=load_theme_lexicon(args.themes))
    theme_records = build_theme_records(chapters, theme_names, DEFAULT_CHUNK_SETTINGS)
    if smart_client is not None:
        relation_records, event_records = build_smart_relation_event_records(
            chapters,
            entity_records,
            DEFAULT_CHUNK_SETTINGS,
            smart_client,
            max_chapters=args.smart_index_max_chapters or 0,
        )
        if not relation_records:
            relation_records = build_relation_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)
        if not event_records:
            event_records = build_event_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)
    else:
        relation_records = build_relation_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)
        event_records = build_event_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)

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
            relation_records=relation_records,
            theme_records=theme_records,
            event_records=event_records,
        )
    finally:
        store.close()

    print(
        f"建索引完成：book_id={args.book_id}，章节数={len(chapters)}，"
        f"parent_chunks={len(parent_chunks)}，child_chunks={len(child_chunks)}，"
        f"实体数={len(entity_records)}，关系数={len(relation_records)}，"
        f"主题数={len(theme_records)}，事件数={len(event_records)}"
    )


def _make_smart_index_client(args: argparse.Namespace) -> LocalChatClient | None:
    if not getattr(args, "smart_index", False):
        return None
    return LocalChatClient(
        LocalLLMSettings(
            model_path=str(getattr(args, "smart_index_model", "") or ""),
            endpoint=str(getattr(args, "smart_index_endpoint", "") or ""),
            n_ctx=int(getattr(args, "smart_index_ctx", 4096) or 4096),
            max_tokens=int(getattr(args, "smart_index_max_tokens", 2048) or 2048),
        )
    )


def ask_question(args: argparse.Namespace) -> None:
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        retriever = _make_retriever(args, store)
        agent = BookRecallAgent(store, retriever=retriever)
        card = agent.ask_card(
            book_id=args.book_id,
            question=args.question,
            user_id=args.user,
            progress_chapter=args.progress,
            session_id=args.session,
        )
    finally:
        store.close()
    if args.format == "json":
        print(agent.render_json(card))
    else:
        print(agent.render_text(card))


def _make_retriever(args: argparse.Namespace, store: BookRecallStore):
    mode = getattr(args, "retriever", "lexical")
    if mode == "lexical":
        return LocalRetriever(store, DEFAULT_SEARCH_SETTINGS)

    index_dir = getattr(args, "vector_dir", None) or default_vector_dir(getattr(args, "db"))
    info = get_vector_index_info(index_dir, getattr(args, "book_id"))
    if info is None:
        if mode == "auto":
            return LocalRetriever(store, DEFAULT_SEARCH_SETTINGS)
        raise LocalModelError("未找到本书的向量索引。请先运行 embed-build，或改用 --retriever lexical。")

    try:
        configure_local_model_cache(default_cache_root(getattr(args, "db")))
        embedder = SentenceTransformerEmbedder(
            info.model_name,
            cache_dir=default_sentence_transformers_cache_dir(getattr(args, "db")),
        )
        return EmbeddingRetriever(
            store,
            DEFAULT_SEARCH_SETTINGS,
            index_dir=index_dir,
            embedder=embedder,
        )
    except LocalModelError:
        if mode == "auto":
            return LocalRetriever(store, DEFAULT_SEARCH_SETTINGS)
        raise


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


def list_themes(args: argparse.Namespace) -> None:
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        rows = store.list_themes_with_aliases(args.book_id)
    finally:
        store.close()
    if not rows:
        print("当前这本书还没有主题索引。")
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
    print(f"- 实体关系：{stats['relations']}")
    print(f"- 主题：{stats['themes']}")
    print(f"- 主题线索记录：{stats['theme_mentions']}")
    print(f"- 事件：{stats['events']}")


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


def show_models(args: argparse.Namespace) -> None:
    report = dependency_report()
    vector_dir = default_vector_dir(args.db)
    cache_dir = default_sentence_transformers_cache_dir(args.db)
    print("本地小模型能力探测：")
    print(f"- numpy：{'可用' if report['numpy'] else '缺失'}")
    print(f"- sentence-transformers：{'可用' if report['sentence_transformers'] else '缺失'}")
    print(f"- torch：{'可用' if report['torch'] else '缺失'}")
    print(f"- faiss：{'可用' if report['faiss'] else '缺失'}（当前实现可用 numpy 精确检索，不强制依赖 faiss）")
    print(f"- langgraph：{'可用' if report['langgraph'] else '缺失'}（可选 Agent 图策略依赖）")
    print(f"- 推荐 embedding 模型：{report['recommended_embedding_model']}")
    print(f"- 默认向量索引目录：{vector_dir}")
    print(f"- model cache: {cache_dir}")
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        books = store.list_books()
    finally:
        store.close()
    for book in books:
        info = get_vector_index_info(vector_dir, book.book_id)
        status = "已构建" if info else "未构建"
        suffix = f"，backend={info.backend}，model={info.model_name}，chunks={info.chunk_count}" if info else ""
        print(f"- {book.book_id}：{status}{suffix}")


def build_embeddings(args: argparse.Namespace) -> None:
    index_dir = args.vector_dir or default_vector_dir(args.db)
    configure_local_model_cache(default_cache_root(args.db))
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        if store.get_book(args.book_id) is None:
            print(f"没有找到 book_id={args.book_id}，请先运行 build。")
            return
        embedder = SentenceTransformerEmbedder(
            args.model,
            cache_dir=default_sentence_transformers_cache_dir(args.db),
        )
        info = build_embedding_index(
            store=store,
            book_id=args.book_id,
            index_dir=index_dir,
            embedder=embedder,
            batch_size=args.batch_size,
            limit_chunks=args.limit_chunks,
        )
    finally:
        store.close()
    print("本地向量索引构建完成：")
    print(f"- book_id：{info.book_id}")
    print(f"- backend：{info.backend}")
    print(f"- model：{info.model_name}")
    print(f"- chunks：{info.chunk_count}")
    print(f"- dimension：{info.dimension}")
    print(f"- path：{info.path}")


def search_embeddings(args: argparse.Namespace) -> None:
    index_dir = args.vector_dir or default_vector_dir(args.db)
    configure_local_model_cache(default_cache_root(args.db))
    store = BookRecallStore(args.db)
    try:
        store.initialize()
        info = get_vector_index_info(index_dir, args.book_id)
        if info is None:
            print("还没有向量索引，请先运行 embed-build。")
            return
        embedder = SentenceTransformerEmbedder(
            info.model_name,
            cache_dir=default_sentence_transformers_cache_dir(args.db),
        )
        retriever = EmbeddingRetriever(store, DEFAULT_SEARCH_SETTINGS, index_dir=index_dir, embedder=embedder)
        hits = retriever.search(args.book_id, args.query, max_chapter=args.progress)
    finally:
        store.close()
    if not hits:
        print("没有找到向量检索命中。")
        return
    for hit in hits:
        print(f"- score={hit.score:.4f} 第 {hit.chapter_number} 章《{hit.chapter_title}》")
        print(f"  {hit.child_text[:160].strip()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BookRecall 阅读记忆助手 MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser_cmd = subparsers.add_parser("build", help="为一本书建立本地索引")
    build_parser_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    build_parser_cmd.add_argument("--input", required=True, help="原始文本路径")
    build_parser_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    build_parser_cmd.add_argument("--title", help="书名，默认使用文件名")
    build_parser_cmd.add_argument("--entities", help="实体词表路径，每行一个实体")
    build_parser_cmd.add_argument("--themes", help="主题词表路径，格式同实体词表；不传时自动发现常见主题词")
    build_parser_cmd.add_argument("--encoding", default="utf-8", help="文本编码，默认 utf-8")
    build_parser_cmd.add_argument("--smart-index", action="store_true", help="启用本地 LLM 智能实体/关系/事件索引")
    build_parser_cmd.add_argument("--smart-index-model", default="", help="Qwen3 4bit GGUF 模型路径")
    build_parser_cmd.add_argument("--smart-index-endpoint", default="", help="OpenAI-compatible 本地服务地址，例如 http://127.0.0.1:8080")
    build_parser_cmd.add_argument("--smart-index-max-chapters", type=int, default=0, help="智能索引最多处理章节数；0 表示全部")
    build_parser_cmd.add_argument("--smart-index-ctx", type=int, default=4096, help="本地 LLM 上下文长度")
    build_parser_cmd.add_argument("--smart-index-max-tokens", type=int, default=2048, help="单次智能索引最大输出 token")
    build_parser_cmd.set_defaults(func=build_index)

    ask_parser_cmd = subparsers.add_parser("ask", help="针对书籍提问")
    ask_parser_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    ask_parser_cmd.add_argument("--question", required=True, help="用户问题")
    ask_parser_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    ask_parser_cmd.add_argument("--user", default="default", help="用户 ID")
    ask_parser_cmd.add_argument("--session", help="会话 ID；同一会话下会复用最近几轮问答上下文")
    ask_parser_cmd.add_argument("--progress", type=int, help="临时覆盖阅读进度章节号")
    ask_parser_cmd.add_argument("--format", choices=("text", "json"), default="text", help="回答输出格式")
    ask_parser_cmd.add_argument("--retriever", choices=("lexical", "embedding", "auto"), default="lexical", help="检索器：默认倒排检索；embedding 需要先构建本地向量索引")
    ask_parser_cmd.add_argument("--vector-dir", help="向量索引目录，默认与数据库同目录下的 vectors")
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

    list_themes_cmd = subparsers.add_parser("list-themes", help="列出一本书的主题线索索引")
    list_themes_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    list_themes_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    list_themes_cmd.set_defaults(func=list_themes)

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

    models_cmd = subparsers.add_parser("models", help="探测本地小模型依赖与向量索引状态")
    models_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    models_cmd.set_defaults(func=show_models)

    embed_build_cmd = subparsers.add_parser("embed-build", help="使用本地 embedding 小模型为已有书籍构建向量索引")
    embed_build_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    embed_build_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    embed_build_cmd.add_argument("--model", default=DEFAULT_EMBEDDING_SETTINGS.model_name, help="sentence-transformers 模型名")
    embed_build_cmd.add_argument("--batch-size", type=int, default=DEFAULT_EMBEDDING_SETTINGS.batch_size, help="embedding 批大小")
    embed_build_cmd.add_argument("--vector-dir", help="向量索引输出目录，默认与数据库同目录下的 vectors")
    embed_build_cmd.add_argument("--limit-chunks", type=int, help="仅构建前 N 个 child chunk，用于快速试跑")
    embed_build_cmd.set_defaults(func=build_embeddings)

    embed_search_cmd = subparsers.add_parser("embed-search", help="直接用本地向量索引检索证据片段")
    embed_search_cmd.add_argument("--book-id", required=True, help="书籍唯一 ID")
    embed_search_cmd.add_argument("--query", required=True, help="检索问题")
    embed_search_cmd.add_argument("--db", default=".bookrecall/bookrecall.db", help="SQLite 数据库路径")
    embed_search_cmd.add_argument("--progress", type=int, help="限制最大章节，防止检索越界")
    embed_search_cmd.add_argument("--vector-dir", help="向量索引目录，默认与数据库同目录下的 vectors")
    embed_search_cmd.set_defaults(func=search_embeddings)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except LocalModelError as exc:
        parser.exit(2, f"本地小模型不可用：{exc}\n")


if __name__ == "__main__":
    main()
