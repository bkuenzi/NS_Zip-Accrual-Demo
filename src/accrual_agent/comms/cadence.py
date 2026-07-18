"""Reminder cadence: which outreach stage is due on which close day.

Modeled on FloQast ReMind-style escalating follow-ups: initial request on day
1, then reminders of increasing urgency on the configured close days
(default 3 / 7 / 10). One message per line per run — a line that is behind
catches up one stage per daily cycle rather than being carpet-bombed.
"""

from __future__ import annotations

from ..models import CommStage

REMINDER_STAGES = (CommStage.DAY3, CommStage.DAY7, CommStage.DAY10)


class Cadence:
    def __init__(self, reminder_days: list[int], final_close_day: int):
        if len(reminder_days) != len(REMINDER_STAGES):
            raise ValueError(
                f"reminder cadence needs exactly {len(REMINDER_STAGES)} days "
                f"(got {reminder_days}); templates map to day3/day7/day10 slots"
            )
        self.reminder_days = sorted(reminder_days)
        self.final_close_day = final_close_day

    def due_stages(self, close_day: int) -> list[CommStage]:
        """All stages due by this close day, in send order."""
        stages: list[CommStage] = []
        if close_day >= 1:
            stages.append(CommStage.INITIAL)
        for stage, day in zip(REMINDER_STAGES, self.reminder_days, strict=True):
            if close_day >= day:
                stages.append(stage)
        return stages

    def next_unsent_stage(self, close_day: int, sent: set[str]) -> CommStage | None:
        for stage in self.due_stages(close_day):
            if stage.value not in sent:
                return stage
        return None

    def ladder_exhausted(self, close_day: int, sent: set[str]) -> bool:
        """Final reminder sent (or its day passed) with the ladder complete."""
        final_stage = REMINDER_STAGES[-1]
        return final_stage.value in sent and close_day >= self.reminder_days[-1]

    def is_close_risk(self, close_day: int) -> bool:
        """Unconfirmed threads get flagged as the deadline approaches."""
        return close_day >= max(self.final_close_day - 2, 1)

    @staticmethod
    def urgency(stage: CommStage) -> str:
        return {
            CommStage.INITIAL: "standard",
            CommStage.DAY3: "gentle",
            CommStage.DAY7: "firm",
            CommStage.DAY10: "final",
        }[stage]
