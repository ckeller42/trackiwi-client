Architecture
============

The system in the `C4 model <https://c4model.com>`_: context, containers and
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

.. mermaid::

   C4Context
     title System context: trackiwi-client
     Person(owner, "Vehicle owner", "Reads their own tracker data")
     System(client, "trackiwi-client", "Read-only CLI and library: syncs positions into a local cache, exports GPX, GeoJSON and CSV, mirrors to InfluxDB")
     System_Ext(cloud, "trackiwi cloud", "Vendor service and its private API; holds the account and the position history")
     System_Ext(tracker, "Vehicle GPS tracker", "Reports positions to the vendor")
     System_Ext(influx, "InfluxDB 2.x", "Time-series store the owner runs")
     System_Ext(grafana, "Grafana", "Portable Flux dashboard")
     Rel(tracker, cloud, "Reports positions")
     Rel(owner, client, "Runs commands")
     Rel(client, cloud, "Reads positions", "HTTPS, read-only")
     Rel(client, influx, "Writes trackiwi_position points", "HTTP")
     Rel(grafana, influx, "Queries", "Flux")
     Rel(owner, grafana, "Views the dashboard")

The API is undocumented and can change without notice, so the contract is
re-checked against the live service by an opt-in test
(``TRACKIWI_LIVE=1``, see ``AGENTS.md``).

2. Containers
-------------

What runs. One Python program does all the work; a scheduler only calls it.
State lives in two small files, both created owner-only
(:need:`REQ_CACHE_MODE_0600`, :need:`REQ_CONFIG_MODE_0600`).

.. mermaid::

   C4Container
     title Containers: trackiwi-client
     Person(owner, "Vehicle owner")
     System_Boundary(sys, "trackiwi-client") {
       Container(cli, "trackiwi CLI and library", "Python 3.11+, standard library only", "login, sync, export, heading, influx check and push, ingest, purge")
       ContainerDb(cache, "Position cache", "SQLite, mode 0600", "positions, mirror_state, tracker_names")
       ContainerDb(cfg, "Session and target config", "JSON and TOML, mode 0600", "config.json: session. influx.toml: InfluxDB target")
       Container(sched, "Scheduler", "systemd timer or Docker loop", "Runs trackiwi ingest on an interval")
     }
     System_Ext(cloud, "trackiwi cloud", "Private API")
     System_Ext(influx, "InfluxDB 2.x", "Bucket trackiwi")
     System_Ext(grafana, "Grafana", "Dashboard")
     Rel(owner, cli, "Runs commands")
     Rel(sched, cli, "Runs ingest")
     Rel(cli, cache, "Reads and writes", "sqlite3")
     Rel(cli, cfg, "Reads", "file")
     Rel(cli, cloud, "Pulls positions", "HTTPS, bearer token")
     Rel(cli, influx, "Pushes points", "HTTP, gzip line protocol")
     Rel(grafana, influx, "Queries", "Flux")

The cache is the buffer between the two remote systems: ``sync`` fills it,
``push`` drains it, and either side can be down without losing a position
(:need:`REQ_SYNC_RESUME`, :need:`REQ_MIRROR_RESUME`).

3. Components
-------------

The Python package. ``cli`` is the only module that knows about all the others;
the rest are small and single-purpose. The arrows are the real imports, and a
test (``tests/test_architecture_doc.py``) fails if this diagram stops matching
them.

.. mermaid::

   C4Component
     title Components: the trackiwi package
     Container_Boundary(pkg, "trackiwi package") {
       Component(cli, "cli", "argparse", "Commands and exit codes; the only orchestrator")
       Component(client, "client", "urllib", "trackiwi API: login, trackers, paged sync; the session file")
       Component(store, "store", "sqlite3", "Owner-only cache, mirror position, tracker names")
       Component(influx, "influx", "urllib", "InfluxDB target config, writer, resumable mirror")
       Component(lineprotocol, "lineprotocol", "pure", "Cached row to one trackiwi_position line")
       Component(export, "export", "pure", "GPX, GeoJSON, CSV; one track per tracker")
       Component(heading, "heading", "pure", "Heading estimate from the last moving fixes")
       Component(http, "_http", "urllib", "No-redirect opener, send, redact, User-Agent")
     }
     Rel(cli, client, "uses")
     Rel(cli, store, "uses")
     Rel(cli, influx, "uses")
     Rel(cli, export, "uses")
     Rel(cli, heading, "uses")
     Rel(client, http, "uses")
     Rel(influx, http, "uses")
     Rel(influx, lineprotocol, "uses")
     Rel(influx, store, "uses")
     Rel(heading, export, "uses")

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

.. mermaid::

   sequenceDiagram
     participant S as Scheduler
     participant C as cli
     participant A as client
     participant API as trackiwi cloud
     participant DB as store (SQLite)
     participant I as influx
     participant IDB as InfluxDB
     S->>C: trackiwi ingest
     Note over C,DB: 1. sync (resumable)
     C->>DB: max_id
     C->>A: sync(offset)
     A->>API: POST /api/v2/trackers/sync
     API-->>A: position rows
     A-->>C: parsed rows, skipped count
     C->>DB: upsert rows
     Note over C,DB: 2. tracker names
     C->>A: trackers()
     A->>API: GET /api/v2/trackers
     C->>DB: store names, fallback for unnamed
     Note over C,IDB: 3. push (idempotent, resumable)
     C->>I: mirror(store)
     I->>DB: mirror_position, rows_after
     I->>IDB: gzip line protocol, one batch
     IDB-->>I: 2xx acknowledged
     I->>DB: set_mirror_position(last id)

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

.. mermaid::

   C4Deployment
     title Deployment: Docker Compose stack
     Deployment_Node(host, "Your machine", "Docker") {
       Deployment_Node(net, "Compose network", "ports bound to 127.0.0.1") {
         Container(ingest, "ingest", "python:3.13-slim", "Loops: trackiwi ingest, then sleeps")
         ContainerDb(state, "trackiwi-state", "Docker volume", "Session and position cache")
         ContainerDb(influx, "influxdb", "influxdb:2.7", "Bucket trackiwi; healthchecked")
         Container(grafana, "grafana", "Grafana 11", "Provisioned datasource and dashboard")
       }
     }
     System_Ext(cloud, "trackiwi cloud", "Private API")
     Rel(ingest, state, "Reads and writes")
     Rel(ingest, cloud, "Pulls positions", "HTTPS")
     Rel(ingest, influx, "Pushes points", "HTTP")
     Rel(grafana, influx, "Queries", "Flux")

**Option B: your own InfluxDB** (systemd user timer, ``deploy/systemd/``). The
command is installed with ``pipx``; the timer runs ``trackiwi ingest``.

.. mermaid::

   C4Deployment
     title Deployment: systemd timer with your own InfluxDB
     Deployment_Node(machine, "Your machine", "Linux, systemd") {
       Container(timer, "trackiwi-ingest.timer", "systemd user unit", "Runs the service every 10 minutes")
       Container(cli, "trackiwi", "pipx, Python 3.11+", "trackiwi ingest")
       ContainerDb(files, "Cache and config", "SQLite and files, mode 0600", "~/.local/share/trackiwi and ~/.config/trackiwi")
     }
     Deployment_Node(yours, "Your InfluxDB and Grafana", "existing") {
       ContainerDb(influx, "InfluxDB 2.x", "your instance", "Write token scoped to one bucket")
       Container(grafana, "Grafana", "10 or later", "Imported dashboard")
     }
     System_Ext(cloud, "trackiwi cloud", "Private API")
     Rel(timer, cli, "Starts")
     Rel(cli, files, "Reads and writes")
     Rel(cli, cloud, "Pulls positions", "HTTPS")
     Rel(cli, influx, "Pushes points", "HTTP")
     Rel(grafana, influx, "Queries", "Flux")

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
