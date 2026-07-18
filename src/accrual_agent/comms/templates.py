"""Jinja2 template loading + rendering for vendor emails and reports.

Templates live in config/templates/*.j2 so accounting can edit tone and
wording without touching Python. Required variables are validated at load so a
broken edit fails at startup, not mid-send. The first line of each email
template must be `Subject: ...`.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, meta

from ..config import CONFIG_DIR
from ..models import CommStage

TEMPLATE_DIR = CONFIG_DIR / "templates"

STAGE_TEMPLATES: dict[CommStage, str] = {
    CommStage.INITIAL: "initial_request.j2",
    CommStage.DAY3: "reminder_day3.j2",
    CommStage.DAY7: "reminder_day7.j2",
    CommStage.DAY10: "reminder_day10.j2",
}

REQUIRED_VARS: dict[str, set[str]] = {
    "initial_request.j2": {"vendor_name", "amount", "currency", "ref_token", "period"},
    "reminder_day3.j2": {"vendor_name", "amount", "ref_token"},
    "reminder_day7.j2": {"vendor_name", "amount", "ref_token"},
    "reminder_day10.j2": {"vendor_name", "amount", "ref_token"},
    "escalation.j2": {"reason_label", "vendor_name", "line_id"},
    "checkpoint_report.j2": {"report_body", "close_day"},
}


class TemplateError(RuntimeError):
    pass


class TemplateEngine:
    def __init__(self, template_dir: Path | None = None):
        self.template_dir = template_dir or TEMPLATE_DIR
        self.env = Environment(
            loader=FileSystemLoader(self.template_dir),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        self.validate()

    def validate(self) -> None:
        for name, required in REQUIRED_VARS.items():
            path = self.template_dir / name
            if not path.exists():
                raise TemplateError(f"missing template {name} in {self.template_dir}")
            declared = meta.find_undeclared_variables(
                self.env.parse(path.read_text())
            )
            missing = required - declared
            if missing:
                raise TemplateError(
                    f"template {name} no longer references required variables: "
                    f"{sorted(missing)}"
                )

    def render(self, template_name: str, **context: object) -> str:
        return self.env.get_template(template_name).render(**context)

    def render_email(self, template_name: str, **context: object) -> tuple[str, str]:
        """Split the rendered template into (subject, body)."""
        rendered = self.render(template_name, **context)
        first_line, _, rest = rendered.partition("\n")
        if not first_line.startswith("Subject:"):
            raise TemplateError(
                f"template {template_name} must start with a 'Subject:' line"
            )
        return first_line.removeprefix("Subject:").strip(), rest.lstrip("\n")

    def render_stage_email(self, stage: CommStage, **context: object) -> tuple[str, str]:
        return self.render_email(STAGE_TEMPLATES[stage], **context)
