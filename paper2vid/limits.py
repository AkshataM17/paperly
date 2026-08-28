"""Free-tier limits and the waitlist.

Two separate protections, because they fail differently.

Per-visitor quota stops one person building fifty papers. Global budget stops
ten thousand people building one each. A launch that goes better than expected
should stop spending, not empty the account -- so the budget is a hard stop,
checked before every paid call, not an alert after the fact.

State is a JSON file rather than a database. It is a counter, it is small, and
a free-tier host that loses it on redeploy resets the free tier -- which is an
acceptable failure for something this cheap to rebuild.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

# What one visitor gets before the waitlist appears.
FREE_BUILDS = 1
FREE_ASKS = 1

# Rough per-call cost, used only to decide when to stop. Measured against
# Sonnet for the storyboard and Haiku for markers and pre-baked answers; adjust
# if you change models. Deliberately rounded UP -- overestimating stops you
# early, underestimating overspends.
COST_BUILD = 0.12
COST_ASK = 0.02

_LOCK = threading.Lock()


class Limits:
    def __init__(self, path: str, budget_usd: float):
        self.path = path
        self.budget = budget_usd
        self.state = {"visitors": {}, "spent": 0.0,
                      "builds": 0, "asks": 0, "waitlist": []}
        self._load()

    # --- persistence -----------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                self.state.update(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self.state, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # --- checks ----------------------------------------------------------

    def new_visitor(self) -> str:
        return uuid.uuid4().hex[:16]

    def _v(self, vid: str) -> dict:
        return self.state["visitors"].setdefault(vid, {"builds": 0, "asks": 0})

    def check(self, vid: str, kind: str) -> tuple[bool, str]:
        """Return (allowed, reason). Reason is shown to the visitor."""
        cost = COST_BUILD if kind == "build" else COST_ASK
        with _LOCK:
            if self.budget and self.state["spent"] + cost > self.budget:
                return False, "budget"
            v = self._v(vid)
            cap = FREE_BUILDS if kind == "build" else FREE_ASKS
            if v[kind + "s"] >= cap:
                return False, "quota"
        return True, ""

    def charge(self, vid: str, kind: str) -> None:
        """Record a call that actually happened."""
        with _LOCK:
            v = self._v(vid)
            v[kind + "s"] += 1
            self.state[kind + "s"] += 1
            self.state["spent"] += COST_BUILD if kind == "build" else COST_ASK
            self._save()

    def remaining(self, vid: str) -> dict:
        with _LOCK:
            v = self._v(vid)
            return {
                "builds": max(0, FREE_BUILDS - v["builds"]),
                "asks": max(0, FREE_ASKS - v["asks"]),
                "budget_left": (round(max(0.0, self.budget - self.state["spent"]), 2)
                                if self.budget else None),
            }

    # --- waitlist --------------------------------------------------------

    def _forward(self, email: str, note: str) -> None:
        """Copy the signup somewhere that survives a redeploy.

        The local file is the source of truth, but a free-tier host wipes it
        on every deploy -- and losing the waitlist defeats the point of
        collecting it. Google Forms accepts a plain POST and gives a Sheet
        that persists. Fire-and-forget: a slow Google must not make the
        visitor wait, and a failed forward must not lose the local record.
        """
        form = os.environ.get("PAPER2VID_FORM_ID")
        field = os.environ.get("PAPER2VID_FORM_FIELD")
        if not (form and field):
            return

        def go():
            try:
                import requests
                requests.post(
                    f"https://docs.google.com/forms/d/e/{form}/formResponse",
                    data={field: email}, timeout=10)
            except Exception:
                pass                      # the local copy is already written

        threading.Thread(target=go, daemon=True).start()

    def join(self, email: str, note: str = "") -> bool:
        email = (email or "").strip()[:200]
        if "@" not in email or len(email) < 5:
            return False
        with _LOCK:
            if any(w["email"] == email for w in self.state["waitlist"]):
                return True                       # already in, not an error
            self.state["waitlist"].append(
                {"email": email, "note": note[:300], "at": int(time.time())})
            self._save()
        self._forward(email, note)
        return True

    def stats(self) -> dict:
        with _LOCK:
            return {"builds": self.state["builds"], "asks": self.state["asks"],
                    "spent": round(self.state["spent"], 2),
                    "budget": self.budget,
                    "waitlist": len(self.state["waitlist"]),
                    "visitors": len(self.state["visitors"])}