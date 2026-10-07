Architecture
============

The system in the `C4 model <https://c4model.com>`_ (drawn with `LikeC4 <https://likec4.dev>`_): context, containers and
components, then how it is deployed, how one ``ingest`` run flows, and what the
cache holds. The code level is the :doc:`api`. Requirement ids (``REQ_…``) link
to :doc:`requirements`.

Two properties shape everything below. The client is **read-only against
trackiwi** (:need:`REQ_READONLY`; the only state-changing request is
``DELETE /api/v2/session``), and it has **no runtime dependencies**: standard
library only.

1. System context
-----------------

Who and what the client talks to. The vendor's cloud holds the account and the
position history; the client pulls from it and, optionally, mirrors into an
InfluxDB the owner runs.

.. likec4-view:: context
   :height: 480px
   :title: context

The API is undocumented and can change without notice, so the contract is
re-checked against the live service by an opt-in test
(``TRACKIWI_LIVE=1``, see ``AGENTS.md``).

2. Containers
-------------

What runs. One Python program does all the work; a scheduler only calls it.
State lives in two small files, both created owner-only
(:need:`REQ_CACHE_MODE_0600`, :need:`REQ_CONFIG_MODE_0600`).

.. likec4-view:: containers
   :height: 560px
   :title: containers

The cache is the buffer between the two remote systems: ``sync`` fills it,
``push`` drains it, and either side can be down without losing a position
(:need:`REQ_SYNC_RESUME`, :need:`REQ_MIRROR_RESUME`).

3. Components
-------------

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

4. One ``ingest`` run
---------------------

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

5. Deployment
-------------

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

6. The cache
------------

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
