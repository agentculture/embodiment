"""The daemon's HTTP surface: the dashboard, the event stream, the controls.

Two modules, split so the half that needs no socket can be tested without one:

* :mod:`embodiment.http.guard` — pure policy. Given a method, a path and a set
  of headers, it answers whether the request may proceed. The install secret,
  the ``Host`` and ``Origin`` allow-lists, and the Cloudflare Access assertion
  seam all live here.
* :mod:`embodiment.http.server` — :class:`~embodiment.http.server.DashboardServer`:
  a stdlib ``ThreadingHTTPServer`` that serves ``web/dist``, projects the event
  bus as SSE, and exposes the control API. Loopback-bound unless the operator
  passes ``--bind-public``, and stoppable inside a deadline it reports.

``CLAUDE.md``'s constraint, which is the reason the guard exists at all:
**loopback is not authentication.** A Cloudflare tunnel delivers a remote
request from ``cloudflared`` on 127.0.0.1, so every state-changing request AND
the stream (which carries the transcript) is guarded regardless of where the
packet appears to come from.

Nothing here is imported at package-import time: ``embodiment.http`` pulls
:mod:`http.server` and a thread pool's worth of stdlib, which a host that only
wants the loop must not pay for. Import the submodule you need.
"""

from __future__ import annotations

__all__ = ["guard", "server"]
