Architecture
============

The architecture, structured after `arc42 <https://arc42.org>`_ and drawn in the `C4 model <https://c4model.com>`_
with `LikeC4 <https://likec4.dev>`_: goals and constraints first, then context, containers and
components, how one ``ingest`` run flows, how it is deployed and what the
cache holds, then the crosscutting concepts, decisions, quality, risks and glossary. The code level is the :doc:`api`. Requirement ids (``REQ_…``) link
to :doc:`requirements`.

Two properties shape everything below. The client is **read-only against
trackiwi** (:need:`REQ_READONLY`; the only state-changing request is
``DELETE /api/v2/session``), and it has **no runtime dependencies**: standard
library only.

Introduction and goals
----------------------

trackiwi-client pulls the positions of a personal trackiwi GPS tracker out of the vendor’s cloud
and keeps them on hardware the owner controls: a local SQLite cache, exports as GPX, GeoJSON or
CSV, and optionally an InfluxDB bucket that Grafana draws. It is unofficial and unaffiliated.

.. list-table::
   :widths: 36 36
   :header-rows: 1

   -

      - Goal
      - What it means
   -

      - Read-only
      - Against the trackiwi API, the only request that changes state is the logout ``DELETE /api/v2/session``. :need:`REQ_READONLY`
   -

      - Private by construction
      - The token and the cache are the most sensitive things it handles: owner-only files, no token in output, no private data in the repository. :need:`REQ_TOKEN_NEVER_LOGGED`, :need:`REQ_CACHE_MODE_0600`, :need:`REQ_NO_PRIVATE_DATA`
   -

      - Resumable
      - Any interruption is repaired by running the same command again. :need:`REQ_SYNC_RESUME`, :need:`REQ_MIRROR_RESUME`
   -

      - Nothing to install
      - The package imports only the standard library. :need:`REQ_ZERO_DEPS`
   -

      - Honest data
      - A row that cannot be trusted is skipped and counted, never stored or guessed. :need:`REQ_MALFORMED_SKIP`

Constraints
-----------

-  There is no published trackiwi API. The client talks to the private API the vendor’s app uses,
   which can change without notice. Behaviour was verified against a live account; the opt-in
   live contract test (``TRACKIWI_LIVE=1``) is the way to re-check it.
-  Python 3.11 or newer, standard library only at runtime. Documentation and test tooling are
   development dependencies only.
-  The API base is never hardcoded: it comes from the login response and must be ``https://``.
   :need:`REQ_API_BASE_FROM_LOGIN`, :need:`REQ_API_BASE_HTTPS`
-  No deployment specifics in this repository (no real hostnames, IPs, credentials, tracker ids
   or coordinates). :need:`REQ_PORTABLE_CONFIG`, :need:`REQ_NO_PRIVATE_DATA`
-  The token is stored in plaintext in the config file, mode ``0600``. There is no keychain
   integration yet (see "Risks and technical debt").

Solution strategy
-----------------

.. list-table::
   :widths: 36 36
   :header-rows: 1

   -

      - Problem
      - Approach
   -

      - The API can change or lie
      - Parse defensively, skip and count bad rows, fail loudly on a page that makes no progress. :need:`REQ_SYNC_FAIL_LOUD`, :need:`REQ_SYNC_PAST_MALFORMED`
   -

      - Runs get interrupted
      - Offset-based sync from the highest stored id; per-target mirror position stored after each acknowledged batch. No retry logic: re-running is the retry.
   -

      - The token must not leak
      - One redaction helper for every message built from server text; tokens only via stdin; owner-only files. :need:`REQ_TOKEN_NEVER_LOGGED`
   -

      - Redirects can carry the token away
      - A shared opener refuses every redirect, and a 3xx is an error. :need:`REQ_NO_REDIRECTS`
   -

      - It must not change the vendor account
      - No write code exists beyond logout; the app-command response header is ignored. :need:`REQ_READONLY`, :need:`REQ_IGNORE_APP_COMMAND`
   -

      - Foreign exceptions look like bugs
      - Converted to ``TrackiwiError`` at each module boundary, so ``main()`` catches only the project’s exceptions.
   -

      - Requirements must stay true
      - Each requirement is a ``sphinx-needs`` object traced to a test; the docs build fails under ``-W`` if one is unverified.

System context
--------------

Who and what the client talks to. The vendor's cloud holds the account and the
position history; the client pulls from it and, optionally, mirrors into an
InfluxDB the owner runs.

.. likec4-view:: context
   :height: 480px
   :title: context

The API is undocumented and can change without notice, so the contract is
re-checked against the live service by an opt-in test
(``TRACKIWI_LIVE=1``, see ``AGENTS.md``).

Containers
----------

What runs. One Python program does all the work; a scheduler only calls it.
State lives in two small files, both created owner-only
(:need:`REQ_CACHE_MODE_0600`, :need:`REQ_CONFIG_MODE_0600`).

.. likec4-view:: containers
   :height: 560px
   :title: containers

The cache is the buffer between the two remote systems: ``sync`` fills it,
``push`` drains it, and either side can be down without losing a position
(:need:`REQ_SYNC_RESUME`, :need:`REQ_MIRROR_RESUME`).

Components
----------

The Python package. ``cli`` is the only module that knows about all the others;
the rest are small and single-purpose. The arrows are the real imports, and a
test (``tests/test_architecture_doc.py``) fails if this diagram stops matching
them.

.. likec4-view:: components
   :height: 700px
   :title: components

Rules the shape encodes:

* ``client`` and ``influx`` **never import each other**; ``client`` is the only
  module that talks to trackiwi and ``influx`` the only one that talks to
  InfluxDB (:need:`REQ_INFLUX_WRITE_SCOPE`). Their shared plumbing is ``_http``.
* ``_http`` refuses every redirect, so a session token can never be forwarded
  to another host (:need:`REQ_NO_REDIRECTS`), and scrubs credentials from error
  text (:need:`REQ_TOKEN_NEVER_LOGGED`).
* ``lineprotocol``, ``export`` and ``heading`` are pure: rows in, text or an
  estimate out, no I/O. ``store`` never touches the network.
* Every module may use the shared contracts in ``trackiwi/__init__.py``
  (``TrackiwiError``, ``AuthError``, ``COLUMNS``, ``Row``, ``__version__``),
  which imports nothing. They are not drawn, to keep the diagram readable.
* ``influx`` only ever writes the ``trackiwi_position`` measurement to the one
  configured target.

One ``ingest`` run
------------------

The scheduled command. Its three steps fail independently: the push still runs
when the sync or the name refresh fails, because the cache already holds
positions that should reach InfluxDB.

.. likec4-view:: ingest
   :mode: sequence
   :height: 640px
   :title: ingest

The mirror position moves **only after InfluxDB acknowledges a batch**
(:need:`REQ_MIRROR_RESUME`), and a row always becomes the same point, so sending
it twice overwrites rather than duplicates (:need:`REQ_MIRROR_IDEMPOTENT`).
``sync`` resumes from the highest stored id and treats the server's offset as
exclusive (:need:`REQ_SYNC_OFFSET_EXCLUSIVE`); a malformed row is skipped and
counted, never fatal (:need:`REQ_MALFORMED_SKIP`).

Login and sync
--------------

The two commands that talk to the trackiwi cloud, drawn as plain sequences. The scheduled run is the
``ingest`` sequence above.

Login
~~~~~

.. mermaid::

   sequenceDiagram
       actor Owner
       participant CLI as CLI
       participant Client as Client
       participant Cloud as trackiwi cloud
       participant Disk as config file 0600

       Owner->>CLI: trackiwi login
       CLI->>Owner: prompt for email and password
       CLI->>Client: login with email and password
       Client->>Cloud: POST login on the trackiwi website
       Cloud-->>Client: server, token, user
       Client->>Client: require https on server
       Client->>Disk: save api_base, token, user_id
       Client-->>CLI: user
       CLI-->>Owner: Logged in


The login request goes to the vendor’s website; the API base for everything after it is the
``server`` field of the response, checked for ``https://`` before it is stored. The password is
never stored. A response that lacks a field is an error that does not echo the body, which could
contain the token. :need:`REQ_API_BASE_FROM_LOGIN`, :need:`REQ_API_BASE_HTTPS`,
:need:`REQ_CONFIG_MODE_0600`

Sync (offset-based, resumable)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. mermaid::

   sequenceDiagram
       actor Owner
       participant CLI as CLI
       participant Store as Store
       participant Client as Client
       participant Cloud as trackiwi cloud

       Owner->>CLI: trackiwi sync
       CLI->>Store: max_id
       Store-->>CLI: highest stored id, or none
       loop until a page has no rows
           CLI->>Client: sync from offset
           Client->>Cloud: POST trackers sync with offset, or initial_sync
           Cloud-->>Client: CSV page
           Client->>Client: parse rows, skip and count malformed
           Client-->>CLI: rows, skipped, total
           CLI->>Store: upsert rows
           Note over Client: next offset is the highest id on the page
       end
       CLI-->>Owner: N new positions, M cached in total


The offset is exclusive: a page requested at offset N holds ids greater than N. The loop stops
when a page holds no rows at all. It fails loudly instead of looping if a page has skipped rows
but not one readable id, or if the highest id does not pass the offset just requested. A
malformed row, even the newest one, is skipped and counted and the offset moves past it. Each
page is written before the next is requested, so an interruption loses nothing and the next run
starts from ``max_id``. ``sync --full`` ignores the stored id and sends ``initial_sync``.
:need:`REQ_SYNC_RESUME`, :need:`REQ_SYNC_OFFSET_EXCLUSIVE`, :need:`REQ_SYNC_FAIL_LOUD`,
:need:`REQ_SYNC_PAST_MALFORMED`

Deployment
----------

Two supported setups, both generic. A real deployment is configured from its own
repository, which consumes this package (:need:`REQ_PORTABLE_CONFIG`).

**Option A: turnkey Docker Compose stack** (``deploy/docker-compose.yml``).
Published ports bind to ``127.0.0.1``; the ingest container reaches InfluxDB
over the compose network and starts only once InfluxDB is healthy.

.. likec4-view:: deploy-compose
   :height: 420px
   :title: deploy-compose

**Option B: your own InfluxDB** (systemd user timer, ``deploy/systemd/``). The
command is installed with ``pipx``; the timer runs ``trackiwi ingest``.

.. likec4-view:: deploy-systemd
   :height: 520px
   :title: deploy-systemd

The cache
---------

Three tables in one SQLite file. There are no foreign keys: ``tracker_names``
and ``mirror_state`` refer to positions by value only. Position ids are the
vendor's own, so they are the resume cursor for both ``sync`` and the mirror.

.. mermaid::

   erDiagram
     positions {
       int id PK
       int tracker_id
       int fix_at "epoch seconds"
       int fix_timezone
       real latitude
       real longitude
       int altitude "m"
       real speed "km/h"
       int course "degrees"
       int distance "cm per fix"
       int rssi "GNSS quality 0-5"
       int sat
       int battery "percent"
       int voltage "centivolts"
     }
     tracker_names {
       int tracker_id PK
       text name
     }
     mirror_state {
       text target PK
       int last_id "highest id acknowledged"
     }
     tracker_names ||--o{ positions : "names tracker_id"
     mirror_state }o..o| positions : "last_id marks a position"

The cache is a complete movement history of a physical vehicle, so it is the
most sensitive file this tool creates. ``purge`` deletes it and SQLite's
sidecar files without opening it (:need:`REQ_PURGE_DELETES`).

Crosscutting concepts
---------------------

-  **Errors and exit codes.** ``TrackiwiError`` for any failure talking to trackiwi, ``AuthError``
   for a rejected or missing session (exit 2). Transport failures, including a timeout while
   reading a response, become ``TrackiwiError`` in ``_http.send``, never a raw ``OSError``.
-  **Secrets.** The token is never logged or printed; error bodies pass through ``_http.redact``
   and ``Client._api`` scrubs the token from any error beneath it. :need:`REQ_TOKEN_NEVER_LOGGED`,
   :need:`REQ_INFLUX_TOKEN_REDACT`. A stored token must always be removable, even from a config
   ``load`` refuses. :need:`REQ_TOKEN_REMOVABLE`
-  **File modes.** Config, cache and exports are created at their final mode (``0600``, in ``0700``
   directories) and a widened mode is narrowed on load. :need:`REQ_CONFIG_MODE_0600`,
   :need:`REQ_CACHE_MODE_0600`, :need:`REQ_EXPORT_ATOMIC`
-  **Time.** Every timestamp is epoch seconds UTC after parsing; ``normalize_epoch`` takes the sync
   CSV integer, ``epoch_from_iso`` takes the ISO string of the trackers endpoint, and they are kept
   apart on purpose. :need:`REQ_FIX_AT_SECONDS`. Units of the other fields are in
   :doc:`reference/units`.
-  **Read-only commands never create the cache.** ``export``, ``heading`` and ``purge`` touch the
   database only if it exists; ``purge`` deletes without opening it. :need:`REQ_PURGE_DELETES`,
   :need:`REQ_PURGE_NOT_ON_LOCK`
-  **Traceability.** Requirements, their tests and the API are in :doc:`requirements`,
   :doc:`traceability` and :doc:`api`.

Decisions
---------

Decisions that shaped the design, with the rule that now enforces each. Dated design specs live
locally and are not part of the repository.

.. list-table::
   :widths: 24 24 24
   :header-rows: 1

   -

      - Decision
      - Reason
      - Enforced by
   -

      - Standard library only
      - Nothing to install on a small host, a small supply-chain surface for a tool that holds a location token.
      - :need:`REQ_ZERO_DEPS`
   -

      - Read-only client
      - A bug must not be able to disarm an alarm, publish a share or edit a tour. ``test_alarm`` and the trailing-slash item routes are never called.
      - :need:`REQ_READONLY`
   -

      - API base from the login response, https only
      - The server decides where the API lives, and a plain-HTTP base would send the bearer token in cleartext.
      - :need:`REQ_API_BASE_FROM_LOGIN`, :need:`REQ_API_BASE_HTTPS`
   -

      - No redirects
      - urllib would copy the token onto the redirected request, and a login page would read as a successful empty answer.
      - :need:`REQ_NO_REDIRECTS`
   -

      - Ignore the ``trackiwi-app-command`` header
      - The official app executes it; this client never acts on server instructions.
      - :need:`REQ_IGNORE_APP_COMMAND`
   -

      - Offset sync without retry logic
      - The offset makes a re-run the retry; a loop would hide failures.
      - :need:`REQ_SYNC_RESUME`
   -

      - The cache is the buffer for the mirror
      - InfluxDB or the network can be down; positions wait in SQLite.
      - :need:`REQ_MIRROR_RESUME`
   -

      - Scoped ingest token, loopback ports
      - The container never sees the operator token; nothing is exposed by accident.
      - :need:`REQ_DEPLOY_LEAST_EXPOSURE`
   -

      - Parked heading is an inference
      - The feed has no compass; the estimate says how far to trust it.
      - :need:`REQ_HEADING_ESTIMATE`, :need:`REQ_HEADING_STATE`

Quality
-------

.. list-table::
   :widths: 24 24 24
   :header-rows: 1

   -

      - Quality
      - Scenario
      - How it is checked
   -

      - Safety
      - No code path other than logout changes state at trackiwi.
      - :need:`REQ_READONLY`, an invariant test in ``tests/test_invariants.py``
   -

      - Privacy
      - A secret or position data is never committed.
      - Three scanners (gitleaks, detect-secrets, ``tools/check_no_private_data.py``) in pre-commit and CI
   -

      - Reliability
      - Kill a sync or a push at any point, run it again, end with the same state.
      - :need:`REQ_SYNC_RESUME`, :need:`REQ_MIRROR_RESUME`
   -

      - Maintainability
      - Strict typing, full docstring coverage, 95 percent test coverage.
      - ``mypy --strict``, ``interrogate``, ``pytest --cov-fail-under=95`` in ``tools/ci.sh`` and CI
   -

      - Documentation truth
      - A requirement without a test, or a dangling ``:need:`` reference, fails the build.
      - ``sphinx-build -b html -W docs docs/_build/html`` in CI

Risks and technical debt
------------------------

-  **Unofficial API.** It can change without notice. ``sync`` fails loudly on an unreadable or
   non-advancing page, and the live contract test (``TRACKIWI_LIVE=1``, never in CI) is the
   early-warning check.
-  **Unverified shapes.** The ``markers`` and ``shares`` endpoints answered with empty lists on the
   account they were checked against, so their field names are expectations. ``fix_timezone`` is an
   integer of unconfirmed meaning (nothing depends on it).
-  **Plaintext token.** The session token is readable by any process of the same user and is swept
   into backups. Moving it into the macOS Keychain is the recommended upgrade and is not done.
-  **Location in API responses.** The ``trackers`` and ``alarms`` responses hold geofence and event
   coordinates. The commands print none, but the responses themselves deserve the care of the cache.
-  **Heading is an estimate.** A vehicle that reversed into its spot points the other way and
   nothing in the data can tell.

Glossary
--------

.. list-table::
   :widths: 36 36
   :header-rows: 1

   -

      - Term
      - Meaning
   -

      - Tracker
      - A GPS device on the trackiwi account, identified by an integer id.
   -

      - Position, fix
      - One reported location row of a tracker, with a server id and a ``fix_at`` time.
   -

      - Offset
      - The id after which the next sync page starts; exclusive.
   -

      - Cache
      - The local SQLite database of positions.
   -

      - Mirror
      - The step that sends cached positions to InfluxDB.
   -

      - Mirror position
      - The highest id acknowledged by one InfluxDB target, stored per target in ``mirror_state``.
   -

      - Ingest
      - ``sync``, refresh tracker names, then mirror: what a timer runs.
   -

      - Requirement
      - A ``sphinx-needs`` object with a ``REQ_`` id, traced to its verifying tests.
