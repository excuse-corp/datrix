"""html_interpreter tool — render HTML as an interactive report."""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from dbgpt.agent.core.action.base import ActionOutput
from dbgpt.agent.resource.tool.base import tool

logger = logging.getLogger(__name__)


async def _build_dashboard_report(report: dict, conv_id: str) -> str:
    from dbgpt._private.config import Config
    from dbgpt.agent.skill.manage import get_skill_manager
    from dbgpt.configs.model_config import PILOT_PATH
    from dbgpt_app.openapi.api_v1.tools.skill_tools import _skill_unavailable_message

    skill_name = "dashboard-data-report"
    manager = get_skill_manager(Config().SYSTEM_APP)
    unavailable = _skill_unavailable_message(
        skill_name, manager._get_skill_path(skill_name)
    )
    if unavailable:
        raise ValueError(unavailable)

    output_dir = Path(PILOT_PATH) / "tmp" / conv_id
    result = await manager.execute_skill_script_file(
        skill_name,
        "build_report.py",
        {"data": report.get("data"), "calcs": report.get("calcs", {})},
        output_dir=str(output_dir),
    )
    chunks = json.loads(result)["chunks"]
    if len(chunks) != 1:
        raise ValueError("\n".join(item["content"] for item in chunks))
    try:
        path = Path(json.loads(chunks[0]["content"])["file_path"]).resolve()
        path.relative_to(output_dir.resolve())
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(chunks[0]["content"]) from exc
    if not path.is_file():
        raise ValueError("报告文件未生成")
    return str(path)


def make_html_interpreter(react_state: Dict[str, Any], skills_dir: str):
    @tool(
        description=(
            "将 HTML 渲染为可交互的网页报告，这是向用户展示网页报告的唯一方式。"
            "【默认用法】直接传入完整的 HTML 字符串："
            '{"html": "<html>...</html>", "title": "报告标题"}。'
            "你需要自己生成完整的 HTML 代码"
            "（包含 <!DOCTYPE html>、<html>、<head>、<body> 等），"
            "然后传给 html 参数即可。"
            "HTML 可以很长，没有长度限制，不需要分段传入。"
            "【技能模式 - 仅在使用技能时可选】"
            "如果正在使用技能（skill），可以用模板模式："
            '{"template_path": "技能名/templates/模板.html", '
            '"data": {"KEY": "值"}, "title": "标题"}。'
            '也可以用文件模式：{"file_path": "/path/to/report.html"}。'
            'dashboard-data-report 技能可使用 {"dashboard_report": {"data": {...}, '
            '"calcs": {...}, "brief_delivery": true}, "title": "标题"}，'
            "一次计算、生成、检查并展示报告。"
        )
    )
    async def html_interpreter(
        html: str = "",
        title: str = "Report",
        file_path: str = "",
        template_path: str = "",
        data: dict | str = None,
        dashboard_report: dict | None = None,
    ) -> str | ActionOutput:
        """Render HTML as an interactive web report."""
        from dbgpt.configs.model_config import STATIC_MESSAGE_IMG_PATH

        skills_path = Path(skills_dir).expanduser().resolve()
        react_state["brief_dashboard_delivery"] = False
        if dashboard_report is not None:
            try:
                if html or file_path or template_path:
                    raise ValueError("dashboard_report 单独使用")
                file_path = await _build_dashboard_report(
                    dashboard_report, react_state.get("conv_id") or "default"
                )
                if not title or title == "Report":
                    title = dashboard_report["data"]["meta"]["title"]
            except (ValueError, TypeError, KeyError, OSError) as exc:
                return ActionOutput(
                    is_exe_success=False,
                    content=f"报告生成失败：{exc}",
                )

        # ── Mode 1: template_path + data ──
        if template_path and template_path.strip():
            tp = template_path.strip()
            target = (skills_path / tp).resolve()
            try:
                target.relative_to(skills_path)
            except ValueError:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Invalid template_path: {tp}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if not target.is_file():
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    f"Template not found: {tp}. "
                                    "Please retry using the `html` parameter "
                                    "directly — "
                                    "generate complete HTML yourself and pass it via "
                                    '{"html": "<html>...</html>", "title": "title"}.'
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            try:
                raw_template = target.read_text(encoding="utf-8")
            except Exception as e:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Error reading template: {e}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

            replacements = data
            if isinstance(replacements, str):
                try:
                    replacements = json.loads(replacements)
                except Exception:
                    try:
                        fixed = str(replacements).rstrip()
                        if not fixed.endswith("}"):
                            fixed += '"}' if not fixed.endswith('"') else "}"
                        replacements = json.loads(fixed)
                    except Exception:
                        replacements = {}
            if not isinstance(replacements, dict):
                replacements = {}

            auto_data = react_state.get("auto_data", {})
            if isinstance(auto_data, dict):
                replacements = {**auto_data, **replacements}

            ratio_data = react_state.get("ratio_data", {})
            if isinstance(ratio_data, dict):
                replacements = {**ratio_data, **replacements}

            image_url_map = react_state.get("image_url_map", {})
            if isinstance(image_url_map, dict):
                for stem, url in image_url_map.items():
                    chart_key = f"CHART_{stem.upper()}"
                    if chart_key not in replacements:
                        replacements[chart_key] = url

            def _replace_placeholder(m):
                key = m.group(1)
                return str(replacements.get(key, ""))

            html = re.sub(r"\{\{([A-Z_0-9]+)\}\}", _replace_placeholder, raw_template)
            if not title or title == "Report":
                title = target.stem

        # ── Mode 2: file_path ──
        elif file_path and file_path.strip():
            fp = file_path.strip()
            if not os.path.isfile(fp):
                cid = react_state.get("conv_id") or "default"
                from dbgpt.configs.model_config import PILOT_PATH

                alt = os.path.join(PILOT_PATH, "data", cid, os.path.basename(fp))
                if os.path.isfile(alt):
                    fp = alt
                else:
                    return json.dumps(
                        {
                            "chunks": [
                                {
                                    "output_type": "text",
                                    "content": f"File not found: {file_path}",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    html = f.read()
                if not title or title == "Report":
                    title = os.path.splitext(os.path.basename(fp))[0]
            except Exception as e:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Error reading file: {e}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        # ── Mode 3: inline html ──
        if html and isinstance(html, str) and not template_path and not file_path:
            if "\\n" in html:
                html = html.replace("\\n", "\n")
            if "\\t" in html:
                html = html.replace("\\t", "\t")

        if not html or not html.strip():
            return json.dumps(
                {
                    "chunks": [
                        {"output_type": "text", "content": "No HTML content provided"}
                    ]
                },
                ensure_ascii=False,
            )

        # Post-process: fix image URLs
        fixed_html = html.strip()
        try:
            IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
            name_to_served: Dict[str, str] = {}
            if os.path.isdir(STATIC_MESSAGE_IMG_PATH):
                for fname in os.listdir(STATIC_MESSAGE_IMG_PATH):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in IMAGE_EXTS:
                        continue
                    m = re.match(r"^[0-9a-f]{8}_(.+)$", fname, re.IGNORECASE)
                    if m:
                        name_to_served[m.group(1).lower()] = f"/images/{fname}"

            if name_to_served:

                def _fix_img_src(match: re.Match) -> str:
                    prefix = match.group(1)
                    raw_path = match.group(2)
                    quote = match.group(3)
                    filename = raw_path.rsplit("/", 1)[-1].lower()
                    if re.match(r"^[0-9a-f]{8}_.+$", filename, re.IGNORECASE):
                        return match.group(0)
                    if filename in name_to_served:
                        return f"{prefix}{name_to_served[filename]}{quote}"
                    return match.group(0)

                fixed_html = re.sub(
                    r"""(src\s*=\s*["'])([^"']+\.(?:png|jpg|jpeg|gif|svg|webp))(["'])""",
                    _fix_img_src,
                    fixed_html,
                    flags=re.IGNORECASE,
                )
        except Exception:
            pass

        # Auto-append missing images
        try:
            gen_images = react_state.get("generated_images", [])
            if gen_images:
                html_img_stems = set(
                    re.sub(r"^[0-9a-f]+_", "", os.path.basename(src))
                    for src in re.findall(
                        r'<img[^>]+src=["\']([^"\']+)["\']', fixed_html, re.IGNORECASE
                    )
                )

                def _img_stem(url):
                    return re.sub(r"^[0-9a-f]+_", "", os.path.basename(url))

                missing = [
                    url
                    for url in gen_images
                    if url not in fixed_html and _img_stem(url) not in html_img_stems
                ]
                if missing:
                    imgs_html = "".join(
                        f'<div style="margin:16px 0">'
                        f'<img src="{url}" '
                        f'style="max-width:100%;height:auto;border-radius:8px">'
                        f"</div>"
                        for url in missing
                    )
                    section = (
                        '<div style="margin-top:32px"><h2>📊 分析图表</h2>'
                        f"{imgs_html}</div>"
                    )
                    if "</body>" in fixed_html.lower():
                        fixed_html = re.sub(
                            r"(</body>)",
                            section + r"\1",
                            fixed_html,
                            count=1,
                            flags=re.IGNORECASE,
                        )
                    else:
                        fixed_html += section
        except Exception:
            pass

        chunks: List[Dict[str, Any]] = [
            {"output_type": "html", "content": fixed_html, "title": title},
        ]
        content = json.dumps({"chunks": chunks}, ensure_ascii=False)
        if dashboard_report is not None:
            react_state["brief_dashboard_delivery"] = (
                dashboard_report.get("brief_delivery") is True
            )
            return ActionOutput(
                is_exe_success=True,
                content=content,
                observations=f"HTML 报告《{title}》已生成、校验并展示在报告面板。",
            )
        return content

    return html_interpreter
