from __future__ import annotations

import json
import re

from ...models.schemas import ChatMessage


class PlanMixin:
    @staticmethod
    def _plan_step_instruction(goal: dict, step: dict) -> str:
        goal_text = json.dumps({
            "objective": goal.get("objective"),
            "success_criteria": goal.get("success_criteria") or [],
            "constraints": goal.get("constraints") or [],
            "deliverable": goal.get("deliverable"),
        }, ensure_ascii=False, indent=2)
        base = (
            f"步骤：{step.get('title')}\n"
            f"类型：{step.get('kind')}\n"
            f"职责：{step.get('role')}\n"
            f"说明：{step.get('description')}\n"
            f"Goal Spec：\n{goal_text}\n"
        )
        if step.get("kind") == "worker":
            return (
                f"{base}\n"
                "独立完成你的候选结果，不假设其他 Worker 会弥补遗漏。"
                "只报告实际执行或验证过的事实；明确列出证据、风险与未验证项。"
            )
        if step.get("kind") == "verifier":
            return (
                f"{base}\n"
                "核验后只输出一个 JSON 对象，不要使用 Markdown 代码围栏。格式："
                '{"verdict":"pass|needs_revision|fail",'
                '"summary":"核验摘要","evidence":["可核对证据"],'
                '"issues":["矛盾、缺口或未验证项"]}。'
                "若没有足够证据，不能返回 pass。"
            )
        if step.get("kind") == "synthesizer":
            return (
                f"{base}\n"
                "生成唯一、可直接交付给用户的最终答案。不要展示内部 JSON、Plan Step "
                "编号或模型讨论过程；吸收 Verifier 指出的问题，并明确仍无法验证的部分。"
            )
        return base

    @staticmethod
    def _public_plan_step(step: dict) -> dict:
        return {**step, "output": None}

    @staticmethod
    def _plan_step_context(
        base_context: list[ChatMessage],
        goal: dict,
        step: dict,
        steps_by_key: dict[str, dict],
        steers: list[dict],
        reviews: list[dict],
        workspace_evidence: list[dict] | None = None,
    ) -> list[ChatMessage]:
        messages = [message.model_copy(deep=True) for message in base_context]
        if step.get("kind") == "worker" and int(step.get("attempt") or 0) > 1:
            previous_output = step.get("output")
            previous_content = (
                str(previous_output.get("content") or "")
                if isinstance(previous_output, dict)
                else ""
            )
            latest_review = reviews[-1] if reviews else {}
            issues = latest_review.get("issues")
            issue_lines = [
                str(issue)[:1000]
                for issue in issues[:20]
            ] if isinstance(issues, list) else []
            messages.append(ChatMessage(
                role="user",
                content=(
                    "【Verifier 触发的受控返工】\n"
                    f"上一版候选：\n{previous_content[:40_000] or '[无可复用候选]'}\n\n"
                    f"核验摘要：{str(latest_review.get('summary') or '需要修订')[:10_000]}\n"
                    + (
                        "待修问题：\n"
                        + "\n".join(f"- {issue}" for issue in issue_lines)
                        if issue_lines else "待修问题：核验未提供结构化问题，请重新检查成功条件。"
                    )
                    + "\n请针对问题生成新的完整候选，不要只描述修改过程。"
                ),
            ))
        dependencies = [
            steps_by_key[key]
            for key in step.get("dependencies") or []
            if key in steps_by_key
        ]
        if dependencies:
            blocks: list[str] = []
            remaining = 120_000
            for dependency in dependencies:
                output = dependency.get("output")
                content = (
                    str(output.get("content") or "")
                    if isinstance(output, dict)
                    else ""
                )
                content = content[:40_000]
                block = (
                    f"### {dependency.get('title')} "
                    f"[{dependency.get('kind')} / {dependency.get('status')}]\n"
                    f"模型：{dependency.get('assigned_model') or 'system'}\n"
                    f"{content or '[无可用输出]'}"
                )
                if dependency.get("error"):
                    block += f"\n错误：{dependency['error']}"
                if len(block) > remaining:
                    block = block[:remaining] + "\n...[依赖输出已截断]"
                remaining = max(0, remaining - len(block))
                blocks.append(block)
                if remaining == 0:
                    break
            messages.append(ChatMessage(
                role="user",
                content=(
                    "以下是已经持久化的上游步骤结果。只把其中可核对内容视为证据，"
                    "失败或空输出不得推断为成功：\n\n"
                    + "\n\n".join(blocks)
                ),
            ))
        if (
            workspace_evidence
            and step.get("kind") in {"verifier", "synthesizer"}
        ):
            messages.append(ChatMessage(
                role="user",
                content=(
                    "【不可由模型声明替代的 Git/测试证据】\n"
                    "以下记录由 AetherChat 在独立 worktree 上直接采集。"
                    "exit_code 非 0 不能视为通过；unverified 表示只有 diff、"
                    "没有执行确定性核验命令：\n"
                    + json.dumps(
                        workspace_evidence,
                        ensure_ascii=False,
                        indent=2,
                    )[:80_000]
                ),
            ))
        steer_texts = [
            str(item.get("content") or "").strip()
            for item in steers
            if str(item.get("content") or "").strip()
        ]
        if steer_texts:
            messages.append(ChatMessage(
                role="user",
                content=(
                    "【本 Run 最新转向记录】\n"
                    + "\n".join(f"- {text}" for text in steer_texts[-10:])
                    + "\n最终输出必须服从这些较新的指令。"
                ),
            ))
        return messages

    @staticmethod
    def _parse_plan_review(content: str) -> dict:
        normalized = str(content or "").strip()
        candidate = normalized
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", normalized, re.S)
        if fenced:
            candidate = fenced.group(1)
        else:
            start = normalized.find("{")
            end = normalized.rfind("}")
            if start >= 0 and end > start:
                candidate = normalized[start:end + 1]
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        verdict = str(parsed.get("verdict") or "unknown").lower()
        if verdict not in {"pass", "needs_revision", "fail"}:
            verdict = "unknown"
        evidence = parsed.get("evidence")
        issues = parsed.get("issues")
        return {
            "verdict": verdict,
            "summary": str(parsed.get("summary") or normalized or "无核验摘要")[:20_000],
            "evidence": evidence[:50] if isinstance(evidence, list) else [],
            "issues": issues[:50] if isinstance(issues, list) else [],
        }
