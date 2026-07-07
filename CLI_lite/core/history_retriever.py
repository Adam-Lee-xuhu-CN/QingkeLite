"""轻量级向量检索模块 - 基于TF-IDF + 余弦相似度，无需外部ML依赖"""
import math
import re
from collections import Counter
from typing import Optional


class SimpleTokenizer:
    """简单的中文/英文分词器"""

    # 中文单字切分 + 英文单词切分
    _chinese_re = re.compile(r'[\u4e00-\u9fff]')
    _english_re = re.compile(r'[a-zA-Z]+')

    @classmethod
    def tokenize(cls, text: str) -> list[str]:
        """分词：中文按字切分，英文按词切分"""
        tokens = []
        # 提取所有中文字符
        tokens.extend(cls._chinese_re.findall(text))
        # 提取所有英文单词（转小写）
        tokens.extend(w.lower() for w in cls._english_re.findall(text))
        return tokens


class TFIDFVectorizer:
    """TF-IDF 向量化器"""

    def __init__(self):
        self.vocab: dict[str, int] = {}  # word -> index
        self.idf: list[float] = []       # 每个词的 IDF 值
        self._fitted = False

    def fit(self, documents: list[str]):
        """从文档列表构建词汇表和 IDF"""
        doc_count = len(documents)
        if doc_count == 0:
            return

        # 统计每个词出现在多少文档中
        doc_freq: Counter = Counter()
        all_tokens: set[str] = set()

        for doc in documents:
            tokens = set(SimpleTokenizer.tokenize(doc))
            all_tokens.update(tokens)
            for token in tokens:
                doc_freq[token] += 1

        # 构建词汇表
        self.vocab = {word: idx for idx, word in enumerate(sorted(all_tokens))}

        # 计算 IDF: log((N + 1) / (df + 1)) + 1
        vocab_size = len(self.vocab)
        self.idf = [0.0] * vocab_size
        for word, idx in self.vocab.items():
            df = doc_freq.get(word, 0)
            self.idf[idx] = math.log((doc_count + 1) / (df + 1)) + 1

        self._fitted = True

    def transform(self, text: str) -> list[float]:
        """将文本转换为 TF-IDF 向量"""
        if not self._fitted:
            return []

        vocab_size = len(self.vocab)
        vector = [0.0] * vocab_size

        tokens = SimpleTokenizer.tokenize(text)
        if not tokens:
            return vector

        # 计算 TF（词频归一化）
        tf_counter = Counter(tokens)
        max_tf = max(tf_counter.values()) if tf_counter else 1

        for token, count in tf_counter.items():
            if token in self.vocab:
                idx = self.vocab[token]
                # 归一化 TF: 0.5 + 0.5 * (count / max_tf)
                vector[idx] = (0.5 + 0.5 * count / max_tf) * self.idf[idx]

        # L2 归一化
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]

        return vector


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """计算两个向量的余弦相似度"""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class HistoryRetriever:
    """历史对话检索器 - 关键词匹配 + 向量语义搜索"""

    def __init__(self, max_results: int = 5, keyword_weight: float = 0.4, vector_weight: float = 0.6):
        self.max_results = max_results
        self.keyword_weight = keyword_weight
        self.vector_weight = vector_weight
        self.vectorizer = TFIDFVectorizer()
        self._corpus: list[str] = []      # 文档语料（每轮对话合并文本）
        self._messages: list[dict] = []   # 原始消息列表
        self._vectors: list[list[float]] = []  # 预计算的向量

    def build_index(self, conversations: list[dict]):
        """构建检索索引"""
        self._messages = conversations
        self._corpus = []

        # 将每对 user+assistant 合并为一个文档
        i = 0
        while i < len(conversations):
            msg = conversations[i]
            if msg.get("role") == "user":
                # 合并当前 user 和下一条 assistant
                text = msg.get("content", "")
                if i + 1 < len(conversations) and conversations[i + 1].get("role") == "assistant":
                    text += " " + conversations[i + 1].get("content", "")
                    i += 2
                else:
                    i += 1
                self._corpus.append(text)
            else:
                i += 1

        # 构建 TF-IDF 索引
        if self._corpus:
            try:
                self.vectorizer.fit(self._corpus)
                self._vectors = [self.vectorizer.transform(doc) for doc in self._corpus]
            except Exception as e:
                # 向量化失败时清空索引，避免后续搜索出错
                self._corpus = []
                self._vectors = []

    def search(self, query: str, keywords: list[str] = None) -> list[dict]:
        """
        混合检索：关键词匹配 + 向量语义搜索
        返回最相关的历史对话轮次
        """
        if not self._corpus:
            return []

        scores = []
        query_vector = self.vectorizer.transform(query)

        for idx, doc in enumerate(self._corpus):
            # 1. 关键词匹配得分
            kw_score = 0.0
            if keywords:
                doc_lower = doc.lower()
                matched = sum(1 for kw in keywords if kw.lower() in doc_lower)
                kw_score = matched / len(keywords) if keywords else 0.0

            # 2. 向量语义相似度
            vec_score = cosine_similarity(query_vector, self._vectors[idx]) if query_vector else 0.0

            # 3. 混合得分
            combined_score = (
                self.keyword_weight * kw_score +
                self.vector_weight * vec_score
            )

            scores.append((idx, combined_score, kw_score, vec_score))

        # 按得分降序排序
        scores.sort(key=lambda x: x[1], reverse=True)

        # 取 top-K，恢复为原始消息格式
        results = []
        for idx, combined, kw_s, vec_s in scores[:self.max_results]:
            # 找到该文档对应的原始消息（user + assistant）
            # 重新计算消息索引
            msg_start = 0
            doc_idx = 0
            i = 0
            while i < len(self._messages):
                if self._messages[i].get("role") == "user":
                    if doc_idx == idx:
                        msg_start = i
                        break
                    doc_idx += 1
                    # 跳过 assistant
                    if i + 1 < len(self._messages) and self._messages[i + 1].get("role") == "assistant":
                        i += 2
                    else:
                        i += 1
                else:
                    i += 1

            # 提取 user + assistant 消息对
            pair = []
            if msg_start < len(self._messages):
                pair.append(self._messages[msg_start])
                if msg_start + 1 < len(self._messages) and self._messages[msg_start + 1].get("role") == "assistant":
                    pair.append(self._messages[msg_start + 1])

            results.append({
                "source": "retrieved_history",
                "messages": pair,
                "score": round(combined, 4),
                "keyword_score": round(kw_s, 4),
                "vector_score": round(vec_s, 4),
            })

        return results
