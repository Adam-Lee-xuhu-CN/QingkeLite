"""测试历史对话检索功能：关键词匹配 + 向量语义搜索"""
import json
import os
import sys

# 确保可以导入项目模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.history_retriever import HistoryRetriever, SimpleTokenizer, TFIDFVectorizer, cosine_similarity


def test_tokenizer():
    """测试分词器"""
    text = "帮我查一下D盘有哪些目录，之前也问过类似的问题"
    tokens = SimpleTokenizer.tokenize(text)
    print(f"分词结果: {tokens[:20]}...")
    assert len(tokens) > 0
    assert "盘" in tokens  # 中文字
    assert "d" in tokens   # 英文字母
    print("  ✓ 分词器正常")


def test_tfidf():
    """测试 TF-IDF 向量化"""
    docs = [
        "帮我查一下D盘有哪些目录",
        "D盘根目录下有Documents和Downloads",
        "今天天气真好",
        "帮我创建一个Python项目",
    ]
    vectorizer = TFIDFVectorizer()
    vectorizer.fit(docs)

    vec = vectorizer.transform("帮我查一下D盘")
    assert len(vec) == len(vectorizer.vocab)
    norm = sum(v * v for v in vec) ** 0.5
    assert abs(norm - 1.0) < 0.01  # L2归一化
    print(f"  词汇表大小: {len(vectorizer.vocab)}")
    print(f"  向量维度: {len(vec)}")
    print("  ✓ TF-IDF 向量化正常")


def test_cosine_similarity():
    """测试余弦相似度"""
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [1.0, 0.0, 0.0]
    vec_c = [0.0, 1.0, 0.0]
    assert abs(cosine_similarity(vec_a, vec_b) - 1.0) < 0.01
    assert abs(cosine_similarity(vec_a, vec_c)) < 0.01
    print("  ✓ 余弦相似度正常")


def test_history_retriever():
    """测试历史对话检索器"""
    # 模拟历史对话
    conversations = [
        {"role": "user", "content": "帮我查一下D盘有哪些目录"},
        {"role": "assistant", "content": "D盘根目录下有Documents、Downloads、Projects等目录"},
        {"role": "user", "content": "今天天气怎么样"},
        {"role": "assistant", "content": "今天天气晴朗，适合外出"},
        {"role": "user", "content": "帮我创建一个Python项目，名字叫my_app"},
        {"role": "assistant", "content": "已创建Python项目my_app，包含基本的目录结构"},
        {"role": "user", "content": "D盘的Projects目录里有什么"},
        {"role": "assistant", "content": "Projects目录里有3个子项目：web_app、data_analysis、cli_tool"},
        {"role": "user", "content": "帮我看看之前的Python项目结构"},
        {"role": "assistant", "content": "my_app项目包含src、tests、config等目录"},
    ]

    retriever = HistoryRetriever(max_results=3)
    retriever.build_index(conversations)

    # 测试1：关键词匹配
    print("\n  测试1: 关键词检索 'D盘 目录'")
    results = retriever.search("D盘有哪些目录", keywords=["D盘", "目录"])
    for r in results:
        msgs = r["messages"]
        first_msg = msgs[0]["content"] if msgs else ""
        print(f"    得分: {r['score']:.4f} | 关键词: {r['keyword_score']:.4f} | 向量: {r['vector_score']:.4f}")
        print(f"    内容: {first_msg[:60]}...")
    assert len(results) > 0
    # 第一条应该是关于D盘目录的
    assert "D盘" in results[0]["messages"][0]["content"]
    print("    ✓ 关键词检索正确")

    # 测试2：向量语义搜索（无关键词命中，但语义相关）
    print("\n  测试2: 语义检索 'D盘里面有什么文件夹'")
    results2 = retriever.search("D盘里面有什么文件夹", keywords=[])
    for r in results2:
        first_msg = r["messages"][0]["content"] if r["messages"] else ""
        print(f"    得分: {r['score']:.4f} | 向量: {r['vector_score']:.4f}")
        print(f"    内容: {first_msg[:60]}...")
    assert len(results2) > 0
    print("    ✓ 语义检索正确")

    # 测试3：不相关内容应该得分低
    print("\n  测试3: 不相关查询 '今天天气'")
    results3 = retriever.search("今天天气怎么样", keywords=["天气"])
    for r in results3:
        first_msg = r["messages"][0]["content"] if r["messages"] else ""
        print(f"    得分: {r['score']:.4f} | 内容: {first_msg[:40]}...")
    assert len(results3) > 0
    assert "天气" in results3[0]["messages"][0]["content"]
    print("    ✓ 不相关查询正确排序")


def test_context_manager_integration():
    """测试 ContextManager 集成检索功能"""
    from core.context_manager import ContextManager

    config = {
        "system_prompt_file": "config/sys_prompt.md",
        "history_rounds": 2,
        "keyword_dict_file": "data/dictionary/keywords.json",
        "max_snippet_length": 2000,
        "max_tokens": 8000,
        "max_retrieved_results": 3,
        "session_dir": "data/sessions",
    }

    mgr = ContextManager(config)

    # 创建测试会话
    session_id = "test_retrieval"
    test_conversations = [
        ("帮我查一下D盘有哪些目录", "D盘根目录下有Documents、Downloads、Projects等目录"),
        ("今天天气怎么样", "今天天气晴朗，适合外出"),
        ("帮我创建一个Python项目", "已创建Python项目，包含基本的目录结构"),
        ("D盘的Projects目录里有什么", "Projects目录里有3个子项目：web_app、data_analysis、cli_tool"),
    ]

    for user_msg, ai_msg in test_conversations:
        mgr.save_conversation(session_id, user_msg, ai_msg)

    # 构建上下文，查询与早期对话相关的内容
    ctx = mgr.build_context("D盘里面有什么文件夹", session_id)

    print(f"\n  最近历史: {len(ctx.history)} 条")
    print(f"  检索片段: {len(ctx.matched_snippets)} 条")
    print(f"  上下文消息: {len(ctx.summarized_context)} 条")
    print(f"  Token估算: {ctx.total_tokens}")

    # 验证最近历史只包含最后2轮
    assert len(ctx.history) == 4  # 2轮 × 2条
    # 验证检索到了相关历史
    assert len(ctx.matched_snippets) > 0

    # 清理测试数据
    test_file = os.path.join(config["session_dir"], f"{session_id}.json")
    if os.path.exists(test_file):
        os.remove(test_file)

    print("  ✓ ContextManager 集成正常")


if __name__ == "__main__":
    print("=" * 60)
    print("历史对话检索功能测试")
    print("=" * 60)

    print("\n[1] 分词器测试")
    test_tokenizer()

    print("\n[2] TF-IDF 向量化测试")
    test_tfidf()

    print("\n[3] 余弦相似度测试")
    test_cosine_similarity()

    print("\n[4] 历史检索器测试")
    test_history_retriever()

    print("\n[5] ContextManager 集成测试")
    test_context_manager_integration()

    print("\n" + "=" * 60)
    print("所有测试通过!")
    print("=" * 60)
