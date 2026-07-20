from dataclasses import dataclass


@dataclass(slots=True)
class ChunkSettings:
    parent_target_chars: int = 1800
    parent_overlap_chars: int = 200
    child_target_chars: int = 260
    child_overlap_chars: int = 40
    max_excerpt_chars: int = 120


@dataclass(slots=True)
class SearchSettings:
    top_k_children: int = 10
    top_k_parents: int = 4


@dataclass(slots=True)
class EmbeddingSettings:
    model_name: str = "Qwen/Qwen3-Embedding-0.6B"
    batch_size: int = 64
    vector_dir_name: str = "vectors"


@dataclass(slots=True)
class RerankSettings:
    model_name: str = "Qwen/Qwen3-Reranker-0.6B"
    batch_size: int = 1
    candidate_count: int = 6
    max_chars: int = 384
    max_length: int = 512


DEFAULT_CHUNK_SETTINGS = ChunkSettings()
DEFAULT_SEARCH_SETTINGS = SearchSettings()
DEFAULT_EMBEDDING_SETTINGS = EmbeddingSettings()
DEFAULT_RERANK_SETTINGS = RerankSettings()
