"""Planning failures shared by package and standalone CLI import paths."""


class OutlinePlanningError(ValueError):
    """A saved planning candidate needs attention, not a whole-stage replay."""


class InvalidPlanningJSON(OutlinePlanningError):
    """The returned bytes do not decode as a complete JSON document."""


class PlanningOutputIncomplete(OutlinePlanningError):
    """Neither generation nor its one correction produced parseable JSON."""


class PlanningInputTooLarge(OutlinePlanningError):
    def __init__(self, name: str, size: int, limit: int):
        self.stage_name = name
        super().__init__(
            f"{name} exceeds the negotiated input capacity ({size} > {limit} bytes); "
            "the complete response and prior checkpoints are preserved; no content was truncated or sent"
        )
