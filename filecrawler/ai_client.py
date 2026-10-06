"""Hook points for check modes 2 and 3 (AI).

Nothing here calls out yet: every method raises AINotConfiguredError until the
company API is wired in, and the checker then falls back to presence-only
results with a note saying the AI step was skipped.

To integrate:
  1. Fill in _post() (or swap in your company's SDK).
  2. Uncomment the request/response handling in the three public methods and
     map the response fields onto AIVerdict / a list of bullet strings.
  3. Make `configured` return True once base_url/api_key are set.
Configuration is read from the FILECRAWLER_AI_URL / FILECRAWLER_AI_KEY
environment variables so no credentials end up in the repo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .models import CheckReport, Requirement


class AIClientError(Exception):
    """An AI call failed; the message is shown against the affected document."""


class AINotConfiguredError(AIClientError):
    pass


@dataclass
class AIVerdict:
    complete: bool                   # True = in working order, False = needs changes
    findings: List[str] = field(default_factory=list)


# Suggested prompts - adjust to whatever your internal API expects.
COMPLETENESS_PROMPT = (
    "You are checking a contractor deliverable: '{code} {title}'. "
    "List every section, field or table in the document that is required but has been "
    "left blank, contains placeholder text (e.g. 'TBC', 'XXX', 'Lorem ipsum') or still "
    "shows template instructions. Reply as JSON: "
    '{{"complete": true|false, "blank_sections": ["..."]}}'
)

REVIEW_PROMPT = (
    "You are reviewing a contractor deliverable: '{code} {title}' for project phase '{phase}'. "
    "Check that it is complete, internally consistent, matches its title, is dated and "
    "signed where expected, and has no obvious errors. Reply as JSON: "
    '{{"acceptable": true|false, "issues": ["..."]}}'
)

SUMMARY_PROMPT = (
    "Below are the results of checking a contractor's document submission for phase "
    "'{phase}'. Write 3-6 short bullet points for a project manager: overall readiness, "
    "the most important gaps, and any patterns (e.g. one category consistently weak).\n\n{results}"
)


class AIClient:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: int = 120):
        self.base_url = base_url or os.environ.get("FILECRAWLER_AI_URL", "")
        self.api_key = api_key or os.environ.get("FILECRAWLER_AI_KEY", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        # TODO: return bool(self.base_url and self.api_key) once the calls below are live.
        return False

    def check_completeness(self, file_path: Path, requirement: "Requirement") -> AIVerdict:
        """Mode 2: are all required parts of this document filled in?"""
        # prompt = COMPLETENESS_PROMPT.format(code=requirement.code, title=requirement.title)
        # response = self._post("/document/check", {
        #     "prompt": prompt,
        #     "file_name": file_path.name,
        #     "file_base64": _encode_file(file_path),
        # })
        # return AIVerdict(
        #     complete=bool(response["complete"]),
        #     findings=[f"Blank: {s}" for s in response.get("blank_sections", [])],
        # )
        raise AINotConfiguredError("AI integration not configured")

    def review_content(self, file_path: Path, requirement: "Requirement", phase: str = "") -> AIVerdict:
        """Mode 3: in-depth review of the document's content."""
        # prompt = REVIEW_PROMPT.format(code=requirement.code, title=requirement.title, phase=phase)
        # response = self._post("/document/review", {
        #     "prompt": prompt,
        #     "file_name": file_path.name,
        #     "file_base64": _encode_file(file_path),
        # })
        # return AIVerdict(complete=bool(response["acceptable"]), findings=list(response.get("issues", [])))
        raise AINotConfiguredError("AI integration not configured")

    def summarize(self, report: "CheckReport") -> List[str]:
        """Bullet-point evaluation of the whole submission."""
        # lines = [
        #     f"{r.requirement.code} {r.requirement.title}: {r.status.label}"
        #     + (f" - {'; '.join(r.ai_findings)}" if r.ai_findings else "")
        #     for r in report.required_results
        # ]
        # prompt = SUMMARY_PROMPT.format(phase=report.phase, results="\n".join(lines))
        # response = self._post("/chat", {"prompt": prompt})
        # return [line.lstrip("-* ").strip() for line in response["text"].splitlines() if line.strip()]
        raise AINotConfiguredError("AI integration not configured")

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # import json
        # import urllib.error
        # import urllib.request
        #
        # request = urllib.request.Request(
        #     self.base_url.rstrip("/") + endpoint,
        #     data=json.dumps(payload).encode("utf-8"),
        #     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        #     method="POST",
        # )
        # try:
        #     with urllib.request.urlopen(request, timeout=self.timeout) as response:
        #         return json.loads(response.read().decode("utf-8"))
        # except (urllib.error.URLError, ValueError) as exc:
        #     raise AIClientError(str(exc)) from exc
        raise AINotConfiguredError("AI integration not configured")


# def _encode_file(path: Path) -> str:
#     import base64
#     return base64.b64encode(path.read_bytes()).decode("ascii")
