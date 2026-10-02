# core/vectorstore.py
# -*- coding: utf-8 -*-
"""向量库操作：FAISS + sqlite，轻量、稳定、不落盘坑。

设计：
  - FAISS 索引文件：vectorstore/vectors.faiss
  - sqlite 元数据：vectorstore/meta.db
      segments(id INTEGER PRIMARY KEY, chapter INTEGER, seg_index INTEGER, text TEXT)
      meta(key TEXT PRIMARY KEY, value TEXT)
  - FAISS 下标 i 对应 segments 表 id=i+1（一一对应，便于按章删除后重建）

为什么不用 chroma：
  chromadb 1.0 的 Rust 后端 hnsw 索引由后台 compactor 异步刷盘，
  Python 进程退出时常出现"索引中间态"，跨进程加载直接报错
  （Error loading hnsw index）。FAISS 文件采用原子替换，且启动时会校验
  FAISS 与 SQLite 的段落数是否一致；1.4 万段 brute-force 也才几十毫秒。
"""
import logging
import os
import re
import sqlite3

import faiss
import numpy as np

from core.text_utils import split_sentences

SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    id          INTEGER PRIMARY KEY,
    chapter     INTEGER NOT NULL DEFAULT 0,
    seg_index   INTEGER NOT NULL DEFAULT 0,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_chapter ON segments(chapter);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text,
    content='segments',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
    INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE OF text ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
    INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
END;
"""

VECTOR_FILE = "vectors.faiss"
DB_FILE = "meta.db"


def get_vectorstore_dir(filepath: str) -> str:
    """取工程目录下的 vectorstore 路径。"""
    return os.path.join(filepath, "vectorstore")


def _paths(filepath: str):
    store_dir = get_vectorstore_dir(filepath)
    return store_dir, os.path.join(store_dir, DB_FILE), os.path.join(store_dir, VECTOR_FILE)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection, dimension: int) -> None:
    conn.executescript(SCHEMA)
    # meta 里记维度，方便校验
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("dimension", str(dimension)),
    )
    conn.commit()


def _ensure_search_index(conn: sqlite3.Connection) -> None:
    """为旧库补建 FTS5 索引；新库由 triggers 自动保持同步。"""
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT value FROM meta WHERE key='fts_schema_version'").fetchone()
    if not row or row[0] != "1":
        conn.execute("INSERT INTO segments_fts(segments_fts) VALUES ('rebuild')")
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("fts_schema_version", "1"),
        )
        conn.commit()


def _get_dimension(conn: sqlite3.Connection):
    row = conn.execute("SELECT value FROM meta WHERE key='dimension'").fetchone()
    return int(row[0]) if row else None


def vectorstore_health(filepath: str) -> dict:
    """返回 SQLite 与 FAISS 是否一致，避免索引与元数据错位时误检索。"""
    _store_dir, db_path, ix_path = _paths(filepath)
    status = {"healthy": False, "segments": 0, "vectors": 0, "reason": "missing files"}
    if not os.path.exists(db_path) or not os.path.exists(ix_path):
        return status
    try:
        conn = _connect(db_path)
        try:
            dimension = _get_dimension(conn)
            status["segments"] = int(conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0])
        finally:
            conn.close()
        if not dimension:
            status["reason"] = "missing vector dimension"
            return status
        ix = faiss.deserialize_index(np.load(ix_path))
        status["vectors"] = int(ix.ntotal)
        if ix.d != dimension:
            status["reason"] = "vector dimension mismatch"
        elif ix.ntotal != status["segments"]:
            status["reason"] = "segment/vector count mismatch"
        else:
            status.update(healthy=True, reason="ok")
        return status
    except Exception as e:
        status["reason"] = f"unreadable index: {e}"
        return status


def _embed_texts(embedding_adapter, texts):
    """用适配器把文本转成 float32 numpy 数组 (n, dim)。"""
    vecs = embedding_adapter.embed_documents(list(texts))
    if not vecs:
        return np.zeros((0, 0), dtype=np.float32)
    arr = np.asarray(vecs, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    # 归一化 → 内积等价于 cosine similarity
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    return arr.astype(np.float32)


def _embed_query(embedding_adapter, query: str):
    vec = embedding_adapter.embed_query(query)
    arr = np.asarray(vec, dtype=np.float32).reshape(1, -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    return arr.astype(np.float32)


def _faiss_index(dimension: int):
    """返回一个 IndexFlatIP（内积 = 余弦相似度，因为向量已归一化）。"""
    return faiss.IndexFlatIP(dimension)


def _load_index(ix_path: str, dimension: int):
    """加载 FAISS 索引；不存在或损坏则返回空索引。

    用 np.load + faiss.deserialize_index，避免 C++ 底层在
    Windows 上对中文路径支持不好的问题。
    """
    if not os.path.exists(ix_path):
        return _faiss_index(dimension)
    try:
        data = np.load(ix_path)
        ix = faiss.deserialize_index(data)
        return ix
    except Exception as e:
        logging.warning("FAISS 索引损坏，将重建空索引: %s", e)
        return _faiss_index(dimension)


def _save_index(ix, ix_path: str) -> None:
    """原子写：序列化成 numpy 数组后存盘，避免中文路径问题。"""
    data = faiss.serialize_index(ix)
    tmp = ix_path + ".tmp.npy"
    np.save(tmp[:-4], data)  # np.save 会自动补 .npy，所以先截掉
    os.replace(tmp, ix_path)


# ----------------------------- 对外 API -----------------------------

def clear_vector_store(filepath: str) -> bool:
    """删除整个向量库目录。"""
    store_dir = get_vectorstore_dir(filepath)
    if not os.path.exists(store_dir):
        logging.info("向量库不存在，无需清空。")
        return False
    try:
        import shutil
        shutil.rmtree(store_dir)
        logging.info("已删除向量库目录: %s", store_dir)
        return True
    except Exception as e:
        logging.error("删除向量库失败: %s", e)
        return False


def split_text_for_vectorstore(chapter_text: str, max_length: int = 500) -> list:
    """按句子聚合成不超过 max_length 的分段。"""
    if not chapter_text.strip():
        return []
    sentences = split_sentences(chapter_text)
    if not sentences:
        return []
    segments = []
    current = []
    current_length = 0
    for sentence in sentences:
        length = len(sentence)
        if current_length + length > max_length:
            if current:
                segments.append(" ".join(current))
            current = [sentence]
            current_length = length
        else:
            current.append(sentence)
            current_length += length
    if current:
        segments.append(" ".join(current))
    return segments


def init_vector_store(embedding_adapter, texts, filepath: str, chapter_number=None):
    """新建向量库并写入 texts；返回 store_dir（成功）或 None（失败）。

    为保持与旧 Chroma 版接口一致，返回值语义仍为"真值 = 成功，假值 = 失败"。
    这里返回 store_dir 字符串（Chrom 版返回 Chroma 对象），调用方只做真假判断。
    """
    store_dir, db_path, ix_path = _paths(filepath)
    os.makedirs(store_dir, exist_ok=True)
    try:
        texts = list(texts)
        if not texts:
            return None
        arr = _embed_texts(embedding_adapter, texts)
        if arr.size == 0:
            return None
        dim = arr.shape[1]
        conn = _connect(db_path)
        try:
            _init_db(conn, dim)
            ch = int(chapter_number) if chapter_number is not None else 0
            for i, t in enumerate(texts):
                conn.execute(
                    "INSERT INTO segments(id, chapter, seg_index, text) VALUES (?,?,?,?)",
                    (i + 1, ch, i, str(t)),
                )
            conn.commit()
        finally:
            conn.close()

        ix = _faiss_index(dim)
        ix.add(arr)
        _save_index(ix, ix_path)
        logging.info("向量库新建并写入 %d 段。", len(texts))
        return store_dir
    except Exception as e:
        logging.warning("初始化向量库失败: %s", e)
        return None


def load_vector_store(embedding_adapter, filepath: str):
    """加载已存在的向量库；不存在或失败返回 None。

    返回值是 store_dir 字符串（调用方只做真假判断 + 传给其他函数）。
    与 Chroma 版不同，这里不返回对象；检索/追加等函数各自直接操作文件。
    """
    store_dir, db_path, ix_path = _paths(filepath)
    if not os.path.isdir(store_dir):
        return None
    if not os.path.exists(db_path) or not os.path.exists(ix_path):
        return None
    try:
        conn = _connect(db_path)
        try:
            dim = _get_dimension(conn)
            _ensure_search_index(conn)
        finally:
            conn.close()
        if dim is None:
            return None
        health = vectorstore_health(filepath)
        if not health["healthy"]:
            logging.warning("向量库不一致，拒绝加载：%s", health["reason"])
            return None
        return store_dir
    except Exception as e:
        logging.warning("加载向量库失败: %s", e)
        return None


def add_segments(embedding_adapter, texts, filepath: str, chapter_number=None) -> int:
    """追加预切好的文本段到向量库，返回新增段数。

    与 ``update_vector_store`` 的区别：本函数直接拿已经切好的 texts，
    不再做句子聚合切段；``update_vector_store`` 接受整章文本，内部会切段。
    """
    return _append(embedding_adapter, texts, filepath, chapter_number)


def _append(embedding_adapter, texts, filepath: str, chapter_number=None) -> int:
    """追加段落到向量库，返回新增段数。"""
    store_dir, db_path, ix_path = _paths(filepath)
    texts = list(texts)
    if not texts:
        return 0
    try:
        arr = _embed_texts(embedding_adapter, texts)
        if arr.size == 0:
            return 0
        dim = arr.shape[1]

        conn = _connect(db_path)
        try:
            db_dim = _get_dimension(conn)
            if db_dim is not None and db_dim != dim:
                logging.warning("向量维度不一致（库=%d，新=%d），跳过追加。", db_dim, dim)
                return 0
            # 找出下一个可用 id
            row = conn.execute("SELECT MAX(id) FROM segments").fetchone()
            next_id = (row[0] or 0) + 1
            ch = int(chapter_number) if chapter_number is not None else 0
            for i, t in enumerate(texts):
                conn.execute(
                    "INSERT INTO segments(id, chapter, seg_index, text) VALUES (?,?,?,?)",
                    (next_id + i, ch, i, str(t)),
                )
            conn.commit()
        finally:
            conn.close()

        ix = _load_index(ix_path, dim)
        ix.add(arr)
        _save_index(ix, ix_path)
        return len(texts)
    except Exception as e:
        logging.warning("追加向量库失败: %s", e)
        return 0


def update_vector_store(embedding_adapter, new_chapter: str, filepath: str,
                         chapter_number: int = None) -> None:
    """把新章节切段后追加进向量库；库不存在则新建。"""
    segments = split_text_for_vectorstore(new_chapter)
    if not segments:
        logging.warning("章节无可入库文本，跳过。")
        return
    store = load_vector_store(embedding_adapter, filepath)
    if not store:
        if not init_vector_store(embedding_adapter, segments, filepath, chapter_number):
            logging.warning("新建向量库失败，跳过。")
        return
    added = _append(embedding_adapter, segments, filepath, chapter_number)
    if added:
        logging.info("向量库已追加 %d 个分段。", added)


def delete_chapter_segments(embedding_adapter, chapter_number: int,
                            filepath: str) -> int:
    """按 chapter 删除该章所有分段，返回删除条数。"""
    if chapter_number is None:
        return 0
    store_dir, db_path, ix_path = _paths(filepath)
    if not os.path.exists(db_path):
        return 0
    try:
        conn = _connect(db_path)
        try:
            # 先查出要删的 id
            rows = conn.execute(
                "SELECT id FROM segments WHERE chapter=? ORDER BY id",
                (int(chapter_number),),
            ).fetchall()
            if not rows:
                return 0
            ids_to_remove = [r[0] for r in rows]

            # 把剩余向量抽出来重建 FAISS（保持 id 连续，便于一一对应）
            remaining = conn.execute(
                "SELECT id, chapter, seg_index, text FROM segments WHERE chapter!=? ORDER BY id",
                (int(chapter_number),),
            ).fetchall()

            # 重建 FAISS（如果还剩）
            dim = _get_dimension(conn)
            if dim is None:
                return 0
            ix = _load_index(ix_path, dim)

            if remaining:
                # 把全部向量读出来，按 id 顺序取剩余的
                all_vecs = ix.reconstruct_n(0, ix.ntotal)
                keep_mask = np.ones(ix.ntotal, dtype=bool)
                for sid in ids_to_remove:
                    if 1 <= sid <= ix.ntotal:
                        keep_mask[sid - 1] = False
                new_vecs = all_vecs[keep_mask]
                new_ix = _faiss_index(dim)
                if new_vecs.shape[0] > 0:
                    new_ix.add(new_vecs)
                _save_index(new_ix, ix_path)

                # 重建 segments 表，id 重新连续编号
                conn.execute("DELETE FROM segments")
                for new_id, row in enumerate(remaining, 1):
                    conn.execute(
                        "INSERT INTO segments(id, chapter, seg_index, text) VALUES (?,?,?,?)",
                        (new_id, row[1], row[2], row[3]),
                    )
            else:
                # 删光了
                _save_index(_faiss_index(dim), ix_path)
                conn.execute("DELETE FROM segments")

            conn.commit()
            return len(ids_to_remove)
        finally:
            conn.close()
    except Exception as e:
        logging.warning("按章删除分段失败: %s", e)
        return 0


def replace_chapter_segments(embedding_adapter, chapter_text: str,
                             chapter_number: int, filepath: str) -> int:
    """把指定章节的向量整章替换：先删旧段，再写新段。返回新段数。"""
    segments = split_text_for_vectorstore(chapter_text)
    # 先删（无论新内容是否为空，都要清掉旧段）
    delete_chapter_segments(embedding_adapter, chapter_number, filepath)
    if not segments:
        return 0
    # 追加新段
    store = load_vector_store(embedding_adapter, filepath)
    if not store:
        result = init_vector_store(embedding_adapter, segments, filepath, chapter_number)
        return len(segments) if result else 0
    added = _append(embedding_adapter, segments, filepath, chapter_number)
    if added:
        logging.info("chapter=%s 已替换为 %d 段。", chapter_number, added)
    return added


def get_relevant_context_from_vector_store(embedding_adapter, query: str,
                                           filepath: str, k: int = 2) -> str:
    """检索与 query 最相关的 k 段文本并拼接（最多 2000 字）。"""
    store_dir, db_path, ix_path = _paths(filepath)
    if not os.path.exists(ix_path) or not os.path.exists(db_path):
        return ""
    try:
        qvec = _embed_query(embedding_adapter, query)
        ix = faiss.deserialize_index(np.load(ix_path))
        if ix.ntotal == 0:
            return ""
        k_actual = min(k, ix.ntotal)
        _scores, indices = ix.search(qvec, k_actual)
        # indices 是 FAISS 下标（0-based），对应 segments.id = index+1
        ids = [int(i) + 1 for i in indices[0] if i >= 0]
        if not ids:
            return ""
        conn = _connect(db_path)
        try:
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT id, text FROM segments WHERE id IN ({placeholders})", ids,
            ).fetchall()
            text_map = {r[0]: r[1] for r in rows}
        finally:
            conn.close()
        return "\n".join(text_map[i] for i in ids if i in text_map)[:2000]
    except Exception as e:
        logging.warning("相似度检索失败: %s", e)
        return ""


def _normalized_terms(terms) -> list[str]:
    """去掉空值与重复项，同时保留调用方给出的词序。"""
    result = []
    for term in terms or []:
        value = str(term or "").strip()
        if value and value not in result:
            result.append(value)
    return result


_ENGLISH_STOPWORDS = {
    "about", "after", "again", "against", "and", "are", "but", "for", "from",
    "has", "have", "her", "him", "his", "into", "its", "not", "of", "on", "or",
    "she", "that", "the", "their", "them", "they", "this", "was", "were", "what",
    "when", "where", "which", "who", "with", "would", "you",
}


def glossary_terms_for_query(query: str, glossary: dict | None, *, source_query: str = "",
                             corpus_is_english: bool = True,
                             hard_terms=None, max_required_terms: int = 2) -> tuple[list[str], list[str]]:
    """从术语表识别实体，返回 ``(required_terms, optional_terms)``。

    标记为核心的实体才是硬约束；其他命中术语作为 FTS5 关键词偏好。
    ``source_query`` 保留翻译前中文，确保翻译服务偶尔改写专名时仍能识别实体。
    """
    if not isinstance(glossary, dict):
        glossary = {}
    source = source_query or query or ""
    required, matched_terms, entity_words = [], [], set()
    hard_candidates = []
    hard_terms = {str(term).strip() for term in (hard_terms or [])}
    query_lower = (query or "").lower()
    for zh, en in glossary.items():
        zh, en = str(zh or "").strip(), str(en or "").strip()
        if not zh or not en:
            continue
        if zh in source or en.lower() in query_lower:
            term = en if corpus_is_english else zh
            if zh in hard_terms:
                # 只将当前幕中最先出现的少数实体作为 AND 约束，避免无关共现片段挤掉语义结果。
                source_pos = source.find(zh)
                if source_pos < 0:
                    source_pos = len(source) + query_lower.find(en.lower())
                if term not in {item[2] for item in hard_candidates}:
                    hard_candidates.append((source_pos, len(hard_candidates), term))
            if term not in matched_terms:
                matched_terms.append(term)
            entity_words.update(re.findall(r"[a-z]+", en.lower()))
    hard_candidates.sort(key=lambda item: (item[0], item[1]))
    required = [item[2] for item in hard_candidates[:max(0, int(max_required_terms))]]
    if not corpus_is_english:
        return required, [term for term in matched_terms if term not in required]
    optional = [
        word for word in re.findall(r"[a-z]{3,}", query_lower)
        if word not in _ENGLISH_STOPWORDS and word not in entity_words
    ]
    return required, _normalized_terms(
        [term for term in matched_terms if term not in required] + optional)


def _fts_or_query(terms: list[str]) -> str:
    """把用户术语转成 FTS5 phrase OR 查询，避免把空格解释为 AND。"""
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _eligible_segment_ids(conn: sqlite3.Connection, required_terms: list[str],
                          chapter_range=None) -> set[int] | None:
    """返回硬过滤后的 ID；无硬约束时返回 None，表示全库可用。"""
    clauses, params = [], []
    if chapter_range is not None:
        start, end = chapter_range
        clauses.append("chapter BETWEEN ? AND ?")
        params.extend((int(start), int(end)))
    for term in required_terms:
        clauses.append("instr(text, ?) > 0")
        params.append(term)
    if not clauses:
        return None
    sql = "SELECT id FROM segments WHERE " + " AND ".join(clauses)
    return {int(row[0]) for row in conn.execute(sql, params)}


def _rank_filtered_vectors(ix, qvec: np.ndarray, allowed_ids: set[int], top_k: int):
    """只重建硬过滤后的向量并计算内积，分块避免大候选集占满内存。"""
    ids = np.asarray(sorted(allowed_ids), dtype=np.int64)
    # 每块只保留局部 top-k；全局 top-k 必然属于某一块的局部 top-k。
    result_ids, result_scores = [], []
    batch_size = 4096
    for start in range(0, len(ids), batch_size):
        batch_ids = ids[start:start + batch_size]
        positions = batch_ids - 1  # segments.id 与 FAISS position 一一对应
        try:
            vectors = ix.reconstruct_batch(positions)
        except AttributeError:  # 兼容少数旧版 FAISS Python binding
            vectors = np.vstack([ix.reconstruct(int(position)) for position in positions])
        scores = vectors @ qvec[0]
        order = np.argsort(-scores, kind="stable")[:top_k]
        result_ids.extend(batch_ids[order].tolist())
        result_scores.extend(scores[order].tolist())
    final_order = np.argsort(-np.asarray(result_scores), kind="stable")[:top_k]
    ranked_ids = [int(result_ids[i]) for i in final_order]
    return ranked_ids, {int(result_ids[i]): float(result_scores[i]) for i in final_order}


def hybrid_search(embedding_adapter, query: str, filepath: str, k: int = 3,
                  required_terms=None, optional_terms=None, chapter_range=None,
                  semantic_weight: float = 0.7, candidate_k: int = 100,
                  rrf_k: int = 30, fallback_to_semantic: bool = True) -> list[dict]:
    """实体优先的关键词/语义混合检索。

    ``chapter_range`` 始终是硬约束。若实体 ``required_terms`` 组合无结果，
    默认回退到该章节范围内的语义检索，以适应小说中的代词和跨段指代。
    ``optional_terms`` 只参与 FTS5/BM25 召回。返回值含各路分数和命中词。
    """
    _store_dir, db_path, ix_path = _paths(filepath)
    required_terms = _normalized_terms(required_terms)
    optional_terms = _normalized_terms(optional_terms)
    if not query or not query.strip() or not os.path.exists(db_path) or not os.path.exists(ix_path):
        return []
    health = vectorstore_health(filepath)
    if not health["healthy"]:
        logging.warning("混合检索跳过：向量库不一致（%s）", health["reason"])
        return []
    semantic_weight = max(0.0, min(1.0, float(semantic_weight)))
    candidate_k = max(int(candidate_k), int(k), 1)
    rrf_k = max(int(rrf_k), 1)
    try:
        conn = _connect(db_path)
        try:
            _ensure_search_index(conn)
            active_required_terms = required_terms
            constraint_mode = "strict" if required_terms else "none"
            allowed_ids = _eligible_segment_ids(conn, active_required_terms, chapter_range)
            if allowed_ids is not None and not allowed_ids:
                # 人物共现是有用的优先信号，却不应令小说中的代词段落无法召回。
                # 章节范围仍保留，不能因实体兜底而跨出用户指定范围。
                if required_terms and fallback_to_semantic:
                    active_required_terms = []
                    constraint_mode = "semantic_fallback"
                    allowed_ids = _eligible_segment_ids(conn, [], chapter_range)
                if allowed_ids is not None and not allowed_ids:
                    return []

            # FTS5 的 bm25 越小越相关。关键词查询不包含 required_terms：
            # 它们已经作为硬约束执行，避免高频角色名主宰排序。
            keyword_ids, keyword_scores = [], {}
            if optional_terms:
                sql = """
                    SELECT s.id, bm25(segments_fts) AS bm25_score
                    FROM segments_fts JOIN segments AS s ON s.id = segments_fts.rowid
                    WHERE segments_fts MATCH ?
                """
                params = [_fts_or_query(optional_terms)]
                if chapter_range is not None:
                    sql += " AND s.chapter BETWEEN ? AND ?"
                    params.extend((int(chapter_range[0]), int(chapter_range[1])))
                for term in active_required_terms:
                    sql += " AND instr(s.text, ?) > 0"
                    params.append(term)
                sql += " ORDER BY bm25_score LIMIT ?"
                rows = conn.execute(sql, (*params, candidate_k)).fetchall()
                keyword_ids = [int(row[0]) for row in rows]
                keyword_scores = {int(row[0]): float(row[1]) for row in rows}

            ix = faiss.deserialize_index(np.load(ix_path))
            qvec = _embed_query(embedding_adapter, query)
            if allowed_ids is not None:
                semantic_ids, semantic_scores = _rank_filtered_vectors(
                    ix, qvec, allowed_ids, candidate_k)
            else:
                scan_k = min(ix.ntotal, candidate_k)
                scores, indices = ix.search(qvec, scan_k)
                semantic_ids = [int(index) + 1 for index in indices[0] if index >= 0]
                semantic_scores = {
                    int(index) + 1: float(score)
                    for score, index in zip(scores[0], indices[0]) if index >= 0
                }

            fused = {}
            for rank, segment_id in enumerate(keyword_ids, 1):
                fused[segment_id] = fused.get(segment_id, 0.0) + (1.0 - semantic_weight) / (rrf_k + rank)
            for rank, segment_id in enumerate(semantic_ids, 1):
                fused[segment_id] = fused.get(segment_id, 0.0) + semantic_weight / (rrf_k + rank)
            if not fused:
                return []
            result_ids = [segment_id for segment_id, _score in sorted(
                fused.items(), key=lambda item: item[1], reverse=True)[:int(k)]]
            placeholders = ",".join("?" * len(result_ids))
            rows = conn.execute(
                f"SELECT id, chapter, seg_index, text FROM segments WHERE id IN ({placeholders})",
                result_ids,
            ).fetchall()
            docs = {int(row[0]): row for row in rows}
            return [
                {
                    "id": segment_id,
                    "chapter": int(docs[segment_id][1]),
                    "seg_index": int(docs[segment_id][2]),
                    "text": docs[segment_id][3],
                    "score": fused[segment_id],
                    "semantic_score": semantic_scores.get(segment_id),
                    "keyword_bm25": keyword_scores.get(segment_id),
                    "constraint_mode": constraint_mode,
                    "matched_terms": [term for term in required_terms + optional_terms
                                      if term.lower() in docs[segment_id][3].lower()],
                }
                for segment_id in result_ids if segment_id in docs
            ]
        finally:
            conn.close()
    except Exception as e:
        logging.warning("混合检索失败: %s", e)
        return []


def count_segments(filepath: str) -> int:
    """读取向量库分段总数（不需要 embedding 适配器）。"""
    store_dir, db_path, _ix_path = _paths(filepath)
    if not os.path.isdir(store_dir):
        return 0
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT COUNT(*) FROM segments").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0


def indexed_chapters(filepath: str) -> list[int]:
    """返回已有向量分段覆盖的正文章号，不需要加载 FAISS。"""
    store_dir, db_path, _ix_path = _paths(filepath)
    if not os.path.isdir(store_dir) or not os.path.isfile(db_path):
        return []
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT DISTINCT chapter FROM segments WHERE chapter > 0 ORDER BY chapter"
            ).fetchall()
            return [int(row[0]) for row in rows]
        finally:
            conn.close()
    except Exception:
        return []
