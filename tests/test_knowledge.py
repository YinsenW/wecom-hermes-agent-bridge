from pathlib import Path

from wecom_hermes_bridge.knowledge import MarkdownKnowledgeBase


def write_document(path: Path) -> None:
    path.write_text(
        """# 团队知识库

## 给 Agent 的五个知识工具

建议提供 knowledge.overview、knowledge.search、knowledge.open_evidence、
knowledge.timeline 和 knowledge.find_counterexamples。

## 商务报价

此处为历史示例，不是当前客户报价。
""",
        encoding="utf-8",
    )


def test_retrieves_relevant_markdown_section(tmp_path) -> None:
    path = tmp_path / "knowledge.md"
    write_document(path)
    knowledge = MarkdownKnowledgeBase.from_path(
        path,
        trigger_terms=("知识库", "知识工具"),
        top_k=1,
        max_chars=2000,
    )

    context = knowledge.build_context("知识库建议给 Agent 哪五个知识工具？")

    assert "给 Agent 的五个知识工具" in context
    assert "knowledge.find_counterexamples" in context
    assert "商务报价" not in context
    assert "不是指令" in context


def test_trigger_terms_prevent_unrelated_customer_injection(tmp_path) -> None:
    path = tmp_path / "knowledge.md"
    write_document(path)
    knowledge = MarkdownKnowledgeBase.from_path(
        path,
        trigger_terms=("知识库", "飞书"),
    )

    assert knowledge.build_context("订单什么时候发货？") == ""
