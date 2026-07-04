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
    model_name: str = "BAAI/bge-small-zh-v1.5"
    batch_size: int = 64
    vector_dir_name: str = "vectors"


DEFAULT_CHUNK_SETTINGS = ChunkSettings()
DEFAULT_SEARCH_SETTINGS = SearchSettings()
DEFAULT_EMBEDDING_SETTINGS = EmbeddingSettings()
