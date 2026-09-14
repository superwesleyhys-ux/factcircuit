#!/usr/bin/env python3
"""旧版 API 报告渲染；由 main.py --tunnel api 显式加载。"""
from __future__ import annotations


from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from agent.core import NewsTracingAgent
from agent.llm_client import LLMClient
from agent.models import CredibilityBreakdown, EventNode, TimelineEvent, TracingReport


console = Console()


# ── 报告渲染 ───────────────────────────────────────────────


def render_report(report: TracingReport) -> None:
    console.print()

    # 1) 直答 — 最醒目的核心输出
    if report.direct_response:
        console.print(Panel(
            report.direct_response,
            title="[bold]直答[/bold]",
            border_style="bold bright_white on blue",
            padding=(1, 2),
        ))

    # 2) 可信度指标 — 透明原始维度，不是单一分数
    _render_credibility(report.credibility)

    # 3) 事件时间线
    if report.event_timeline:
        _render_event_timeline(report.event_timeline)

    # 4) 因果事件树
    console.print()
    tree = Tree(
        f"[bold]{report.event.title}[/bold] ({report.event.date})",
        guide_style="cyan",
    )
    _build_rich_tree(tree, report.event)
    console.print(Panel(tree, title="因果事件树"))

    # 5) 事实核查表
    if report.consistent_facts or report.disputed_facts:
        console.print()
        fact_table = Table(title="事实核查", show_lines=True)
        fact_table.add_column("状态", justify="center", no_wrap=True)
        fact_table.add_column("事实声明")
        fact_table.add_column("来源数", justify="center")

        for f in report.consistent_facts:
            fact_table.add_row(
                "[green]一致[/green]",
                f.claim,
                str(len(f.source_urls)),
            )
        for f in report.disputed_facts:
            fact_table.add_row(
                "[red]分歧[/red]",
                f.claim,
                str(len(f.source_urls)),
            )
        console.print(fact_table)

    # 6) 信源一览
    if report.source_timeline:
        console.print()
        src_table = Table(
            title=f"信源一览 ({len(report.source_timeline)} 个来源)",
            show_lines=True,
        )
        src_table.add_column("时间", style="cyan", no_wrap=True)
        src_table.add_column("来源", style="bold")
        src_table.add_column("类型", style="dim")
        src_table.add_column("首发", justify="center")
        src_table.add_column("链接", style="dim", max_width=50)

        for s in report.source_timeline:
            src_table.add_row(
                s.publish_time or "—",
                s.outlet or "—",
                s.source_type or "—",
                "★" if s.is_original else "",
                s.url if s.url else "—",
            )
        console.print(src_table)

    # 7) 关键发现 / 信息缺口 / 立场标注
    if report.causal_summary:
        console.print(
            Panel(report.causal_summary, title="因果链综述", border_style="blue")
        )
    if report.key_findings:
        findings = "\n".join(f"  * {f}" for f in report.key_findings)
        console.print(Panel(findings, title="关键发现", border_style="green"))
    if report.information_gaps:
        gaps = "\n".join(f"  ! {g}" for g in report.information_gaps)
        console.print(Panel(gaps, title="信息缺口", border_style="yellow"))
    if report.bias_notes:
        notes = "\n".join(f"  > {n}" for n in report.bias_notes)
        console.print(Panel(notes, title="立场/偏见标注", border_style="red"))


def _render_credibility(cred: CredibilityBreakdown) -> None:
    gr_pct = f"{cred.grounding_rate:.0%}" if cred.total_causal_nodes else "N/A"
    types_str = "、".join(cred.source_types) if cred.source_types else "—"
    line = (
        f"[bold]{cred.source_count}[/bold] 个信源  [dim]|[/dim]  "
        f"[bold]{len(cred.source_types)}[/bold] 类媒体 [dim]({types_str})[/dim]  "
        f"[dim]|[/dim]  "
        f"[green]{cred.consistent_count}[/green] 条一致 / "
        f"[red]{cred.disputed_count}[/red] 条争议  [dim]|[/dim]  "
        f"因果锚定 [bold]{cred.grounded_count}[/bold]/"
        f"[bold]{cred.total_causal_nodes}[/bold] "
        f"[dim]({gr_pct})[/dim]"
    )
    console.print(Panel(line, title="可信度指标", expand=False, border_style="dim"))


def _render_event_timeline(events: list[TimelineEvent]) -> None:
    console.print()
    table = Table(
        title=f"事件时间线 ({len(events)} 个事件)",
        show_lines=True,
    )
    table.add_column("日期", style="cyan", no_wrap=True)
    table.add_column("事件", max_width=40)
    table.add_column("详情", max_width=50)
    table.add_column("级别", justify="center", no_wrap=True)
    table.add_column("佐证", justify="center", no_wrap=True)
    table.add_column("多方视角", max_width=45)

    sig_style = {"重大": "bold red", "重要": "bold yellow", "背景": "dim"}

    for ev in events:
        style = sig_style.get(ev.significance, "")
        title = f"[{style}]{ev.title}[/{style}]" if style else ev.title
        verified_mark = "[green]V[/green]" if ev.verified else "[dim]-[/dim]"

        perspectives = ""
        if ev.perspectives:
            parts = []
            for p in ev.perspectives[:2]:
                src = p.get("source", "?")
                framing = p.get("framing", "")
                if len(framing) > 30:
                    framing = framing[:28] + "…"
                parts.append(f"[dim]{src}:[/dim] {framing}")
            perspectives = "\n".join(parts)

        desc = ev.description
        if len(desc) > 60:
            desc = desc[:58] + "…"

        table.add_row(
            ev.date or "—",
            title,
            desc,
            f"[{style}]{ev.significance}[/{style}]" if style else ev.significance,
            verified_mark,
            perspectives or "—",
        )

    console.print(table)


def _build_rich_tree(parent: Tree, node: EventNode) -> None:
    for child in node.causes:
        rel_tag = f" [dim][{child.relation}][/dim]" if child.relation else ""
        conf = f" [dim]({child.confidence:.0%})[/dim]"
        grounded_tag = " [green][有据][/green]" if child.grounded else " [yellow][推测][/yellow]"
        branch = parent.add(
            f"[bold]{child.title}[/bold] ({child.date}){rel_tag}{conf}{grounded_tag}"
        )
        if child.summary:
            branch.add(f"[italic]{child.summary}[/italic]")
        if child.sources:
            src_text = ", ".join(
                s.get("title", s.get("url", "?")) for s in child.sources[:3]
            )
            branch.add(f"[dim]来源: {src_text}[/dim]")
        _build_rich_tree(branch, child)


async def run_api(news_input: str, *, depth: int = 3, model: str | None = None) -> None:
    """Run the original API workflow after explicit selection by main.py."""
    llm = LLMClient(model=model)
    agent = NewsTracingAgent(llm=llm, max_depth=depth, console=console)
    report = await agent.run(news_input)
    render_report(report)
